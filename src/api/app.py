from contextlib import asynccontextmanager
import hashlib
import json
from io import BytesIO
import secrets
from pathlib import Path
import re
from typing import Literal
from uuid import UUID, uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from filelock import Timeout
from pydantic import model_validator, Field
from src.config.settings import Settings
from src.orchestrator.workflow import Workflow
from src.schemas.common import StrictModel
from src.schemas.icp import load_icp
from src.schemas.review import ReviewRequest, ReviewEvent, ProfessionalReviewRequest, PublicationRequest, OutcomeRequest, EditorialEditRequest
from src.schemas.decisions import SelectionRequest, SelectionEvent
from src.services.seo_decisions import validate_selection, verified_topic_links
from src.schemas.jobs import Job, JobKind, view as job_view
from src.orchestrator.state import RunState
from src.security.auth import AuthService, PendingAccount, BootstrapAccountConflict, digest, hash_password
from src.security.run_access import RunAccess, AccessRevoked, authorize_job
from src.services.jobs import JobWorker
from src.schemas.assets import Platform
from src.services.social import SocialService
from src.services.images import ImageService, ImageProcessingError, ImageGenerationError, image_generation_options
from src.services.image_sets import ImageSetService, ImageMasterError
from src.services.model_routes import ModelConfigurationError
from src.services.image_processing import MEDIA_TYPES, image_file_path
from src.services.render import render_topic_map
from src.storage.postgres_storage import RunBusy
from src.storage.sqlite_storage import BudgetExceeded
from src.services.user_errors import http_guidance
from src.services.readiness import readiness
from src.services.visibility import Observation

class StartRequest(StrictModel):
    icp_id: str = 'icp_1_buy_and_hold'
    seed_topic: str | None = Field(None, min_length=3, max_length=120)

class AssetDecision(StrictModel):
    action: Literal['approved', 'rejected']

class ImageRequest(StrictModel):
    platform: Platform
    png_requested: bool = False

    @model_validator(mode='after')
    def valid_png_platform(self):
        if self.png_requested and self.platform != 'google_business_profile':
            raise ValueError('PNG selection is only available for Google Business Profile')
        return self

class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1,max_length=128)

class RegistrationRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    password_confirmation: str = Field(min_length=12, max_length=128)

    @model_validator(mode='after')
    def matching_passwords(self):
        if self.password != self.password_confirmation:
            raise ValueError('Passwords do not match')
        return self

class BootstrapRequest(RegistrationRequest):
    setup_code: str = Field(min_length=1, max_length=256)

class SharingRequest(StrictModel):
    user_id: UUID
    enabled: bool = True

class OwnerRequest(StrictModel):
    user_id: UUID
    admin_password: str = Field(min_length=1, max_length=128)
    admin_code: str = Field(min_length=6, max_length=6)

class ChallengeRequest(StrictModel):
    challenge: str = Field(min_length=32,max_length=256)
    code: str = Field(min_length=6,max_length=64)

class ChallengeOnly(StrictModel):
    challenge: str = Field(min_length=32,max_length=256)

class PasswordSetupRequest(StrictModel):
    challenge: str = Field(min_length=32,max_length=256)
    password: str = Field(min_length=12,max_length=128)

class UserRequest(StrictModel):
    email: str = Field(min_length=3,max_length=254)
    name: str = Field(min_length=1,max_length=100)
    role: Literal['member','reviewer','tax_adviser'] = 'reviewer'
    admin_password: str = Field(min_length=1,max_length=128)
    admin_code: str = Field(min_length=6,max_length=6)

class AdminAction(StrictModel):
    admin_password: str = Field(min_length=1,max_length=128)
    admin_code: str = Field(min_length=6,max_length=6)

class QueueJobRequest(StrictModel):
    kind: JobKind
    idempotency_key: UUID
    icp_id: str | None = None
    seed_topic: str | None = Field(None, min_length=3, max_length=120)
    run_id: UUID | None = None
    review: ReviewRequest | None = None
    parent_id: UUID | None = None
    png_requested: bool = False
    selection: SelectionRequest | None = None
    expected_revision: int | None = Field(None, ge=0)
    research_budget_usd: float | None = Field(None,gt=0,le=5)
    run_budget_usd: float | None = Field(None,gt=0,le=5)
    research_scope: Literal['site','icp_1'] | None = None
    internal_link_policy: Literal['strict','starter_library'] | None = None
    evaluation_plan_id: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')
    confirm_evaluation: bool = False

