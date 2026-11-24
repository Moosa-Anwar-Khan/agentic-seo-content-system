"""Exercise failure notices/diagnostics in an isolated browser with no paid providers."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest  # Enforce offline networking before importing integrations.
import json,os,subprocess,tempfile,threading,time
from uuid import uuid4
from cryptography.fernet import Fernet
import httpx,uvicorn
from playwright.sync_api import sync_playwright,expect
from src.api.app import create_app
from src.config.settings import Settings
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.schemas.ai import AICall
from src.services.llm import ModelRequestError
from src.services.model_routes import resolve_route
from src.services.recovery import current_job

ART=ROOT/'var/assessment/failure-diagnostics-browser'
ART.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='session-',dir=ART))
settings=Settings(_env_file=None,demo_mode=True,combined_mode=False,seo_decisions_enabled=False,
 data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),
 auth_public_origin='http://127.0.0.1:3110')
app=create_app(settings)
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8020,access_log=False,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
node=ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node'
frontend=Path(os.environ.get('PORTAL_FRONTEND_DIR',str(ROOT/'frontend')))
log=(work/'frontend.log').open('w')
front=subprocess.Popen([str(node),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3110'],cwd=frontend,
 env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8020','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
checks={};errors=[];writes=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(120):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Isolated servers did not start')
 app.state.worker.close()
 auth=app.state.auth;workflow=app.state.workflow
 admin=auth.store.create_user('admin@example.invalid','Morgan','admin','disposable-strong-password-123')
 member=auth.store.create_user('alice@example.invalid','Alice','member','disposable-strong-password-123')
 state=workflow.start(icp_1_buy_and_hold,owner_user_id=member['id']);run_id=str(state.run_id)
 job=Job(run_id=state.run_id,kind='quality_checks',reviewer=member['name'],reviewer_id=member['id'],idempotency_key=uuid4())
 workflow.storage.create_job(job)
 def fail(active_job):
  call=AICall(run_id=state.run_id,job_id=str(active_job.id),attempt_id=active_job.attempt_id,task='seo_qa',
   provider='deepseek',model='deepseek-v4-flash',latency_ms=1,status='failure',finish_reason='length',
   max_output_tokens=6000,output_tokens=6000,provider_request_id='synthetic-request-001')
  workflow.storage.log_ai_call(call)
  current_job.get().details(component='seo_qa',operation='Review article unit',unit_id='claim:C04',call_id=str(call.id))
  raise ModelRequestError(resolve_route(settings,'seo_qa'),'output_limit')
 app.state.worker._execute=fail
 assert app.state.worker.process_one()
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
  def login(user,width=1440):
   session,csrf=auth.session(user['id'])
   context=browser.new_context(viewport={'width':width,'height':1050},permissions=['clipboard-read','clipboard-write'])
   context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('http://127.0.0.1:3110/','blob:','data:')) else route.abort())
   context.add_cookies([{'name':'review_session','value':session,'url':settings.auth_public_origin},{'name':'review_csrf','value':csrf,'url':settings.auth_public_origin}])
   page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
   page.goto(settings.auth_public_origin)
   page.evaluate('([user,id])=>localStorage.setItem("muster:"+user+":last-run",id)',[user['id'],run_id]);page.reload()
   return context,page
  admin_context,page=login(admin)
  current=page.get_by_role('region',name='Current progress')
  # Section has an accessible label, but is not necessarily exposed as region on every browser.
  current=page.locator('section[aria-label="Current progress"]')
  expect(current.get_by_role('heading',name='SEO QA — Step stopped',exact=True)).to_be_visible()
  expect(current.get_by_text('claim:C04',exact=False)).to_be_visible()
  expect(current.get_by_text('6000 tokens',exact=False).first).to_be_visible()
  page.get_by_role('navigation',name='Workflow stages').get_by_role('button',name='Draft & Quality Checks').click()
  expect(page.get_by_role('heading',name='German article draft',exact=True)).to_be_visible()
  checks['current_failure_names_agent_unit_and_measured_limit_without_losing_draft']=True
  page.route('**/api/jobs/*/diagnostics',lambda route:route.abort())
  current.get_by_role('button',name='Technical details',exact=True).click()
  expect(current.get_by_text('Technical details could not be loaded.',exact=False)).to_be_visible()
  expect(page.get_by_role('heading',name='German article draft',exact=True)).to_be_visible()
  page.unroute('**/api/jobs/*/diagnostics')
  current.get_by_role('button',name='Technical details',exact=True).click()
  expect(current.locator('pre')).to_contain_text('synthetic-request-001')
  checks['admin_report_via_proxy_and_failed_optional_load_preserves_draft']=True
  current.get_by_role('button',name='Copy diagnostic report',exact=True).click()
  expect(current.get_by_text('Diagnostic report copied.',exact=True)).to_be_visible()
  assert 'synthetic-request-001' in page.evaluate('navigator.clipboard.readText()')
  checks['copy_sanitized_report']=True
  current.get_by_text('Failure history',exact=True).click()
  current.get_by_role('button',name='Load failure history',exact=True).click()
  expect(current.locator('summary').filter(has_text='SEO QA ·')).to_have_count(1)
  checks['admin_can_reopen_persisted_failure_history']=True
  page.screenshot(path=str(ART/'diagnostics-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844})
  assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
  page.screenshot(path=str(ART/'diagnostics-mobile.png'),full_page=True)
  current.get_by_role('button',name='Technical details',exact=True).focus()
  page.keyboard.press('Enter')
  expect(current.get_by_role('button',name='Copy diagnostic report',exact=True)).to_have_count(0)
  checks['mobile_layout_and_keyboard_focus']=True
  member_context,member_page=login(member,390)
  expect(member_page.locator('section[aria-label="Current progress"]').get_by_role('heading',name='SEO QA — Step stopped',exact=True)).to_be_visible()
  expect(member_page.get_by_role('button',name='Technical details',exact=True)).to_have_count(0)
  denied=member_context.request.get(settings.auth_public_origin+'/api/jobs/'+str(job.id)+'/diagnostics')
  assert denied.status==403
  checks['member_sees_clear_reason_but_cannot_read_admin_report']=True
  assert not writes,writes
  assert not errors,errors
  checks['inspection_never_submits_paid_or_retry_work']=True
  member_context.close();admin_context.close();browser.close()
 print(json.dumps({'checks':checks,'passed':len(checks),'artifacts':str(ART)},indent=2))
finally:
 front.terminate()
 try:front.wait(timeout=15)
 except subprocess.TimeoutExpired:front.kill();front.wait()
 log.close();server.should_exit=True;thread.join(timeout=15)
