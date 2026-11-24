"""Disposable browser exercise of visibility collection, imports and failures."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest
import json,os,subprocess,tempfile,threading,time
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from cryptography.fernet import Fernet
import httpx,uvicorn
from playwright.sync_api import sync_playwright,expect
from src.config.settings import Settings
from src.api.app import create_app
from src.orchestrator.state import RunState
from src.schemas.icp import icp_1_buy_and_hold
from src.integrations.dataforseo import DataForSEO
from src.services.research_budget import micros
from pytest import MonkeyPatch

ART=ROOT/'var/assessment/visibility-browser';ART.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='session-',dir=ART));patch=MonkeyPatch();calls=[]
settings=Settings(_env_file=None,demo_mode=False,combined_mode=True,max_run_usd=5,
 data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),auth_public_origin='http://127.0.0.1:3112',
 dataforseo_login='synthetic-only',dataforseo_password='synthetic-only')
app=create_app(settings)
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8022,access_log=False,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
node=ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node'
frontdir=Path(os.environ['PORTAL_FRONTEND_DIR']);log=(work/'next.log').open('w')
front=subprocess.Popen([str(node),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3112'],cwd=frontdir,env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8022','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
checks={};errors=[];writes=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(100):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Disposable servers did not start')
 store=app.state.workflow.storage
 user=app.state.auth.store.create_user('visibility@example.invalid','Sam','reviewer','synthetic-password-123')
 state=RunState(icp=icp_1_buy_and_hold,owner_user_id=user['id'],status='awaiting_selection',run_budget_usd=5,research_scope='site')
 state.campaign_id=state.run_id;state.campaign_budget_usd=5;store.save_run_state(state)
 def lookup(self,route,payload):
  calls.append((route,payload));reservation=store.reserve_research_cost(str(state.run_id),100000,5000000)
  time.sleep(.15);store.settle_research_cost(reservation,1000)
  return [{'sections':[{'text':'Synthetischer Browsertest: Muster und Partner.','annotations':[{'direct_url':'https://steuerkanzlei.example/'}]}]}]
 patch.setattr(DataForSEO,'_lookup',lookup)
 patch.setattr('src.services.tool_health.check_accounts',lambda *args:[{'tool':'dataforseo','status':'account_verified'}])
 session,csrf=app.state.auth.session(user['id'])
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args=['--no-first-run'])
  context=browser.new_context(viewport={'width':1440,'height':1050},accept_downloads=True)
  context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('http://127.0.0.1:3112/','blob:','data:')) else route.abort())
  context.add_cookies([{'name':'review_session','value':session,'url':settings.auth_public_origin},{'name':'review_csrf','value':csrf,'url':settings.auth_public_origin}])
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
  page.goto(settings.auth_public_origin+'/visibility')
  expect(page.get_by_role('heading',name='AI visibility & tool status',exact=True)).to_be_visible()
  expect(page.get_by_role('heading',name='German baseline queries',exact=True)).to_be_visible()
  expect(page.get_by_role('heading',name='Website content & identity checks',exact=True)).to_be_visible()
  expect(page.get_by_text('Uncollected structured data remains unknown.',exact=False)).to_be_visible()
  assert page.locator('ol li').count()==12 and not calls and not writes
  checks['viewing_baseline_and_tool_health_never_purchases_work']=True
  button=page.get_by_role('button',name='Collect four Gemini API observations',exact=True)
  page.once('dialog',lambda d:d.dismiss());button.click();assert not calls and not writes
  checks['cancelled_paid_confirmation_is_free']=True
  page.once('dialog',lambda d:d.accept());button.evaluate('(button)=>{button.click();button.click()}')
  expect(page.get_by_text('Synthetischer Browsertest: Muster und Partner.',exact=True)).to_have_count(4,timeout=30000)
  assert len(calls)==4 and len([w for w in writes if w.endswith('/jobs')])==1
  expect(page.get_by_text('Controlled model API; not consumer-app ranking',exact=True)).to_be_visible()
  expect(page.get_by_text('Recorded: $0.0040',exact=False)).to_be_visible()
  checks['one_click_one_durable_job_four_labelled_observations']=True
  page.get_by_role('button',name='Refresh saved evidence',exact=True).click();assert len(calls)==4
  checks['refresh_does_not_repeat_paid_collection']=True
  entry={'platform':'claude','query':'Welche Steuerberatung betreut Unternehmen?','model':'Synthetic consumer observation','search_mode':'web_search','evidence_type':'consumer_interface',
   'observed_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),'excerpt':'Synthetischer öffentlicher Vergleich ohne Markennennung.','cited_urls':[],'provenance':'Synthetic browser evidence only'}
  page.get_by_role('textbox',name='Import an observation',exact=True).fill(json.dumps(entry))
  page.get_by_role('button',name='Save observation',exact=True).click()
  expect(page.get_by_text('Synthetischer öffentlicher Vergleich ohne Markennennung.',exact=True)).to_have_count(1)
  assert len(calls)==4
  checks['manual_import_is_separate_and_free']=True
  with page.expect_download() as download:page.get_by_role('button',name='Download evidence CSV',exact=True).click()
  download.value.save_as(str(ART/'synthetic-evidence.csv'))
  with page.expect_download() as download:page.get_by_role('button',name='Download English report',exact=True).click()
  download.value.save_as(str(ART/'synthetic-progress.pdf'))
  checks['english_pdf_and_csv_downloads_make_no_provider_calls']=len(calls)==4
  page.screenshot(path=str(ART/'visibility-desktop.png'),full_page=True)
  page.route('**/api/runs/*/visibility',lambda route:route.abort())
  page.get_by_role('button',name='Refresh saved evidence',exact=True).click()
  expect(page.get_by_role('heading',name='Connection interrupted',exact=True)).to_be_visible()
  expect(page.get_by_role('heading',name='Saved observations',exact=True)).to_be_visible()
  page.unroute('**/api/runs/*/visibility');page.get_by_role('button',name='Refresh saved evidence',exact=True).click()
  expect(page.get_by_role('heading',name='Connection interrupted',exact=True)).to_have_count(0)
  checks['disconnect_retains_results_and_reconnect_never_retries']=len(calls)==4
  page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.get_by_role('button',name='Menu',exact=True).click();page.keyboard.press('Escape')
  expect(page.get_by_role('button',name='Menu',exact=True)).to_have_attribute('aria-expanded','false')
  page.screenshot(path=str(ART/'visibility-mobile.png'),full_page=True)
  checks['mobile_no_overflow_and_keyboard_navigation']=True
  context.clear_cookies();page.get_by_role('button',name='Refresh saved evidence',exact=True).click()
  expect(page.get_by_role('heading',name='Please sign in again',exact=True)).to_be_visible()
  expect(page.get_by_role('heading',name='Saved observations',exact=True)).to_have_count(0)
  checks['session_expiry_clears_private_observations']=True
  assert not errors,errors
  checks['no_browser_runtime_errors']=True
  browser.close()
 (ART/'results.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2))
finally:
 front.terminate()
 try:front.wait(timeout=15)
 except subprocess.TimeoutExpired:front.kill();front.wait()
 server.should_exit=True;thread.join(timeout=15);log.close();patch.undo()