def create_app(settings: Settings | None = None) -> FastAPI:
    settings=settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        with Workflow(settings) as workflow:
            app.state.workflow=workflow
            app.state.auth=AuthService(workflow.storage,settings.auth_encryption_key.get_secret_value())
            setup_code = app.state.auth.prepare_bootstrap()
            if setup_code:
                print('First administrator setup code (valid for 15 minutes, local terminal only): '+setup_code, flush=True)
                del setup_code
            worker=JobWorker(settings, workflow)
            worker.start()
            app.state.worker=worker
            try:
                yield
            finally:
                worker.close()
    app=FastAPI(title='Muster Agentic SEO',version='1.2.1',lifespan=lifespan)

    @app.middleware('http')
    async def sensitive_response_headers(request: Request, call_next):
        origin=request.headers.get('origin')
        if request.method in {'POST','PUT','PATCH','DELETE'} and origin and origin.rstrip('/') != settings.auth_public_origin.rstrip('/'):
            return JSONResponse({'detail':'Ungültiger Anfrageursprung.'},status_code=403,headers={'Cache-Control':'no-store'})
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    def failure_guidance(request, info, exc, status):
        # Only authenticated workflow requests are retained. No body, URL query or cookie is stored.
        user=getattr(request.state, 'actor', None)
        if not user or request.url.path.endswith('/diagnostics'):return info
        raw_run=getattr(request.state, 'failure_run_id', None)
        state=getattr(request.state,'failure_state',None)
        if state is None:return info
        from src.services.failures import capture, error_info
        cause=exc
        seen=set()
        while cause.__context__ is not None and id(cause) not in seen:
            seen.add(id(cause))
            cause=cause.__context__
        if getattr(cause,'_failure_boundary',None):exc=cause
        parts=request.url.path.rstrip('/').split('/')
        component=next((part for part in reversed(parts) if part in {
            'graphic','editorial','reviews','professional-reviews','publication','outcomes','bundle',
            'image-sets','images','social-posts','selection','preflight'}), 'Portal API')
        if component=='graphic' and status in {409,413,422}:
            info={**info,'code':'graphic_input'}
        event=capture(app.state.workflow.storage,code=info['code'],exc=exc,state=state,
            component=component,operation='Complete '+component.replace('-',' ')+' request',
            reference=info['reference'],http_status=status)
        result=error_info(event['summary'])
        if user['role']=='admin':result['diagnostic_report']=event
        return result

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Preserve validation locations and codes; translate human-readable messages.
        response = await request_validation_exception_handler(request, exc)
        payload = json.loads(response.body)
        for error in payload['detail']:
            error.pop('input', None)
            error.pop('ctx', None)
            error['msg'] = 'Pflichtfeld fehlt.' if error['type'] == 'missing' else 'Ungültige Eingabe. Bitte prüfen Sie das angegebene Feld.'
            if 'error' in error.get('ctx', {}):
                error['ctx']['error'] = error['msg']
        payload['user_error'] = failure_guidance(request, http_guidance(422), exc, 422)
        return JSONResponse(payload, status_code=response.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        # Framework-generated errors use the same German presentation boundary.
        detail = {'Not Found': 'Route nicht gefunden.', 'Method Not Allowed': 'Methode nicht erlaubt.', 'Bad Request': 'Ungültige Anfrage.'}.get(exc.detail, exc.detail) if isinstance(exc.detail, str) else exc.detail
        info = failure_guidance(request, http_guidance(exc.status_code, detail), exc, exc.status_code)
        if exc.status_code >= 500:
            import logging
            logging.getLogger(__name__).error('Portal request failed (%s), reference %s', info['code'], info['reference'])
        return JSONResponse({'detail': detail, 'user_error': info}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        # Server diagnostics remain English; never include raw input or provider text.
        import logging
        logging.getLogger(__name__).error('API request failed (%s); inspect configuration and run state.', type(exc).__name__)
        info = failure_guidance(request, http_guidance(500), exc, 500)
        logging.getLogger(__name__).error('Portal failure reference %s (%s)', info['reference'], type(exc).__name__)
        return JSONResponse({'detail': 'Interner Serverfehler. Bitte prüfen Sie den Laufstatus, bevor Sie die Anfrage erneut senden.', 'user_error': info}, status_code=500)

    def token(request: Request) -> dict:
        if request.headers.get('authorization'):
            raise HTTPException(401,'Bitte anmelden.')
        secret = request.cookies.get('review_session')
        csrf = request.headers.get('x-csrf-token') if request.method in {'POST','PUT','PATCH','DELETE'} else None
        if csrf is not None and csrf != request.cookies.get('review_csrf'):
            raise HTTPException(403,'Ungültige Anfrage. Bitte Seite neu laden.')
        user = app.state.auth.identify(secret,csrf)
        if not user or (request.method in {'POST','PUT','PATCH','DELETE'} and not csrf):
            raise HTTPException(401 if not user else 403,'Bitte anmelden oder Seite neu laden.')
        request.state.actor = user
        # This covers every nested run artifact and job route, including downloads.
        run_id = request.path_params.get('run_id')
        job_id = request.path_params.get('job_id')
        if job_id:
            job = app.state.workflow.storage.get_job(str(job_id))
            if not job:
                raise HTTPException(404, 'Saved item not found.')
            run_id = job.run_id
        if run_id:
            try: UUID(str(run_id))
            except ValueError: return user  # Keep FastAPI's field-level validation contract.
            state = require_access(str(run_id), user)
            request.state.failure_run_id = str(run_id)
            request.state.failure_state = state
            if request.method == 'POST' and request.url.path.endswith(('/selection','/graphic')):
                try: RunAccess(app.state.auth.store).drafting(state, user)
                except AccessRevoked: raise HTTPException(403, 'Only the owner, administrator or trusted final reviewer can perform this drafting step.') from None
        return user

    def require_access(run_id, user):
        try: normalized = UUID(str(run_id))
        except ValueError: raise HTTPException(422, 'Invalid run identifier.') from None
        state = get_state(normalized)
        try:
            RunAccess(app.state.auth.store).require(state, user)
        except AccessRevoked:
            raise HTTPException(404, 'Saved item not found.') from None
        return state

    def signed_in_response(user_id: str, session: tuple[str,str], recovery_codes: list[str] | None = None):
        secret, csrf = session
        user = app.state.auth.store.user('id',user_id)
        response = JSONResponse({'reviewer':user['name'],'role':user['role'],'user_id':user['id'],
                                 'email':user['email'],'account_status':user['registration_status'],
                                 'csrf_token':csrf,**({'recovery_codes':recovery_codes} if recovery_codes else {})})
        secure = settings.auth_public_origin.startswith('https://')
        response.set_cookie('review_session',secret,max_age=43200,httponly=True,secure=secure,samesite='strict',path='/')
        response.set_cookie('review_csrf',csrf,max_age=43200,httponly=False,secure=secure,samesite='strict',path='/')
        return response

    @app.get('/auth/bootstrap/status')
    def bootstrap_status():
        return {'setup_available': app.state.auth.store.count_admins() == 0}

    @app.post('/auth/bootstrap')
    def bootstrap(body: BootstrapRequest):
        try:
            return app.state.auth.bootstrap_from_browser(body.setup_code, body.email, body.name, body.password)
        except BootstrapAccountConflict as exc:
            messages = {
                'pending_password': 'Pending registration found. Use the same password you chose when registering, together with the current local setup code.',
                'account_unavailable': 'This email belongs to an account that cannot be used for first-administrator setup. Use a different administrator email or trusted terminal recovery.',
                'reserved_identity': 'This trusted reviewer name is already assigned. Use your own name for the administrator account.',
            }
            raise HTTPException(409, messages[exc.reason]) from None
        except PermissionError:
            raise HTTPException(400, 'Invalid or expired local setup code. Check the server terminal; restart the server for a new code. Attempts are limited.') from None
        except ValueError:
            if app.state.auth.store.count_admins():
                raise HTTPException(409, 'An administrator already exists. Return to sign in, or use trusted terminal recovery.') from None
            raise HTTPException(422, 'Check administrator details. Enter your name, a complete valid email address and matching passwords of 12–128 characters.') from None
        except Exception as exc:
            if type(exc).__name__ not in {'IntegrityError','UniqueViolation'}: raise
            raise HTTPException(409, 'These account details cannot be used.') from None

    @app.post('/auth/register', status_code=202)
    def register(body: RegistrationRequest, request: Request):
        store = app.state.auth.store
        # Proxy clients share this limit; never trust user-controlled forwarded IPs.
        key = digest('registration-origin:' + (request.client.host if request.client else 'local'))
        if store.throttled(key):
            raise HTTPException(429, 'Too many registration attempts. Wait 15 minutes before trying again.')
        store.throttle(key, True)
        try:
            app.state.auth.register(body.email, body.name, body.password)
        except ValueError:
            raise HTTPException(422, 'Use a valid email, a name other than Alex or Sam, and a password of 12–128 characters.') from None
        except PermissionError:
            raise HTTPException(429, 'Too many registration attempts. Wait 15 minutes before trying again.') from None
        except Exception as exc:
            if type(exc).__name__ not in {'IntegrityError','UniqueViolation'}: raise
            raise HTTPException(409, 'Registration could not be completed. If you already registered, sign in or ask the administrator.') from None
        return {'status':'pending', 'message':'Your account is awaiting administrator approval. After activation, sign in to set up your authenticator. No email will be sent.'}

    @app.post('/auth/login')
    def login(body: LoginRequest):
        try:
            return app.state.auth.login(body.email,body.password)
        except PendingAccount:
            raise HTTPException(403, 'Your account is awaiting administrator approval. Ask your administrator to activate it; then sign in to set up your authenticator.') from None
        except PermissionError:
            raise HTTPException(401,'Anmeldung fehlgeschlagen. Bitte Angaben prüfen.')

    @app.post('/auth/setup/password')
    def setup_password(body: PasswordSetupRequest):
        try:
            app.state.auth.change_initial_password(body.challenge,body.password)
            return {'stage':'setup_mfa'}
        except (PermissionError,ValueError):
            raise HTTPException(400,'Passwort ungültig oder Anmeldung abgelaufen.')

    @app.post('/auth/setup/mfa')
    def setup_mfa(body: ChallengeOnly):
        try: return app.state.auth.start_mfa(body.challenge)
        except PermissionError: raise HTTPException(401,'Anmeldung abgelaufen.')

    @app.post('/auth/setup/confirm')
    def confirm_mfa(body: ChallengeRequest):
        try:
            user = app.state.auth.challenge(body.challenge,'setup_mfa')
            session,codes = app.state.auth.finish_mfa_setup(body.challenge,body.code)
            return signed_in_response(user['id'],session,codes)
        except (PermissionError,ValueError): raise HTTPException(401,'Authenticator-Code ungültig oder Anmeldung abgelaufen.')

    @app.post('/auth/verify')
    def verify_mfa(body: ChallengeRequest):
        try:
            user = app.state.auth.challenge(body.challenge,'mfa')
            return signed_in_response(user['id'],app.state.auth.verify_mfa(body.challenge,body.code))
        except PermissionError: raise HTTPException(401,'Authenticator-Code ungültig oder Anmeldung abgelaufen.')

    @app.get('/session')
    def session(request: Request, user: dict=Depends(token)):
        csrf=request.cookies.get('review_csrf')
        if not csrf or not app.state.auth.identify(request.cookies.get('review_session'),csrf):
            raise HTTPException(401,'Bitte anmelden.')
        return {'reviewer':user['name'],'role':user['role'],'user_id':user['id'],'email':user['email'],'account_status':user['registration_status'],'csrf_token':csrf}

    @app.post('/session/logout')
    def logout(request: Request):
        secret=request.cookies.get('review_session')
        if app.state.auth.identify(secret):
            csrf=request.headers.get('x-csrf-token')
            if not csrf or csrf != request.cookies.get('review_csrf') or not app.state.auth.identify(secret,csrf):
                raise HTTPException(403,'Ungültige Anfrage. Bitte Seite neu laden.')
        app.state.auth.logout(secret)
        response = JSONResponse({'ok': True})
        response.delete_cookie('review_session',path='/')
        response.delete_cookie('review_csrf',path='/')
        return response

    def require_admin(user: dict, password: str, code: str):
        try: app.state.auth.fresh_admin(user,password,code)
        except PermissionError: raise HTTPException(403,'Administrator-Anmeldung erneut bestätigen.')

    @app.get('/admin/users')
    def list_users(user: dict=Depends(token)):
        if user['role'] != 'admin': raise HTTPException(403,'Keine Berechtigung.')
        return app.state.auth.store.users()

    @app.post('/admin/users')
    def create_user(body: UserRequest, user: dict=Depends(token)):
        require_admin(user,body.admin_password,body.admin_code)
        temporary_password=secrets.token_urlsafe(24)
        try:
            created=app.state.auth.store.create_user(body.email,body.name,body.role,temporary_password)
        except ValueError:
            raise HTTPException(422,'Kontoangaben ungültig.')
        except Exception as exc:
            if type(exc).__name__ not in {'IntegrityError','UniqueViolation'}: raise
            raise HTTPException(409,'E-Mail-Adresse bereits vergeben.')
        return {'user':{k:created[k] for k in ('id','email','name','role','enabled')},'temporary_password':temporary_password}

    @app.post('/admin/users/{user_id}/approve')
    def approve_registration(user_id: UUID, body: AdminAction, user: dict=Depends(token)):
        return registration_decision(user_id, body, user, True)

    @app.post('/admin/users/{user_id}/reject')
    def reject_registration(user_id: UUID, body: AdminAction, user: dict=Depends(token)):
        return registration_decision(user_id, body, user, False)

    def registration_decision(user_id, body, user, approved):
        require_admin(user, body.admin_password, body.admin_code)
        try:
            app.state.auth.store.decide_registration(str(user_id), approved, user['id'])
        except ValueError:
            raise HTTPException(409, 'This registration is no longer pending.') from None
        return {'ok':True}

    @app.post('/admin/users/{user_id}/reset')
    def reset_user(user_id: UUID, body: AdminAction, user: dict=Depends(token)):
        require_admin(user,body.admin_password,body.admin_code)
        target=app.state.auth.store.user('id',str(user_id))
        if not target or target['role'] not in {'member', 'reviewer', 'tax_adviser'}: raise HTTPException(404,'Prüferkonto nicht gefunden.')
        temporary_password=secrets.token_urlsafe(24)
        store=app.state.auth.store
        store.reset_credentials(str(user_id),hash_password(temporary_password))
        return {'temporary_password':temporary_password}

    @app.post('/admin/users/{user_id}/disable')
    def disable_user(user_id: UUID, body: AdminAction, user: dict=Depends(token)):
        require_admin(user,body.admin_password,body.admin_code)
        target=app.state.auth.store.user('id',str(user_id))
        if not target or target['role'] not in {'member', 'reviewer', 'tax_adviser'}: raise HTTPException(404,'Prüferkonto nicht gefunden.')
        app.state.auth.store.set_enabled(str(user_id),False)
        return {'ok':True}

    @app.post('/admin/users/{user_id}/enable')
    def enable_user(user_id: UUID, body: AdminAction, user: dict=Depends(token)):
        require_admin(user,body.admin_password,body.admin_code)
        target=app.state.auth.store.user('id',str(user_id))
        if not target or target['role'] not in {'member', 'reviewer', 'tax_adviser'}: raise HTTPException(404,'Prüferkonto nicht gefunden.')
        if target['registration_status'] != 'active':
            raise HTTPException(409, 'Approve the pending registration before enabling the account.')
        app.state.auth.store.set_enabled(str(user_id),True)
        return {'ok':True}

    def get_state(run_id: UUID):
        try: return app.state.workflow.get(str(run_id))
        except KeyError: raise HTTPException(404,'Lauf nicht gefunden')

    def image_model_label():
        return (settings.image_generation_provider or settings.image_provider) + '/' + (settings.image_generation_model or settings.image_model)

    @app.get('/health')
    def health(): return {'status':'ok','mode':'demo' if settings.demo_mode else 'live'}

    @app.get('/system/readiness', dependencies=[Depends(token)])
    def system_readiness():
        return JSONResponse(readiness(settings, app.state.workflow.storage), headers={'Cache-Control':'no-store'})

    @app.get('/icps',dependencies=[Depends(token)])
    def icps():
        output=[]
        for path in sorted((settings.input_dir/'icps').iterdir()):
            if path.suffix in {'.json','.yaml','.yml'}:
                icp=load_icp(path)
                output.append({'id':icp.id,'name':icp.name,'drafting_approved':icp.id=='icp_1_buy_and_hold' or bool(icp.drafting_approved and icp.approved_fact_sources)})
        return output

    @app.post('/runs',dependencies=[Depends(token)])
    def start(body: StartRequest, credential: dict=Depends(token)):
        import re
        if not re.fullmatch(r'[a-z0-9_]+',body.icp_id): raise HTTPException(422,'Ungültige Zielgruppen-ID')
        path=next((settings.input_dir/'icps'/f'{body.icp_id}{suffix}' for suffix in ['.json','.yaml','.yml'] if (settings.input_dir/'icps'/f'{body.icp_id}{suffix}').is_file()),None)
        if path is None: raise HTTPException(404,'Zielgruppe nicht gefunden')
        try:
            icp = load_icp(path)
            if body.seed_topic:
                if settings.demo_mode and body.seed_topic != icp.seed_topic:
                    raise ValueError('Offline fixtures cover the default seed only')
                from src.security.sanitize import sanitize_text
                icp.seed_topic = sanitize_text(body.seed_topic, 120)
            return app.state.workflow.start(icp, owner_user_id=credential['id'])
        except ValueError: raise HTTPException(422,"Ungültige Eingabe oder im Demo-Modus nicht unterstützte Zielgruppe")

    @app.get('/runs')
    def runs(scope: Literal['accessible','mine','shared','all']='accessible', credential: dict=Depends(token)):
        access = RunAccess(app.state.auth.store)
        if scope == 'all' and credential['role'] != 'admin':
            raise HTTPException(403, 'Administrator access required.')
        output = []
        for state in app.state.workflow.storage.list_runs():
            owner = access.owner(state)
            shared = access.shared(state, credential)
            if not access.can_read(state, credential): continue
            if scope == 'mine' and owner != credential['id']: continue
            if scope == 'shared' and (not shared or owner == credential['id']): continue
            output.append({'run_id':str(state.run_id), 'status':state.status, 'revision':state.revision,
                'updated_at':state.updated_at, 'owner_user_id':owner,
                'access':'owner' if owner == credential['id'] else 'shared' if shared else 'administrator'})
        return output

    @app.get('/runs/{run_id}/sharing')
    def sharing(run_id: UUID, credential: dict=Depends(token)):
        state = get_state(run_id)
        access = RunAccess(app.state.auth.store)
        try: access.manage(state, credential)
        except AccessRevoked: raise HTTPException(403, 'Only the owner or administrator can manage sharing.') from None
        return {'owner_user_id':access.owner(state), 'shared_user_ids':access.shares(state),
            'reviewers':[{'id':u['id'],'name':u['name'],'role':u['role']} for u in app.state.auth.store.users()
                         if access.active(u) and access.reviewer(u)]}

    @app.post('/runs/{run_id}/sharing')
    def update_sharing(run_id: UUID, body: SharingRequest, credential: dict=Depends(token)):
        target = app.state.auth.store.user('id', str(body.user_id))
        if not target: raise HTTPException(422, 'Choose an activated reviewer.')
        try: RunAccess(app.state.auth.store).grant(get_state(run_id), target, credential, body.enabled)
        except AccessRevoked: raise HTTPException(403, 'Only the owner or administrator can manage sharing.') from None
        except ValueError: raise HTTPException(422, 'Assign an owner and choose an activated professional or final reviewer.') from None
        return {'ok':True}

    @app.post('/runs/{run_id}/owner')
    def assign_owner(run_id: UUID, body: OwnerRequest, credential: dict=Depends(token)):
        require_admin(credential, body.admin_password, body.admin_code)
        target = app.state.auth.store.user('id', str(body.user_id))
        try: RunAccess(app.state.auth.store).assign(get_state(run_id), target, credential)
        except ValueError: raise HTTPException(422, 'Choose an activated account.') from None
        return {'ok':True}

    @app.get('/budget', dependencies=[Depends(token)])
    def monthly_budget():
        return {**app.state.workflow.storage.monthly_costs(),'run_limit_usd':settings.max_run_usd}

    @app.post('/runs/{run_id}/professional-reviews')
    def professional_review(run_id: UUID, body: ProfessionalReviewRequest, credential: dict=Depends(token)):
        from src.services.professional_review import attest
        try:
            return attest(app.state.workflow, str(run_id), body, credential['id'])
        except PermissionError:
            raise HTTPException(403, 'Fachliche Freigabe erfordert ein eingerichtetes Steuerberaterkonto.')
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Aktuelle Revision, Hauptinfografik und offene Punkte prüfen.')

    @app.post('/runs/{run_id}/publication')
    def record_publication(run_id: UUID, body: PublicationRequest, credential: dict=Depends(token)):
        from src.services.publication import record
        try:
            return record(app.state.workflow, str(run_id), body, credential['name'])
        except PermissionError:
            raise HTTPException(403, 'Keine Berechtigung zur Veröffentlichungsbestätigung.')
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Aktuelle Freigabe und gültige veröffentlichte Website-URL erforderlich.')

    @app.post('/runs/{run_id}/outcomes')
    def record_outcome(run_id: UUID, body: OutcomeRequest, credential: dict=Depends(token)):
        from src.services.publication import record_outcome
        try:
            return record_outcome(app.state.workflow, str(run_id), body, credential['name'])
        except PermissionError:
            raise HTTPException(403, 'Keine Berechtigung zur Ergebniserfassung.')
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Veröffentlichung, Revision und Messzeitraum prüfen.')

    @app.get('/runs/{run_id}',dependencies=[Depends(token)])
    def state(run_id: UUID, credential: dict=Depends(token)):
        return RunAccess(app.state.auth.store).visible_state(get_state(run_id), credential)

    @app.get('/runs/{run_id}/preflight',dependencies=[Depends(token)])
    def run_preflight(run_id: UUID,recommendation_id: str | None = None,credential: dict=Depends(token)):
        from src.services.preflight import inspect
        from src.services.guided_portal import workflow_summary
        try:
            state=get_state(run_id);report=inspect(app.state.workflow,state,recommendation_id)
            report['workflow']=workflow_summary(state,report,credential)
            return report
        except KeyError:raise HTTPException(404,'Recommendation not found') from None

    @app.get('/runs/{run_id}/progress',dependencies=[Depends(token)])
    def run_progress(run_id: UUID, credential: dict=Depends(token)):
        from src.services.progress import progress
        return progress(app.state.workflow,get_state(run_id),credential)

    @app.get('/runs/{run_id}/review-preview', response_class=HTMLResponse)
    def review_preview(run_id: UUID, revision: int, finding_id: str | None = None, credential: dict=Depends(token)):
        from src.services.progress import progress
        from src.services.guided_portal import preview
        from src.services.editorial_brand import ARTICLE_CSP
        state=get_state(run_id)
        if revision<0 or revision!=state.revision:raise HTTPException(409,'Open the current saved revision before viewing highlights.')
        if not state.html:raise HTTPException(409,'The draft has not been saved yet.')
        if finding_id and not re.fullmatch(r'[a-zA-Z0-9-]{1,64}',finding_id):raise HTTPException(422,'Invalid finding identifier.')
        report=progress(app.state.workflow,state,credential)
        from src.services.presentation import render_current
        presented=state.model_copy(update={'html':render_current(state,settings)})
        try:html=preview(presented,report['findings'],finding_id)
        except KeyError:raise HTTPException(404,'Finding not found for this revision.') from None
        return HTMLResponse(html,headers={'Content-Security-Policy':ARTICLE_CSP.replace("frame-ancestors 'none'","frame-ancestors 'self'"),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @app.post('/runs/{run_id}/reviews')
    def review(run_id: UUID,body: ReviewRequest,credential: dict=Depends(token)):
        try: return app.state.workflow.submit_review(str(run_id),body,credential['name'],credential['id'])
        except KeyError: raise HTTPException(404,'Lauf nicht gefunden')
        except PermissionError: raise HTTPException(403,'Keine Berechtigung zur Prüfung')
        except (ValueError,Timeout,RunBusy): raise HTTPException(409,'Prüfentscheidung kann nicht übernommen werden. Bitte aktualisieren Sie den Lauf und prüfen Sie Revision und Status.')

    @app.post('/runs/{run_id}/editorial')
    def edit_editorial(run_id: UUID, body: EditorialEditRequest, credential: dict=Depends(token)):
        from src.services.editorial_edits import save_edit
        from src.security.sanitize import PrivacyError, InputSizeError
        try:
            return save_edit(app.state.workflow, str(run_id), body, credential)
        except PermissionError:
            raise HTTPException(403, 'Only an authorized owner, administrator or final reviewer can edit this draft.') from None
        except PrivacyError:
            raise HTTPException(422, 'Use public editorial content only. Remove private addresses or personal identifiers before saving. The approved public company email is allowed.') from None
        except InputSizeError:
            raise HTTPException(413, 'An edited text field exceeds the supported size. Split long sections and keep source quotations focused.') from None
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Refresh the saved revision. Check active or uncertain tasks, the revision limit and the selected page type before saving.') from None

    @app.get('/runs/{run_id}/release',dependencies=[Depends(token)])
    def release_details(run_id: UUID):
        from src.combined.editorial import release_errors
        from src.services.professional_review import is_current
        state=get_state(run_id)
        return {'blockers':release_errors(state,settings) if state.combined_version else [],
                'claims':state.editorial.get('claims',[]),
                'sources':[{'id':x['id'],'url':x['url'],'retrieved_at':x['retrieved_at']}
                           for x in state.research_packet.get('official_sources',[])],
                'graphic':state.editorial.get('graphic'), 'manual_upload_only':True,
                'professional_review_current': is_current(state), 'publication': state.publication}

    class GraphicUpload(StrictModel):
        content_base64: str = Field(max_length=400000)
        expected_revision: int = Field(ge=0)

    @app.post('/runs/{run_id}/graphic')
    def manual_graphic(run_id: UUID, body: GraphicUpload, credential: dict=Depends(token)):
        try:
            import base64
            from src.combined.graphics import attach
            content=base64.b64decode(body.content_base64,validate=True)
            return attach(app.state.workflow,str(run_id),content,body.expected_revision)
        except (ValueError,Timeout,RunBusy):
            raise HTTPException(409,'Bitte aktuelle Revision und WebP-Grafik prüfen: 1200-1600 Pixel breit, unter 300 KB.')

    @app.get('/runs/{run_id}/article',dependencies=[Depends(token)],response_class=HTMLResponse)
    def article(run_id: UUID):
        s=get_state(run_id)
        if not s.html: raise HTTPException(409,'Entwurf noch nicht bereit')
        from src.services.editorial_brand import ARTICLE_CSP
        from src.services.presentation import render_current
        return HTMLResponse(render_current(s,settings),headers={'Content-Security-Policy':ARTICLE_CSP,'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @app.get('/runs/{run_id}/dashboard',dependencies=[Depends(token)])
    def dashboard_view(run_id: UUID,credential: dict=Depends(token),scope: Literal['icp_1','site']='icp_1',country: Literal['de','all']='de'):
        from src.services.dashboard import dashboard
        return dashboard(RunAccess(app.state.auth.store).visible_state(get_state(run_id), credential),app.state.workflow.storage,scope,country)

    @app.get('/runs/{run_id}/analysis-report.pdf',dependencies=[Depends(token)])
    def analysis_report(run_id: UUID,credential: dict=Depends(token),scope: Literal['icp_1','site']='icp_1',country: Literal['de','all']='de',language: Literal['de','en']='de'):
        from src.services.dashboard import dashboard
        from src.services.analysis_pdf import create_report
        from fastapi.responses import Response
        data=dashboard(RunAccess(app.state.auth.store).visible_state(get_state(run_id), credential),app.state.workflow.storage,scope,country)
        return Response(create_report(data,language=language),media_type='application/pdf',headers={
            'Content-Disposition':f'attachment; filename="seo-analyse-{str(run_id)[:8]}-{scope}-{country}.pdf"','Cache-Control':'no-store'})

    @app.get('/runs/{run_id}/visibility')
    def visibility_view(run_id: UUID,credential: dict=Depends(token)):
        from src.services.visibility import view,tools
        state=require_access(run_id,credential)
        return {**view(state),'tools':tools(settings,state,app.state.workflow.storage),
            'campaign':app.state.workflow.storage.campaign_costs(str(run_id))}

    @app.post('/runs/{run_id}/visibility/observations')
    def visibility_import(run_id: UUID,body: Observation,credential: dict=Depends(token)):
        from src.services.visibility import add
        state=require_access(run_id,credential)
        try:RunAccess(app.state.auth.store).manage(state,credential)
        except AccessRevoked:raise HTTPException(403,'Only the owner or administrator may import observations.') from None
        with app.state.workflow.storage.run_lock(str(run_id)):
            state=require_access(run_id,credential)
            if any(j.status in {'running','queued','paused'} for j in app.state.workflow.storage.list_jobs(str(run_id))):
                raise HTTPException(409,'Inspect active or uncertain work before importing observations.')
            try:return add(app.state.workflow.storage,state,body,credential['id'])
            except ValueError:raise HTTPException(409,'Observation conflicts with saved evidence or the storage limit.') from None

    @app.get('/runs/{run_id}/research-evidence')
    def research_evidence(run_id: UUID,format: Literal['json','csv']='json',credential: dict=Depends(token)):
        from src.services.dashboard import dashboard
        from src.services.evidence_report import export_evidence
        state=require_access(run_id,credential)
        state=RunAccess(app.state.auth.store).visible_state(state,credential)
        data=dashboard(state,app.state.workflow.storage,state.research_scope or 'icp_1','de')
        if format=='json':return data
        return Response(export_evidence(data),media_type='text/csv',headers={
            'Content-Disposition':f'attachment; filename="research-evidence-{str(run_id)[:8]}.csv"','Cache-Control':'no-store'})

    @app.post('/runs/{run_id}/selection')
    def select_recommendation(run_id: UUID, body: SelectionRequest, credential: dict=Depends(token)):
        try:
            return app.state.workflow.submit_selection(str(run_id), body, credential['name'], credential['id'])
        except KeyError:
            raise HTTPException(404, 'Lauf nicht gefunden.')
        except (ValueError, PermissionError, Timeout, RunBusy):
            raise HTTPException(409, 'Auswahl ist veraltet oder nicht zulässig. Bitte prüfen Sie den Status und bestätigen Sie erforderliche Hinweise.')

    @app.get('/runs/{run_id}/topic-map',dependencies=[Depends(token)],response_class=HTMLResponse)
    def topic_map(run_id: UUID):
        s=get_state(run_id)
        if not s.topic_map: raise HTTPException(409,'Themenplan noch nicht bereit')
        from src.services.editorial_brand import ARTICLE_CSP
        return HTMLResponse(render_topic_map(s.topic_map, verified_topic_links(s)),headers={'Content-Security-Policy':ARTICLE_CSP,'Cache-Control':'no-store'})

    @app.get('/runs/{run_id}/internal-workflow')
    def internal_workflow_view(run_id: UUID, credential: dict=Depends(token)):
        state = require_access(run_id, credential)
        try: RunAccess(app.state.auth.store).manage(state, credential)
        except AccessRevoked: raise HTTPException(403, 'Internal review requires the run owner or administrator.') from None
        from src.services.internal_workflow import view
        return view(settings, app.state.workflow.storage, state)

    from src.services.internal_workflow import InternalAction

    @app.post('/runs/{run_id}/internal-workflow')
    def internal_workflow_action(run_id: UUID, body: InternalAction, credential: dict=Depends(token)):
        require_access(run_id, credential)
        from src.services.internal_workflow import act
        try: return act(app.state.workflow, str(run_id), body, credential)
        except AccessRevoked: raise HTTPException(403, 'Internal review requires the run owner or administrator.') from None
        except (ValueError, Timeout, RunBusy) as exc: raise HTTPException(409, str(exc)) from None

    @app.get('/runs/{run_id}/internal-workflow/download')
    def internal_workflow_download(run_id: UUID, credential: dict=Depends(token)):
        state = require_access(run_id, credential)
        try: RunAccess(app.state.auth.store).manage(state, credential)
        except AccessRevoked: raise HTTPException(403, 'Internal export requires the run owner or administrator.') from None
        from src.services.internal_workflow import download
        from src.services.editorial_brand import PROFILE_VERSION
        try:
            with app.state.workflow.storage.run_lock(str(run_id)):
                state = require_access(run_id, credential)
                data = download(settings, app.state.workflow.storage, state)
        except ValueError as exc: raise HTTPException(409, str(exc)) from None
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="internally-reviewed-professionally-unapproved-{run_id}-r{state.revision}-brand-{PROFILE_VERSION}.zip"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/runs/{run_id}/evaluation/test-walkthrough')
    def test_walkthrough(run_id: UUID, credential: dict=Depends(token)):
        state = require_access(run_id, credential)
        try: RunAccess(app.state.auth.store).drafting(state, credential)
        except AccessRevoked: raise HTTPException(403, 'Only the owner, administrator or authorized final reviewer can rehearse the workflow.') from None
        from src.services.test_walkthrough import snapshot
        return snapshot(settings, app.state.workflow.storage, state)

    @app.get('/runs/{run_id}/evaluation/test-export')
    def test_walkthrough_export(run_id: UUID, revision: int, context: str, credential: dict=Depends(token)):
        state = require_access(run_id, credential)
        try: RunAccess(app.state.auth.store).drafting(state, credential)
        except AccessRevoked: raise HTTPException(403, 'Only the owner, administrator or authorized final reviewer can rehearse the workflow.') from None
        if revision < 0 or not re.fullmatch(r'[0-9a-f]{64}', context):
            raise HTTPException(422, 'Invalid test revision or context.')
        from src.services.test_walkthrough import export
        from src.services.editorial_brand import PROFILE_VERSION
        try:
            with app.state.workflow.storage.run_lock(str(run_id)):
                state = require_access(run_id, credential)
                data = export(settings, app.state.workflow.storage, state, revision, context)
        except ValueError as exc: raise HTTPException(409, str(exc)) from None
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="test-unapproved-{run_id}-r{revision}-brand-{PROFILE_VERSION}.zip"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/runs/{run_id}/evaluation/preflight')
    def evaluation_preflight(run_id: UUID, credential: dict=Depends(token)):
        state=require_access(run_id,credential)
        try:RunAccess(app.state.auth.store).drafting(state,credential)
        except AccessRevoked:raise HTTPException(403,'Only the owner, administrator or authorized final reviewer can evaluate outputs.') from None
        from src.services.evaluation import preflight
        return preflight(settings,app.state.workflow.storage,state)

    @app.get('/runs/{run_id}/evaluation')
    def evaluation_result(run_id: UUID, credential: dict=Depends(token)):
        state=require_access(run_id,credential)
        from src.services.evaluation import latest,LABEL
        from src.services.presentation import design_status
        return {'label':LABEL,'revision':state.revision,'evaluation':latest(app.state.workflow.storage,state,settings),'design_status':design_status(state)}

    @app.get('/runs/{run_id}/evaluation/download')
    def evaluation_download(run_id: UUID, credential: dict=Depends(token)):
        state=require_access(run_id,credential)
        if not state.editorial:raise HTTPException(409,'Save an article draft first.')
        from src.services.evaluation import package
        from src.services.editorial_brand import PROFILE_VERSION
        from fastapi.responses import Response
        return Response(package(settings,app.state.workflow.storage,state),media_type='application/zip',
            headers={'Content-Disposition':f'attachment; filename="evaluation-unapproved-{run_id}-r{state.revision}-brand-{PROFILE_VERSION}.zip"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/runs/{run_id}/evaluation/review-package', response_class=HTMLResponse)
    def evaluation_review_package(run_id: UUID, credential: dict=Depends(token)):
        state = require_access(run_id, credential)
        if not state.editorial: raise HTTPException(409, 'Save an article draft first.')
        from src.services.evaluation import review_package
        from src.services.editorial_brand import ARTICLE_CSP
        return HTMLResponse(review_package(settings, app.state.workflow.storage, state), headers={
            'Content-Security-Policy': ARTICLE_CSP + '; frame-src about:',
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})

    @app.get('/runs/{run_id}/evaluation/infographic')
    def evaluation_infographic(run_id: UUID, credential: dict=Depends(token)):
        state=require_access(run_id,credential)
        from src.services.evaluation_vision import attachment
        from fastapi.responses import Response
        try:data,_=attachment(settings,state)
        except ValueError:raise HTTPException(409,'The current validated infographic is unavailable. Saved content remains available.') from None
        return Response(data,media_type='image/webp',headers={'Content-Security-Policy':"default-src 'none'"})

    @app.get('/runs/{run_id}/evaluation/images/preflight')
    def evaluation_images_preflight(run_id: UUID, credential: dict=Depends(token)):
        state=require_access(run_id,credential)
        from src.services.evaluation_images import preflight
        return preflight(settings,app.state.workflow.storage,state)

    @app.get('/runs/{run_id}/evaluation/images/{platform}')
    def evaluation_image_file(run_id: UUID, platform: Platform, credential: dict=Depends(token)):
        state=require_access(run_id,credential)
        from src.services.evaluation_images import file
        from fastapi.responses import Response
        try:data,item=file(settings,app.state.workflow.storage,state,platform)
        except ValueError:raise HTTPException(404,'No compatible test image is available.') from None
        return Response(data,media_type=item['media_type'],headers={'Content-Disposition':'inline; filename="'+item['filename']+'"','Content-Security-Policy':"default-src 'none'"})

    @app.get('/runs/{run_id}/evaluation/assistant-review')
    def assistant_review(run_id: UUID, credential: dict=Depends(token)):
        from src.services.assistant_review import load
        state=require_access(run_id,credential)
        record=load(settings,app.state.workflow.storage,state)
        return {'review':record['review'] if record else None}

    @app.get('/runs/{run_id}/evaluation/combined-review',response_class=HTMLResponse)
    def combined_review(run_id: UUID, credential: dict=Depends(token)):
        from src.services.assistant_review import load,render_current_review
        state=require_access(run_id,credential)
        record=load(settings,app.state.workflow.storage,state)
        if not record:raise HTTPException(404,'No compatible independent review is available for this revision.')
        return HTMLResponse(render_current_review(settings,app.state.workflow.storage,state,record),headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data:; frame-src about:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'; sandbox"})

    @app.get('/runs/{run_id}/bundle',dependencies=[Depends(token)])
    def bundle(run_id: UUID, credential: dict=Depends(token)):
        s=RunAccess(app.state.auth.store).visible_state(get_state(run_id), credential)
        directory=settings.output_dir/'runs'/str(run_id)/('approved' if s.status=='exported' else f'revision-{s.revision:03d}')
        if not directory.is_dir(): raise HTTPException(409,'Export noch nicht bereit')
        from src.services.website_package import files as website_files, handoff_index
        from src.services.presentation import render_current, design_status
        additions=website_files(s,settings)
        if s.combined_version and s.editorial:additions['START_HERE.html']=handoff_index(s)
        buffer=BytesIO()
        with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob('*')):
                if path.is_file() and path.suffix in {'.html','.json','.md','.csv','.mmd','.txt','.webp'}:
                    relative=str(path.relative_to(directory))
                    if relative in additions:
                        continue
                    if relative == 'reviews/state.json':
                        archive.writestr(relative, s.model_dump_json(indent=2))
                    elif relative == 'research-snapshot.json':
                        archive.writestr(relative, json.dumps(s.research_snapshot, ensure_ascii=False, indent=2))
                    elif s.combined_version and relative.startswith('drafts-html/'):
                        archive.writestr(relative,render_current(s,settings))
                    elif relative=='topic-maps/topic-map.html' and s.topic_map:
                        archive.writestr(relative,render_topic_map(s.topic_map,verified_topic_links(s)))
                    else:
                        archive.write(path, relative)
            for name,value in additions.items():archive.writestr(name,value)
            posts = app.state.workflow.storage.list_assets(str(run_id), 'post')
            for post in posts:
                if post.source_revision == s.revision and post.status == 'approved':
                    archive.writestr(f'social/{post.platform}-{post.id}.json', post.model_dump_json(indent=2))
            platform_status={'website':'approved' if s.status=='exported' else 'pending',
                             'linkedin':'pending','google_business_profile':'pending','manual_upload_only':True}
            from src.services.publication import social_ready
            platform_status['publication'] = s.publication
            platform_status['outcomes'] = s.outcomes
            platform_status['design_status'] = design_status(s)
            platform_status['website_upload_ready'] = False
            platform_status['social_upload_ready'] = {p.platform: social_ready(s,p) for p in posts if p.source_revision==s.revision}
            for post in posts:
                if post.source_revision!=s.revision:continue
                platform_status[post.platform]=post.status
                if post.status=='approved':
                    archive.writestr('social/'+post.platform+'.txt',post.text)
            archive.writestr('platform-status.json',json.dumps(platform_status,ensure_ascii=False,indent=2))
            current_posts = {p.platform: p for p in posts if p.source_revision == s.revision}
            for image in app.state.workflow.storage.list_assets(str(run_id), 'image'):
                if image.source_revision != s.revision or image.status != 'approved':
                    continue
                if image.platform != 'website' and (image.platform not in current_posts or
                    current_posts[image.platform].status != 'approved' or
                    image.content_id != str(current_posts[image.platform].id)):
                    continue
                if image.optimization_status not in {'completed', 'legacy'}:
                    continue
                image_path = image_file_path(settings, image)
                if image_path.is_file():
                    archive.write(image_path, f'images/{Path(image.seo_filename).name if image.seo_filename else image.platform + "-" + str(image.id) + image_path.suffix}')
                    archive.writestr(f'images/{image.platform}-{image.id}.json', image.model_dump_json(indent=2))
        return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="seo-{run_id}.zip"','Cache-Control':'no-store'})

    @app.post('/runs/{run_id}/social-posts',dependencies=[Depends(token)])
    def prepare_social(run_id: UUID):
        workflow = app.state.workflow
        try:
            with workflow.storage.run_lock(str(run_id)):
                return SocialService(settings, workflow.storage).prepare(get_state(run_id))
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Social-Media-Entwürfe können derzeit nicht erstellt werden. Bitte prüfen Sie den Laufstatus.')
        except HTTPException:
            raise
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error('Social draft generation failed (%s) for run %s', type(exc).__name__, run_id)
            raise HTTPException(502, 'Social-Media-Entwürfe konnten nicht erstellt werden. Der Website-Entwurf bleibt erhalten.')

    @app.get('/runs/{run_id}/social-posts',dependencies=[Depends(token)])
    def social_posts(run_id: UUID):
        get_state(run_id)
        return app.state.workflow.storage.list_assets(str(run_id), 'post')

    class SocialLinkRequest(StrictModel):
        expected_revision: int = Field(ge=0)

    @app.post('/runs/{run_id}/social-posts/{post_id}/article-link')
    def social_article_link(run_id: UUID, post_id: UUID, body: SocialLinkRequest, credential: dict=Depends(token)):
        if credential['name'] not in {'Alex','Sam'}:
            raise HTTPException(403, 'Keine Berechtigung zur Linkänderung.')
        from src.services.publication import use_article_link
        try:
            return use_article_link(app.state.workflow, str(run_id), str(post_id), body.expected_revision)
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Aktuelle Veröffentlichung und Textlänge prüfen. Linkänderungen benötigen eine neue Freigabe.')

    @app.post('/runs/{run_id}/social-posts/{post_id}/reviews')
    def review_social(run_id: UUID, post_id: UUID, body: AssetDecision, credential: dict=Depends(token)):
        workflow = app.state.workflow
        reviewer = credential['name']
        try:
            with workflow.storage.run_lock(str(run_id)):
                return SocialService(settings, workflow.storage).review(get_state(run_id), str(post_id), body.action, reviewer, credential['id'])
        except KeyError: raise HTTPException(404, 'Social-Media-Entwurf nicht gefunden')
        except PermissionError: raise HTTPException(403, 'Keine Berechtigung zur Social-Media-Freigabe.')
        except (ValueError, Timeout, RunBusy): raise HTTPException(409, 'Entwurf ist veraltet oder bereits geprüft. Bitte aktualisieren Sie den Lauf.')

    @app.get('/runs/{run_id}/ai-calls',dependencies=[Depends(token)])
    def ai_calls(run_id: UUID):
        get_state(run_id)
        return app.state.workflow.storage.list_ai_calls(str(run_id))

    @app.get('/runs/{run_id}/image-sets',dependencies=[Depends(token)])
    def image_sets(run_id: UUID):
        return ImageSetService(settings, app.state.workflow.storage).list(get_state(run_id))

    @app.get('/image-generation/options', dependencies=[Depends(token)])
    def image_options():
        return image_generation_options(settings)

    @app.post('/runs/{run_id}/image-sets',dependencies=[Depends(token)])
    def create_image_set(run_id: UUID, body: dict[str, bool]):
        state = get_state(run_id)
        if set(body) - {'png_requested'}:
            raise HTTPException(422, 'Ungültige Bildoption')
        workflow = app.state.workflow
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageSetService(settings, workflow.storage).create(state, body.get('png_requested', False))
        except ImageGenerationError as exc:
            raise HTTPException(502, f'Bildgenerierung mit {exc.provider}/{exc.model} fehlgeschlagen ({exc.code}). Der Textentwurf bleibt erhalten.')
        except ImageMasterError as exc:
            raise HTTPException(502, f'Das Bildmodell {exc.provider}/{exc.model} lieferte kein geeignetes Masterbild. Der Textentwurf bleibt erhalten.')
        except ModelConfigurationError:
            raise HTTPException(409, f'Bildmodell image_generation ({image_model_label()}) ist nicht korrekt konfiguriert oder unterstützt die Funktion nicht.')
        except (ValueError, BudgetExceeded, Timeout, RunBusy):
            raise HTTPException(409, 'Bildserie kann derzeit nicht erstellt werden. Prüfen Sie Social-Media-Entwürfe, Modellkonfiguration und Bildlimit.')
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error('Image set generation failed (%s) for run %s', type(exc).__name__, run_id)
            raise HTTPException(502, 'Bildserie konnte nicht erstellt werden. Der Textentwurf bleibt erhalten.')

    @app.post('/runs/{run_id}/image-sets/{master_id}/variations',dependencies=[Depends(token)])
    def vary_image_set(run_id: UUID, master_id: UUID):
        state = get_state(run_id)
        workflow = app.state.workflow
        parent = workflow.storage.get_asset(str(master_id), 'master')
        if not parent or parent.run_id != run_id:
            raise HTTPException(404, 'Bildserie nicht gefunden')
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageSetService(settings, workflow.storage).create(state, parent.png_requested, str(master_id))
        except ImageGenerationError as exc:
            raise HTTPException(502, f'Bildgenerierung mit {exc.provider}/{exc.model} fehlgeschlagen ({exc.code}). Der Textentwurf bleibt erhalten.')
        except ImageMasterError as exc:
            raise HTTPException(502, f'Das Bildmodell {exc.provider}/{exc.model} lieferte kein geeignetes Masterbild. Der Textentwurf bleibt erhalten.')
        except ModelConfigurationError:
            raise HTTPException(409, f'Bildmodell image_generation ({image_model_label()}) ist nicht korrekt konfiguriert oder unterstützt die Funktion nicht.')
        except (ValueError, BudgetExceeded, Timeout, RunBusy):
            raise HTTPException(409, 'Neue Bildserie kann derzeit nicht erstellt werden. Prüfen Sie Revision, Modell und Bildlimit.')
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error('Image set variation failed (%s) for run %s', type(exc).__name__, run_id)
            raise HTTPException(502, 'Neue Bildserie konnte nicht erstellt werden. Der Textentwurf bleibt erhalten.')

    @app.post('/runs/{run_id}/image-sets/{master_id}/process/{platform}',dependencies=[Depends(token)])
    def retry_image_set_variant(run_id: UUID, master_id: UUID, platform: Platform):
        state = get_state(run_id)
        workflow = app.state.workflow
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageSetService(settings, workflow.storage).retry_variant(state, str(master_id), platform)
        except KeyError:
            raise HTTPException(404, 'Bildversion nicht gefunden')
        except ImageProcessingError:
            raise HTTPException(502, 'Bildverarbeitung fehlgeschlagen. Das Masterbild bleibt für einen weiteren Versuch erhalten.')
        except (ValueError, Timeout, RunBusy):
            raise HTTPException(409, 'Bildverarbeitung für diese Version ist nicht verfügbar.')

    @app.post('/runs/{run_id}/images',dependencies=[Depends(token)])
    def generate_image(run_id: UUID, body: ImageRequest):
        workflow = app.state.workflow
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageService(settings, workflow.storage).generate(get_state(run_id), body.platform, png_requested=body.png_requested)
        except ImageProcessingError as exc: return JSONResponse({'detail':'Bildverarbeitung fehlgeschlagen. Sie können sie ohne neue Generierung wiederholen.', 'image_id':str(exc.image_id)},status_code=502)
        except ImageGenerationError as exc: raise HTTPException(502, f'Bildgenerierung mit {exc.provider}/{exc.model} fehlgeschlagen ({exc.code}). Der Textentwurf bleibt erhalten.')
        except ModelConfigurationError: raise HTTPException(409, f'Bildmodell image_generation ({image_model_label()}) ist nicht korrekt konfiguriert oder unterstützt die Funktion nicht.')
        except (ValueError, Timeout, RunBusy, BudgetExceeded): raise HTTPException(409, 'Bild kann derzeit nicht erstellt werden. Bitte prüfen Sie die Konfiguration, das Bildlimit und den Laufstatus.')
        except RuntimeError: raise HTTPException(502, 'Bildgenerierung fehlgeschlagen. Der Textentwurf bleibt erhalten; Sie können es erneut versuchen.')

    @app.post('/runs/{run_id}/images/{image_id}/variations',dependencies=[Depends(token)])
    def vary_image(run_id: UUID, image_id: UUID):
        workflow = app.state.workflow
        parent = workflow.storage.get_asset(str(image_id), 'image')
        if not parent or parent.run_id != run_id: raise HTTPException(404, 'Bild nicht gefunden')
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageService(settings, workflow.storage).generate(get_state(run_id), parent.platform, str(image_id), parent.png_requested)
        except ImageProcessingError as exc: return JSONResponse({'detail':'Bildverarbeitung fehlgeschlagen. Sie können sie ohne neue Generierung wiederholen.', 'image_id':str(exc.image_id)},status_code=502)
        except ImageGenerationError as exc: raise HTTPException(502, f'Bildgenerierung mit {exc.provider}/{exc.model} fehlgeschlagen ({exc.code}). Der Textentwurf bleibt erhalten.')
        except ModelConfigurationError: raise HTTPException(409, f'Bildmodell image_generation ({image_model_label()}) ist nicht korrekt konfiguriert oder unterstützt die Funktion nicht.')
        except (ValueError, Timeout, RunBusy, BudgetExceeded): raise HTTPException(409, 'Bildversion ist veraltet oder die Generierung ist nicht verfügbar.')
        except RuntimeError: raise HTTPException(502, 'Neue Bildvariante fehlgeschlagen. Der Textentwurf bleibt erhalten.')

    @app.get('/runs/{run_id}/images',dependencies=[Depends(token)])
    def images(run_id: UUID):
        get_state(run_id)
        return app.state.workflow.storage.list_assets(str(run_id), 'image')

    @app.post('/runs/{run_id}/images/{image_id}/process',dependencies=[Depends(token)])
    def retry_image_processing(run_id: UUID, image_id: UUID):
        workflow = app.state.workflow
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageService(settings, workflow.storage).retry_processing(get_state(run_id), str(image_id))
        except KeyError: raise HTTPException(404, 'Bild nicht gefunden')
        except ImageProcessingError as exc: return JSONResponse({'detail':'Bildverarbeitung erneut fehlgeschlagen. Das Originalbild bleibt für einen weiteren Versuch erhalten.', 'image_id':str(exc.image_id)},status_code=502)
        except (ValueError, Timeout, RunBusy): raise HTTPException(409, 'Bildverarbeitung ist nicht verfügbar. Bitte prüfen Sie Bildversion und Laufstatus.')

    @app.get('/runs/{run_id}/images/{image_id}/file',dependencies=[Depends(token)])
    def image_file(run_id: UUID, image_id: UUID):
        get_state(run_id)
        asset = app.state.workflow.storage.get_asset(str(image_id), 'image')
        if not asset or asset.run_id != run_id: raise HTTPException(404, 'Bild nicht gefunden')
        if asset.optimization_status not in {'completed', 'legacy'}: raise HTTPException(409, 'Bildverarbeitung noch nicht abgeschlossen')
        path = image_file_path(settings, asset)
        if not path.is_file(): raise HTTPException(404, 'Bilddatei nicht gefunden')
        name = Path(asset.seo_filename).name if asset.seo_filename else path.name
        return Response(path.read_bytes(), media_type=MEDIA_TYPES[asset.final_format], headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Disposition':f'inline; filename="{name}"'})

    @app.post('/runs/{run_id}/images/{image_id}/reviews')
    def review_image(run_id: UUID, image_id: UUID, body: AssetDecision, credential: dict=Depends(token)):
        workflow = app.state.workflow
        reviewer = credential['name']
        try:
            with workflow.storage.run_lock(str(run_id)):
                return ImageService(settings, workflow.storage).review(get_state(run_id), str(image_id), body.action, reviewer, credential['id'])
        except KeyError: raise HTTPException(404, 'Bild nicht gefunden')
        except (ValueError, Timeout, RunBusy): raise HTTPException(409, 'Bild ist veraltet oder bereits geprüft. Bitte aktualisieren Sie den Lauf.')

    @app.post('/jobs', status_code=202)
    def enqueue_job(body: QueueJobRequest, credential: dict=Depends(token)):
        workflow = app.state.workflow
        if body.run_id:
            state = require_access(body.run_id, credential)
            if body.kind in {'selection', 'quality_checks', 'ai_visibility', 'evaluation', 'evaluation_images'}:
                try: RunAccess(app.state.auth.store).drafting(state, credential)
                except AccessRevoked: raise HTTPException(403, 'Only the owner, administrator or trusted final reviewer can perform this drafting step.') from None
        reviewer = credential['name']
        if body.run_budget_usd is not None and (body.kind!='start' or body.run_budget_usd>settings.max_run_usd):
            raise HTTPException(422, 'Choose a creation-time run budget within the application run limit.')
        if body.research_scope is not None and body.kind!='start':
            raise HTTPException(422,'Research scope can only be chosen when creating a run.')
        if body.internal_link_policy is not None and body.kind!='start':
            raise HTTPException(422,'Internal link policy can only be chosen when creating a run.')
        if body.research_budget_usd is not None and body.research_budget_usd>settings.max_run_usd:
            raise HTTPException(422,'Research allowance exceeds the configured application limit.')
        if body.research_budget_usd is not None and body.kind!='research_refresh':
            raise HTTPException(422,'Recherchebudget ist nur für Rechercheaufgaben zulässig.')
        if body.selection is not None and body.kind != 'selection':
            raise HTTPException(422, 'Auswahl ist nur für eine Auswahlaufgabe zulässig.')
        if body.expected_revision is not None and body.kind not in {'quality_checks','evaluation','evaluation_images'}:
            raise HTTPException(422, 'Prüfstand ist nur für eine erneute Qualitätsprüfung zulässig.')
        fingerprint_payload = body.model_dump(mode='json', exclude={'idempotency_key'})
        if body.kind not in {'evaluation','evaluation_images'}:
            if body.confirm_evaluation or body.evaluation_plan_id:
                raise HTTPException(422,'Evaluation confirmation is only valid for an evaluation job.')
            fingerprint_payload.pop('evaluation_plan_id',None)
            fingerprint_payload.pop('confirm_evaluation',None)
        if body.research_scope is None:
            fingerprint_payload.pop('research_scope',None)
        if body.internal_link_policy is None:
            fingerprint_payload.pop('internal_link_policy',None)
        if body.run_budget_usd is None:
            fingerprint_payload.pop('run_budget_usd',None)
        # Match token-era request fingerprints for clients replaying an existing request.
        if body.selection is None:
            fingerprint_payload.pop('selection', None)
        if body.expected_revision is None:
            fingerprint_payload.pop('expected_revision', None)
        if body.research_budget_usd is None:
            fingerprint_payload.pop('research_budget_usd',None)
        if fingerprint_payload.get('selection') and not fingerprint_payload['selection'].get('professional_support'):
            fingerprint_payload['selection'].pop('professional_support',None)
        if fingerprint_payload.get('selection') and fingerprint_payload['selection'].get('verified_parent_url') is None:
            fingerprint_payload['selection'].pop('verified_parent_url',None)
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload,
                                                sort_keys=True).encode()).hexdigest()
        previous = workflow.storage.get_job_by_idempotency(str(body.idempotency_key))
        if previous:
            if previous.reviewer_id != credential['id'] or previous.request_fingerprint != fingerprint:
                raise HTTPException(409, 'Anfragekennung wurde bereits verwendet.')
            require_access(previous.run_id, credential)
            return job_view(previous)
        initial_state = None
        payload = {}
        if body.kind in {'research_refresh','research_repair'}:
            if not body.run_id or body.review or body.selection or body.parent_id or body.icp_id or body.expected_revision is not None or body.png_requested:
                raise HTTPException(422,'Ungültige Rechercheoptionen.')
            parent=get_state(body.run_id)
            try: RunAccess(app.state.auth.store).manage(parent, credential)
            except AccessRevoked: raise HTTPException(403, 'Only the owner or administrator can start linked research.') from None
            if body.kind=='research_repair' and not parent.research_snapshot.get('completed'):
                raise HTTPException(409,'Für die kostenfreie Belegprüfung ist eine abgeschlossene Recherche erforderlich.')
            if any(j.status in {'queued','running','paused'} for j in workflow.storage.list_jobs(str(parent.run_id))):
                raise HTTPException(409,'Vorhandene Aufgabe zuerst prüfen; keine parallele Recherche.')
            for candidate in workflow.storage.list_runs():
                if candidate.parent_run_id==parent.run_id and any(j.status in {'queued','running','paused'} for j in workflow.storage.list_jobs(str(candidate.run_id))):
                    raise HTTPException(409,'Eine verknüpfte Recherche ist bereits aktiv oder unklar.')
            initial_state=RunState(icp=parent.icp,parent_run_id=parent.run_id,owner_user_id=parent.owner_user_id,decision_workflow_version=1,
                internal_link_policy=parent.internal_link_policy,research_scope=parent.research_scope,
                campaign_id=parent.campaign_id,campaign_budget_usd=parent.campaign_budget_usd,
                run_budget_usd=min(body.research_budget_usd or parent.run_budget_usd or 1,settings.max_run_usd))
            run_id=initial_state.run_id
            payload={'budget_usd':0 if body.kind=='research_repair' else initial_state.run_budget_usd,
                     'parent_run_id':str(parent.run_id)}
        elif body.kind == 'start':
            if body.run_id or body.review or body.parent_id or body.png_requested or not body.icp_id:
                raise HTTPException(422, 'Ungültige Laufoptionen.')
            if not re.fullmatch(r'[a-z0-9_]+', body.icp_id):
                raise HTTPException(422, 'Ungültige Zielgruppen-ID.')
            path = next((settings.input_dir/'icps'/f'{body.icp_id}{suffix}' for suffix in ('.json','.yaml','.yml')
                         if (settings.input_dir/'icps'/f'{body.icp_id}{suffix}').is_file()), None)
            if path is None:
                raise HTTPException(404, 'Zielgruppe nicht gefunden.')
            icp = load_icp(path)
            if settings.demo_mode and icp.id != 'icp_1_buy_and_hold':
                raise HTTPException(422, 'Diese Zielgruppe ist im Demo-Modus nicht verfügbar.')
            if body.seed_topic:
                if settings.demo_mode and body.seed_topic != icp.seed_topic:
                    raise HTTPException(422, 'Die Offline-Demo verwendet Immobiliensteuerberater als Hauptthema.')
                from src.security.sanitize import sanitize_text
                icp.seed_topic = sanitize_text(body.seed_topic, 120)
            initial_state = RunState(icp=icp, owner_user_id=credential['id'], run_budget_usd=body.run_budget_usd or min(settings.max_run_usd,1), research_scope=body.research_scope, internal_link_policy=body.internal_link_policy or 'strict', decision_workflow_version=int(settings.seo_decisions_enabled))
            initial_state.campaign_id=initial_state.run_id
            initial_state.campaign_budget_usd=initial_state.run_budget_usd
            run_id = initial_state.run_id
        else:
            if not body.run_id or body.icp_id or body.seed_topic:
                raise HTTPException(422, 'Lauf-ID erforderlich.')
            run_id = body.run_id
            state = get_state(run_id)
            if any(item.status in {'queued', 'running'} for item in workflow.storage.list_jobs(str(run_id))):
                raise HTTPException(409, 'Für diesen Lauf wird bereits eine Aufgabe verarbeitet.')
            if body.kind == 'selection':
                if not body.selection or body.review or body.parent_id or body.png_requested:
                    raise HTTPException(422, 'Ungültige Auswahloptionen.')
                try:
                    validate_selection(state, body.selection)
                except ValueError:
                    raise HTTPException(409, 'Auswahl ist veraltet oder Hinweise wurden nicht bestätigt.')
                event = SelectionEvent(**body.selection.model_dump(), reviewer_name=reviewer,
                                       reviewer_id=credential['id'], event_id=str(uuid4()))
                payload = {'event': event.model_dump(mode='json')}
            elif body.kind == 'quality_checks':
                if any(item.status == 'paused' for item in workflow.storage.list_jobs(str(run_id))):
                    raise HTTPException(409, 'Der Ausgang einer Anfrage ist unklar. Bitte prüfen Sie zuerst die pausierte Aufgabe.')
                if (body.review or body.parent_id or body.png_requested or body.expected_revision is None or
                        state.revision != body.expected_revision or not state.article or not state.briefing or
                        state.status not in {'failed', 'draft_created', 'brand_check_passed'}):
                    raise HTTPException(409, 'Der gespeicherte Entwurf ist nicht für eine erneute Prüfung bereit. Bitte aktualisieren Sie den Lauf.')
                from src.services.guided_qa import readiness
                qa_ready=readiness(workflow.storage,state,settings)
                if not qa_ready['ready']:
                    raise HTTPException(409,qa_ready['reason'])
                payload = {'source_revision': state.revision}
            elif body.kind == 'review':
                if not body.review or body.parent_id or body.png_requested:
                    raise HTTPException(422, 'Ungültige Prüfoptionen.')
                failed_correction = (body.review.action == 'needs_revision' and state.status == 'failed' and
                    state.article and state.html and any(check and not check.passed for check in (state.brand_check, state.seo_check)))
                if (state.status != 'seo_check_passed' and not failed_correction) or state.revision != body.review.expected_revision:
                    raise HTTPException(409, 'Der Entwurf ist nicht mehr zur Prüfung bereit.')
                if body.review.action == 'needs_revision':
                    if state.human_revisions >= settings.max_human_revisions:
                        raise HTTPException(409, 'The explicit correction limit has been reached. Saved work remains available.')
                    if any(item.status == 'paused' for item in workflow.storage.list_jobs(str(run_id))):
                        raise HTTPException(409, 'Inspect the uncertain provider request before requesting a paid correction.')
                event = ReviewEvent(**body.review.model_dump(), reviewer_name=reviewer, reviewer_id=credential['id'], event_id=str(uuid4()))
                try: workflow._require_review(state, event)
                except PermissionError: raise HTTPException(403, 'Final approval remains restricted to Alex or Sam.') from None
                except ValueError: raise HTTPException(409, 'Complete all current professional and infographic review requirements first.') from None
                payload = {'event': event.model_dump(mode='json')}
            elif body.kind == 'evaluation':
                if (not body.confirm_evaluation or not body.evaluation_plan_id or body.expected_revision!=state.revision or
                        body.review or body.parent_id or body.png_requested or body.selection):
                    raise HTTPException(409,'Open the current evaluation estimate and explicitly confirm that plan first.')
                from src.services.evaluation import preflight as evaluation_preflight
                report=evaluation_preflight(settings,workflow.storage,state)
                if report['plan_id']!=body.evaluation_plan_id or not report['ready']:
                    raise HTTPException(409,'The evaluation plan changed or is blocked. Open a fresh estimate; no request was sent.')
                payload={'source_revision':state.revision,'evaluation_plan':report}
            elif body.kind == 'evaluation_images':
                if (not body.confirm_evaluation or not body.evaluation_plan_id or body.expected_revision!=state.revision or
                        body.review or body.parent_id or body.png_requested or body.selection):
                    raise HTTPException(409,'Open and explicitly confirm the current image plan first.')
                from src.services.evaluation_images import preflight as image_preflight
                report=image_preflight(settings,workflow.storage,state)
                if report['plan_id']!=body.evaluation_plan_id or not report['ready']:
                    raise HTTPException(409,report['reason'] or 'The image plan changed. Open a fresh estimate.')
                payload={'source_revision':state.revision,'image_plan':report}
            elif body.kind == 'ai_visibility':
                if body.review or body.parent_id or body.png_requested or body.selection:
                    raise HTTPException(422,'AI observation jobs do not accept editorial or image options.')
                if settings.demo_mode:
                    raise HTTPException(409,'Live AI observations are disabled in demo mode. Imported synthetic observations remain labelled.')
                payload={'source_revision':state.revision}
            elif body.kind == 'social':
                if body.review or body.parent_id or body.png_requested or state.status not in {'seo_check_passed', 'exported'}:
                    raise HTTPException(409, 'Social-Media-Entwürfe sind für diesen Lauf nicht verfügbar.')
                payload = {'source_revision': state.revision}
            elif body.kind in {'image_set', 'image_set_variation'}:
                if body.review or not settings.image_generation_enabled or state.status not in {'seo_check_passed', 'exported'}:
                    raise HTTPException(409, 'Bildgenerierung ist für diesen Lauf nicht verfügbar.')
                options = image_generation_options(settings)
                if not options['configured']:
                    raise HTTPException(409, options['reason'])
                try:
                    ImageSetService(settings, workflow.storage)._sources(state)
                except ValueError:
                    raise HTTPException(409, 'Bitte geben Sie den Artikel frei, exportieren Sie ihn lokal und erstellen Sie beide aktuellen Social-Media-Entwürfe.') from None
                if body.kind == 'image_set_variation':
                    if not body.parent_id:
                        raise HTTPException(422, 'Bildserie erforderlich.')
                    parent = workflow.storage.get_asset(str(body.parent_id), 'master')
                    if not parent or parent.run_id != run_id or parent.source_revision != state.revision:
                        raise HTTPException(409, 'Bildserie ist veraltet.')
                    payload = {'parent_id': str(body.parent_id), 'png_requested': parent.png_requested,
                               'source_revision': state.revision}
                elif body.parent_id:
                    raise HTTPException(422, 'Ungültige Bildoptionen.')
                else:
                    payload = {'png_requested': body.png_requested, 'source_revision': state.revision}
            elif body.kind == 'single_image_variation':
                if body.review or body.png_requested or not body.parent_id or not settings.image_generation_enabled:
                    raise HTTPException(422, 'Ungültige Bildoptionen.')
                parent = workflow.storage.get_asset(str(body.parent_id), 'image')
                if not parent or parent.run_id != run_id or parent.source_revision != state.revision:
                    raise HTTPException(409, 'Bildversion ist veraltet.')
                payload = {'parent_id': str(body.parent_id), 'source_revision': state.revision}
        job = Job(run_id=run_id, kind=body.kind, idempotency_key=body.idempotency_key,
                  reviewer=reviewer, reviewer_id=credential['id'], request_fingerprint=fingerprint, payload=payload,
                  source_revision=initial_state.revision if initial_state else get_state(run_id).revision)
        try:return job_view(workflow.storage.create_job(job, initial_state))
        except ValueError:raise HTTPException(409,'Eine verknüpfte Aufgabe ist bereits aktiv oder unklar.') from None

    @app.get('/jobs', dependencies=[Depends(token)])
    def jobs(credential: dict=Depends(token)):
        access = RunAccess(app.state.auth.store)
        visible = {str(s.run_id) for s in app.state.workflow.storage.list_runs() if access.can_read(s, credential)}
        return [job_view(item) for item in app.state.workflow.storage.list_jobs() if str(item.run_id) in visible]

    @app.get('/jobs/{job_id}', dependencies=[Depends(token)])
    def job_status(job_id: UUID):
        job = app.state.workflow.storage.get_job(str(job_id))
        if not job:
            raise HTTPException(404, 'Aufgabe nicht gefunden.')
        return job_view(job)

    @app.get('/runs/{run_id}/diagnostics')
    def run_diagnostics(run_id: UUID, credential: dict=Depends(token)):
        if credential['role'] != 'admin':
            raise HTTPException(403, 'Administrator access is required for technical diagnostics.')
        return {'run_id':str(run_id),'events':app.state.workflow.storage.list_run_failures(str(run_id))}

    @app.get('/jobs/{job_id}/diagnostics')
    def job_diagnostics(job_id: UUID, credential: dict=Depends(token)):
        if credential['role'] != 'admin':
            raise HTTPException(403, 'Administrator access is required for technical diagnostics.')
        job=app.state.workflow.storage.get_job(str(job_id))
        if not job:raise HTTPException(404, 'Saved item not found.')
        return {'job_id':str(job.id),'events':app.state.workflow.storage.list_failures(str(job.id))}

    @app.post('/jobs/{job_id}/retry', dependencies=[Depends(token)])
    def retry_job(job_id: UUID):
        storage = app.state.workflow.storage
        job = storage.get_job(str(job_id))
        if not job:
            raise HTTPException(404, 'Aufgabe nicht gefunden.')
        try: authorize_job(job, storage)
        except AccessRevoked: raise HTTPException(403, 'The original actor must be active and retain access before retrying this task.') from None
        if job.status not in {'paused', 'failed'}:
            raise HTTPException(409, 'Diese Aufgabe kann derzeit nicht erneut gestartet werden.')
        if job.kind=='evaluation':
            raise HTTPException(409,'Evaluation retries cannot authorize another purchase. Inspect saved results and verify a repair, then explicitly confirm a fresh evaluation plan.')
        if job.error_code in {'quality_failed', 'quality_evidence'}:
            raise HTTPException(409, 'The draft is saved. Request a specific correction or rerun only quality checks; do not restart the original drafting task.')
        try:
            return job_view(storage.retry_job(str(job.id)))
        except ValueError:
            raise HTTPException(409, 'Diese Aufgabe kann derzeit nicht erneut gestartet werden.') from None
    return app

app=create_app()
