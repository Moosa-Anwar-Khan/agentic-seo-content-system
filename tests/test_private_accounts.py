"""Registration, bootstrap and private durable history, using disposable accounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.orchestrator.state import RunState
from src.orchestrator.workflow import Workflow
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.security.auth import AuthService, digest, totp_code
from src.security.run_access import AccessRevoked, RunAccess, authorize_job
from src.services.recovery import begin_paid, running_job
from src.storage.sqlite_storage import SQLiteStorage

PASSWORD = 'disposable-strong-password-123'


def account(auth, name, role='member', pending=False):
    return auth.store.create_user(name.lower()+'@example.org', name, role, PASSWORD, pending=pending, chosen_password=True)


def use(client, user):
    session, csrf = client.app.state.auth.session(user['id'])
    client.cookies.clear()
    client.cookies.set('review_session',session)
    client.cookies.set('review_csrf',csrf)
    return {'X-CSRF-Token':csrf}


def setup_mfa(client, result):
    challenge=result['challenge']
    setup=client.post('/auth/setup/mfa',json={'challenge':challenge}).json()
    response=client.post('/auth/setup/confirm',json={'challenge':challenge,'code':totp_code(setup['secret'])})
    assert response.status_code==200
    return response.json(),setup['secret']


def test_frontend_registration_approval_mfa_logout_and_persistent_history(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        auth=client.app.state.auth
        assert client.get('/auth/bootstrap/status').json()=={'setup_available':True}
        code=auth.prepare_bootstrap()
        body={'name':'Morgan','email':'admin@example.org','password':PASSWORD,'password_confirmation':PASSWORD,'setup_code':code}
        response=client.post('/auth/bootstrap',json=body)
        assert response.status_code==200 and response.json()['stage']=='setup_mfa'
        admin,admin_secret=setup_mfa(client,response.json())
        assert admin['role']=='admin' and admin['account_status']=='active'
        assert client.get('/auth/bootstrap/status').json()=={'setup_available':False}
        assert code not in client.get('/auth/bootstrap/status').text
        assert client.post('/auth/bootstrap',json=body).status_code==400
        csrf={'X-CSRF-Token':admin['csrf_token']}
        registration={'name':'Alice','email':'alice@example.org','password':PASSWORD,'password_confirmation':PASSWORD}
        response=client.post('/auth/register',json=registration)
        assert response.status_code==202 and response.json()['status']=='pending'
        pending=auth.store.user('email','alice@example.org')
        assert pending['role']=='member' and pending['enabled']==0 and pending['must_change_password']==0
        denied=client.post('/auth/login',json={'email':'alice@example.org','password':PASSWORD})
        assert denied.status_code==403 and denied.json()['user_error']['code']=='pending_account'
        fake_session,_=auth.session(pending['id'])
        assert auth.identify(fake_session) is None
        approve={'admin_password':PASSWORD,'admin_code':totp_code(admin_secret)}
        assert client.post('/admin/users/'+pending['id']+'/approve',headers=csrf,json=approve).status_code==200
        assert client.post('/admin/users/'+pending['id']+'/approve',headers=csrf,json=approve).status_code==409
        assert client.post('/session/logout',headers=csrf).status_code==200
        login=client.post('/auth/login',json={'email':'alice@example.org','password':PASSWORD}).json()
        assert login['stage']=='setup_mfa'
        signed,_=setup_mfa(client,login)
        csrf={'X-CSRF-Token':signed['csrf_token']}
        run=client.post('/runs',headers=csrf,json={}).json()
        run_id=run['run_id']
        assert run['owner_user_id']==signed['user_id']
        assert run['status']=='seo_check_passed' and 'Immobiliensteuerberater' in run['html']
        # A member cannot become the final reviewer through ownership.
        assert client.post('/runs/'+run_id+'/reviews',headers=csrf,json={'action':'approved','expected_revision':run['revision']}).status_code==403
        assert client.post('/session/logout',headers=csrf).status_code==200
        assert client.get('/runs/'+run_id).status_code==401
        login=client.post('/auth/login',json={'email':'alice@example.org','password':PASSWORD}).json()
        signed_again=client.post('/auth/verify',json={'challenge':login['challenge'],'code':signed['recovery_codes'][0]}).json()
        assert signed_again['user_id']==signed['user_id']
        assert client.get('/runs?scope=mine').json()[0]['run_id']==run_id
    with TestClient(create_app(settings)) as reopened:
        reopened.app.state.worker.close()
        assert reopened.get('/auth/bootstrap/status').json()['setup_available'] is False
        auth=reopened.app.state.auth
        alice=auth.store.user('email','alice@example.org')
        use(reopened,alice)
        assert reopened.get('/runs/'+run_id).json()['owner_user_id']==alice['id']
        assert reopened.get('/runs/'+run_id+'/bundle').status_code==200
        assert auth.store.user('email','admin@example.org')['totp_secret']


@pytest.mark.parametrize('name',['Alex','Sam','  alex ','Ｓａｍ','ALEX'])
def test_self_registration_cannot_claim_final_approval_identity(settings,name):
    with TestClient(create_app(settings)) as client:
        body={'name':name,'email':'person@example.org','password':PASSWORD,'password_confirmation':PASSWORD}
        assert client.post('/auth/register',json=body).status_code==422
        assert client.app.state.auth.store.users()==[]


def test_registration_validation_redacts_passwords_and_blocks_role_injection(settings):
    with TestClient(create_app(settings)) as client:
        body={'name':'Alice','email':'alice@example.org','password':PASSWORD,'password_confirmation':'different-password-123'}
        response=client.post('/auth/register',json=body)
        assert response.status_code==422
        assert PASSWORD not in response.text and 'different-password' not in response.text
        body.update(password_confirmation=PASSWORD,role='admin')
        assert client.post('/auth/register',json=body).status_code==422
        assert client.app.state.auth.store.users()==[]
        body.pop('role')
        for _ in range(5):client.post('/auth/register',json=body)
        assert client.post('/auth/register',json={**body,'email':'another@example.org'}).status_code==429


def test_first_admin_expiry_throttle_restart_and_atomic_race(settings):
    storage=SQLiteStorage(settings.data_dir/'bootstrap.sqlite')
    auth=AuthService(storage,settings.auth_encryption_key.get_secret_value())
    old=auth.prepare_bootstrap()
    assert auth._bootstrap_digest==digest(old)
    assert old not in repr(auth.__dict__)
    auth._bootstrap_expires=datetime.now(timezone.utc)-timedelta(seconds=1)
    with pytest.raises(PermissionError):auth.bootstrap_from_browser(old,'one@example.org','Morgan',PASSWORD)
    new=auth.prepare_bootstrap()
    for _ in range(4):
        with pytest.raises(PermissionError):auth.bootstrap_from_browser('wrong','one@example.org','Morgan',PASSWORD)
    with pytest.raises(PermissionError):auth.bootstrap_from_browser(new,'one@example.org','Morgan',PASSWORD)
    # A restart changes the code but does not reset the persistent attempt limit.
    restarted=AuthService(storage,settings.auth_encryption_key.get_secret_value())
    code=restarted.prepare_bootstrap()
    assert code!=new
    with pytest.raises(PermissionError):restarted.bootstrap_from_browser(code,'one@example.org','Morgan',PASSWORD)
    storage_path=settings.data_dir/'race.sqlite'
    a=AuthService(SQLiteStorage(storage_path),settings.auth_encryption_key.get_secret_value())
    b=AuthService(SQLiteStorage(storage_path),settings.auth_encryption_key.get_secret_value())
    codes=[a.prepare_bootstrap(),b.prepare_bootstrap()]
    def claim(index):
        try:return [a,b][index].bootstrap_from_browser(codes[index],f'admin{index}@example.org','Morgan',PASSWORD)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,[0,1]))
    assert sum(r is not None for r in results)==1
    assert a.store.count_admins()==1
    assert AuthService(SQLiteStorage(storage_path),settings.auth_encryption_key.get_secret_value()).prepare_bootstrap() is None
    # The winner's setup code was consumed, even before MFA enrollment.
    winner=next(i for i,r in enumerate(results) if r)
    with pytest.raises(PermissionError):[a,b][winner].bootstrap_from_browser(codes[winner],'again@example.org','Other',PASSWORD)


def test_rejected_registration_cannot_be_enabled_or_issued_a_session(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth
        user=account(auth,'Alice',pending=True)
        auth.store.decide_registration(user['id'],False,'fixture-administrator')
        with pytest.raises(ValueError):auth.store.set_enabled(user['id'],True)
        assert auth.identify(auth.session(user['id'])[0]) is None
        assert client.post('/auth/login',json={'email':user['email'],'password':PASSWORD}).status_code==401


def test_two_user_isolation_all_routes_jobs_assets_costs_and_legacy_assignment(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth;workflow=client.app.state.workflow
        alice,bob=account(auth,'Alice'),account(auth,'Bob')
        admin=account(auth,'Morgan','admin')
        headers=use(client,alice)
        run=client.post('/runs',headers=headers,json={}).json();run_id=run['run_id']
        queued=client.post('/jobs',headers=headers,json={'kind':'social','run_id':run_id,'idempotency_key':str(uuid4())}).json()
        legacy=workflow.start(icp_1_buy_and_hold)
        assert client.get('/runs/'+str(legacy.run_id)).status_code==404
        assert len(client.get('/runs').json())==1
        headers=use(client,bob)
        assert client.get('/runs').json()==[] and client.get('/jobs').json()==[]
        for suffix in ['', '/article','/topic-map','/bundle','/release','/dashboard','/analysis-report.pdf','/ai-calls','/images','/image-sets','/social-posts','/sharing',f'/images/{uuid4()}/file']:
            response=client.get('/runs/'+run_id+suffix)
            assert response.status_code==404,(suffix,response.text)
            assert 'Immobilien' not in response.text and alice['id'] not in response.text
        for suffix in ['/selection','/reviews','/professional-reviews','/graphic','/social-posts','/image-sets','/images','/sharing','/owner','/publication','/outcomes',f'/images/{uuid4()}/reviews',f'/image-sets/{uuid4()}/variations']:
            assert client.post('/runs/'+run_id+suffix,headers=headers,json={}).status_code==404,suffix
        assert client.get('/jobs/'+queued['id']).status_code==404
        assert client.post('/jobs/'+queued['id']+'/retry',headers=headers).status_code==404
        assert client.post('/jobs',headers=headers,json={'kind':'social','run_id':run_id,'idempotency_key':str(uuid4())}).status_code==404
        assert client.get('/runs?scope=all').status_code==403
        assert 'run_id' not in client.get('/budget').text
        use(client,admin)
        assert len(client.get('/runs?scope=all').json())==2
        assert client.get('/runs/'+str(legacy.run_id)).status_code==200
        # Explicit admin assignment survives the older ownerless checkpoint.
        access=RunAccess(auth.store);access.assign(legacy,bob,admin)
        assert workflow.get(str(legacy.run_id)).owner_user_id==bob['id']
        use(client,bob)
        assert client.get('/runs?scope=mine').json()[0]['run_id']==str(legacy.run_id)
        use(client,alice)
        assert client.get('/runs/'+str(legacy.run_id)).status_code==404
        assert client.post('/runs',headers={'X-CSRF-Token':client.cookies.get('review_csrf')},json={'owner_user_id':bob['id']}).status_code==422


def test_sharing_restrictions_revocation_and_queued_actor_activation(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth;workflow=client.app.state.workflow
        alice,bob=account(auth,'Alice'),account(auth,'Bob')
        alex=account(auth,'Alex','reviewer');adviser=account(auth,'Anna','tax_adviser')
        pending=account(auth,'Pending',pending=True)
        headers=use(client,alice)
        run=client.post('/runs',headers=headers,json={}).json();run_id=run['run_id']
        for target in [bob,pending]:
            assert client.post('/runs/'+run_id+'/sharing',headers=headers,json={'user_id':target['id']}).status_code==422
        for target in [alex,adviser]:
            assert client.post('/runs/'+run_id+'/sharing',headers=headers,json={'user_id':target['id']}).status_code==200
        adviser_headers=use(client,adviser)
        assert client.post('/runs/'+run_id+'/graphic',headers=adviser_headers,json={}).status_code==403
        assert client.post('/jobs',headers=adviser_headers,json={'kind':'quality_checks','run_id':run_id,'expected_revision':run['revision'],'idempotency_key':str(uuid4())}).status_code==403
        headers=use(client,alex)
        assert client.get('/runs?scope=mine').json()==[]
        assert client.get('/runs?scope=shared').json()[0]['run_id']==run_id
        assert client.get('/runs/'+run_id+'/article').status_code==200
        assert client.post('/runs/'+run_id+'/sharing',headers=headers,json={'user_id':bob['id']}).status_code==403
        payload={'kind':'review','run_id':run_id,'idempotency_key':str(uuid4()),'review':{'action':'needs_revision','feedback_notes':'Bitte ergänzen Sie die Checkliste für den Immobilienkauf.','expected_revision':run['revision']}}
        job=client.post('/jobs',headers=headers,json=payload)
        assert job.status_code==202,job.text
        headers=use(client,alice)
        assert client.post('/runs/'+run_id+'/sharing',headers=headers,json={'user_id':alex['id'],'enabled':False}).status_code==200
        assert client.app.state.worker.process_one()
        stored=workflow.storage.get_job(job.json()['id'])
        assert stored.status=='failed' and stored.error_code=='access_revoked'
        assert workflow.get(run_id).revision==run['revision']
        headers=use(client,alex)
        assert client.get('/runs/'+run_id).status_code==404
        # A disabled owner cannot run a job; logout alone does not cancel one.
        headers=use(client,alice)
        queued=client.post('/jobs',headers=headers,json={'kind':'social','run_id':run_id,'idempotency_key':str(uuid4())}).json()
        assert client.post('/session/logout',headers=headers).status_code==200
        assert client.app.state.worker.process_one()
        assert workflow.storage.get_job(queued['id']).status=='completed'
        use(client,alice)
        state=workflow.get(run_id)
        durable=Job(run_id=state.run_id,reviewer='Alice',reviewer_id=alice['id'],kind='social',idempotency_key=uuid4())
        workflow.storage.create_job(durable)
        auth.store.set_enabled(alice['id'],False)
        assert client.app.state.worker.process_one()
        assert workflow.storage.get_job(str(durable.id)).error_code=='access_revoked'
        use(client,adviser)
        assert client.get('/jobs/'+str(durable.id)).status_code==200
        assert client.post('/jobs/'+str(durable.id)+'/retry',headers={'X-CSRF-Token':client.cookies.get('review_csrf')}).status_code==403


def test_owner_can_select_and_request_corrections_but_never_final_approve(settings):
    settings.seo_decisions_enabled=True
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth
        alice=account(auth,'Alice');headers=use(client,alice)
        run=client.post('/runs',headers=headers,json={}).json()
        from tests.test_seo_decisions import choice
        state=client.app.state.workflow.get(run['run_id'])
        selected=client.post('/jobs',headers=headers,json={'kind':'selection','run_id':run['run_id'],'selection':choice(state).model_dump(mode='json'),'idempotency_key':str(uuid4())})
        assert selected.status_code==202,selected.text
        assert client.app.state.worker.process_one()
        result=client.get('/runs/'+run['run_id']).json()
        assert result['status']=='seo_check_passed' and result['owner_user_id']==alice['id']
        revision={'kind':'review','run_id':run['run_id'],'review':{'action':'needs_revision','feedback_notes':'Bitte ergänzen Sie eine Checkliste für Vermieter.','expected_revision':result['revision']},'idempotency_key':str(uuid4())}
        first=client.post('/jobs',headers=headers,json=revision)
        assert first.status_code==202 and client.post('/jobs',headers=headers,json=revision).json()['id']==first.json()['id']
        assert client.app.state.worker.process_one()
        final=client.get('/runs/'+run['run_id']).json()
        assert final['owner_user_id']==alice['id'] and final['revision']==result['revision']+1
        assert client.post('/jobs',headers=headers,json={**revision,'idempotency_key':str(uuid4()),'review':{'action':'approved','expected_revision':final['revision']}}).status_code==403


def test_paid_boundary_revocation_preserves_inflight_result_and_blocks_next_request(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth;storage=client.app.state.workflow.storage
        alice=account(auth,'Alice');alex=account(auth,'Alex','reviewer')
        state=RunState(icp=icp_1_buy_and_hold,owner_user_id=alice['id']);storage.save_run_state(state)
        access=RunAccess(auth.store);access.grant(state,alex,alice,True)
        job=Job(run_id=state.run_id,reviewer='Alex',reviewer_id=alex['id'],kind='social',idempotency_key=uuid4())
        with running_job(job,storage):
            begin_paid('first',storage,str(state.run_id))
            # Request was already submitted; revocation must preserve its response.
            access.grant(state,alex,alice,False)
            storage.finish_operation('first',{'result':'saved German response'})
            with pytest.raises(AccessRevoked):begin_paid('second',storage,str(state.run_id))
        assert storage.get_operation('first')['status']=='completed'
        assert storage.get_operation('second') is None
        assert storage.get_run_state(str(state.run_id)).owner_user_id==alice['id']


def test_linked_research_inherits_owner_without_shares_and_restart_keeps_failed_job(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth;workflow=client.app.state.workflow
        alice=account(auth,'Alice');alex=account(auth,'Alex','reviewer')
        headers=use(client,alice)
        state=RunState(icp=icp_1_buy_and_hold,owner_user_id=alice['id']);workflow.storage.save_run_state(state)
        RunAccess(auth.store).grant(state,alex,alice,True)
        payload={'kind':'research_refresh','run_id':str(state.run_id),'idempotency_key':str(uuid4())}
        job=client.post('/jobs',headers=headers,json=payload).json()
        child=workflow.get(job['run_id'])
        assert child.owner_user_id==alice['id'] and child.parent_run_id==state.run_id
        assert RunAccess(auth.store).shares(child)==[]
        use(client,alex)
        assert client.get('/runs/'+str(child.run_id)).status_code==404
        stored=workflow.storage.get_job(job['id']);stored.status='failed';stored.error_code='processing';workflow.storage.save_job(stored)
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        alice=client.app.state.auth.store.user('email','alice@example.org');headers=use(client,alice)
        assert client.get('/runs/'+job['run_id']).json()['owner_user_id']==alice['id']
        assert client.get('/jobs/'+job['id']).json()['status']=='failed'
        assert client.post('/jobs/'+job['id']+'/retry',headers=headers).status_code==200


def test_additive_account_migration_preserves_existing_identity_state_session_and_ledger(settings):
    from src.security.auth import hash_password
    storage=SQLiteStorage(settings.data_dir/'old.sqlite')
    state=RunState(icp=icp_1_buy_and_hold)
    storage.save_run_state(state)
    reservation=storage.reserve_research_cost(str(state.run_id),10000,1000000)
    user_id=str(uuid4());encoded=hash_password(PASSWORD)
    with storage.connect() as db:
        db.execute('CREATE TABLE users (id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE,name TEXT NOT NULL,role TEXT NOT NULL,password_hash TEXT NOT NULL,totp_secret TEXT,last_totp_step BIGINT NOT NULL DEFAULT -1,must_change_password INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1)')
        db.execute('INSERT INTO users(id,email,name,role,password_hash) VALUES (?,?,?,?,?)',(user_id,'old@example.org','Existing','reviewer',encoded))
    auth=AuthService(storage,settings.auth_encryption_key.get_secret_value())
    session,csrf=auth.session(user_id)
    reopened=AuthService(storage,settings.auth_encryption_key.get_secret_value())
    assert reopened.identify(session,csrf)['id']==user_id
    assert reopened.store.user('id',user_id)['password_hash']==encoded
    assert reopened.store.user('id',user_id)['registration_status']=='active'
    assert storage.get_run_state(str(state.run_id)).owner_user_id is None
    assert storage.research_costs(str(state.run_id))['reserved_microusd']==10000
    assert reservation


def test_idempotent_start_does_not_leak_after_transfer_and_inherited_costs_need_parent_access(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth;workflow=client.app.state.workflow
        alice=account(auth,'Alice');bob=account(auth,'Bob');admin=account(auth,'Morgan','admin')
        headers=use(client,alice)
        payload={'kind':'start','icp_id':'icp_1_buy_and_hold','idempotency_key':str(uuid4())}
        first=client.post('/jobs',headers=headers,json=payload).json()
        state=workflow.get(first['run_id'])
        access=RunAccess(auth.store);access.assign(state,bob,admin)
        assert client.post('/jobs',headers=headers,json=payload).status_code==404
        child=RunState(icp=state.icp,owner_user_id=alice['id'],parent_run_id=state.run_id,
            research_snapshot={'inherited_research_costs':{'run_id':str(state.run_id),'recorded_microusd':987654},
                'dataforseo':{'diagnostics':[{'source_run_id':str(state.run_id),'original_cost':.987654,'cost':0}]}})
        workflow.storage.save_run_state(child)
        visible=client.get('/runs/'+str(child.run_id)).json()
        assert visible['research_snapshot']['inherited_research_costs'] is None
        assert 'original_cost' not in visible['research_snapshot']['dataforseo']['diagnostics'][0]
        assert workflow.get(str(child.run_id)).research_snapshot['inherited_research_costs']['recorded_microusd']==987654
        job=Job(run_id=child.run_id,kind='research_repair',reviewer='Alice',reviewer_id=alice['id'],idempotency_key=uuid4())
        with pytest.raises(AccessRevoked):authorize_job(job,workflow.storage)


def test_first_admin_can_claim_own_pending_registration_only_with_setup_code_and_original_password(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();auth=client.app.state.auth
        registration={'name':'Alice','email':'alice@example.org','password':PASSWORD,'password_confirmation':PASSWORD}
        assert client.post('/auth/register',json=registration).status_code==202
        original=auth.store.user('email','alice@example.org')
        old_session,_=auth.session(original['id'])
        code=auth.prepare_bootstrap()
        body={**registration,'name':'Morgan','setup_code':'invalid-code'}
        assert client.post('/auth/bootstrap',json=body).status_code==400
        assert auth.store.user('id',original['id'])['registration_status']=='pending'
        wrong={**body,'setup_code':code,'password':'wrong-registration-password','password_confirmation':'wrong-registration-password'}
        response=client.post('/auth/bootstrap',json=wrong)
        assert response.status_code==409
        assert response.json()['user_error']['code']=='setup_pending_password'
        assert 'original registration password' in response.json()['user_error']['next_steps'][0]
        assert wrong['password'] not in response.text and code not in response.text
        assert auth.store.count_admins()==0
        response=client.post('/auth/bootstrap',json={**body,'setup_code':code})
        assert response.status_code==200 and response.json()['stage']=='setup_mfa'
        claimed=auth.store.user('id',original['id'])
        assert claimed['role']=='admin' and claimed['registration_status']=='active' and claimed['enabled']
        assert claimed['password_hash']==original['password_hash']
        assert len(auth.store.users())==1 and auth.identify(old_session) is None
        assert client.get('/session').status_code==401
        signed,_=setup_mfa(client,response.json())
        assert signed['user_id']==original['id'] and signed['reviewer']=='Morgan'
        assert auth.store.execute("SELECT COUNT(*) FROM access_audit WHERE action='bootstrap_pending_admin'",one=True)[0]==1
        assert client.post('/auth/bootstrap',json={**body,'setup_code':code}).status_code==400


def test_pending_claim_and_fresh_first_admin_race_create_one_administrator(settings):
    path=settings.data_dir/'pending-race.sqlite'
    a=AuthService(SQLiteStorage(path),settings.auth_encryption_key.get_secret_value())
    pending=account(a,'Alice',pending=True)
    b=AuthService(SQLiteStorage(path),settings.auth_encryption_key.get_secret_value())
    codes=[a.prepare_bootstrap(),b.prepare_bootstrap()]
    def claim(i):
        try:return [a,b][i].bootstrap_from_browser(codes[i], 'alice@example.org' if i==0 else 'fresh@example.org','Morgan',PASSWORD)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,[0,1]))
    assert sum(r is not None for r in results)==1 and a.store.count_admins()==1
    alice=a.store.user('id',pending['id'])
    assert alice['role']==('admin' if results[0] else 'member')
    assert alice['registration_status']==('active' if results[0] else 'pending')


@pytest.mark.parametrize('status',['active','rejected'])
def test_first_admin_setup_never_claims_an_active_or_rejected_account(settings,status):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth
        existing=account(auth,'Alice',pending=status=='rejected')
        if status=='rejected':auth.store.decide_registration(existing['id'],False,'fixture-admin')
        code=auth.prepare_bootstrap()
        body={'name':'Morgan','email':'alice@example.org','password':PASSWORD,'password_confirmation':PASSWORD,'setup_code':code}
        response=client.post('/auth/bootstrap',json=body)
        assert response.status_code==409 and response.json()['user_error']['code']=='setup_account'
        assert auth.store.count_admins()==0
        assert auth.store.user('id',existing['id'])['registration_status']==status


def test_valid_code_cannot_be_used_for_unlimited_pending_password_guesses(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth;account(auth,'Alice',pending=True)
        code=auth.prepare_bootstrap()
        body={'name':'Morgan','email':'alice@example.org','password':'wrong-existing-password','password_confirmation':'wrong-existing-password','setup_code':code}
        for _ in range(5):assert client.post('/auth/bootstrap',json=body).status_code==409
        body.update(password=PASSWORD,password_confirmation=PASSWORD)
        assert client.post('/auth/bootstrap',json=body).status_code==400
        assert auth.store.count_admins()==0


def test_invalid_admin_email_has_field_guidance_and_retains_setup_code(settings):
    with TestClient(create_app(settings)) as client:
        auth=client.app.state.auth;code=auth.prepare_bootstrap()
        body={'name':'Morgan','email':'person@incomplete','password':PASSWORD,'password_confirmation':PASSWORD,'setup_code':code}
        response=client.post('/auth/bootstrap',json=body)
        assert response.status_code==422 and response.json()['user_error']['code']=='setup_details'
        assert auth.store.count_admins()==0
        assert auth._bootstrap_digest==digest(code)
        body['email']='valid@example.org'
        assert client.post('/auth/bootstrap',json=body).status_code==200
