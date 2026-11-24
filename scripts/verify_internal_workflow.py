"""Real internal-review HTTP/browser journey, disposable data and fake providers."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest  # Removes provider keys and blocks external networking.
import os,json,io,subprocess,tempfile,threading,time,zipfile
import httpx,uvicorn,pytest
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright,expect
from src.config.settings import Settings
from src.api.app import create_app
from tests.test_solo_evaluation import fixture,provider

ART=ROOT/'outputs/management/Internal_Workflow_2026-10-23';ART.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='internal-workflow-'));patch=pytest.MonkeyPatch()
settings=Settings(_env_file=None,demo_mode=True,combined_mode=True,seo_decisions_enabled=True,data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),auth_public_origin='http://127.0.0.1:3129')
calls=provider(patch);app=create_app(settings)
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8029,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
build=Path(os.environ.get('PORTAL_FRONTEND_DIR',str(ROOT/'.assessment-tools/internal-workflow-build/frontend')))
log=(work/'frontend.log').open('w')
front=subprocess.Popen([str(ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node'),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3129'],cwd=build,env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8029','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
views=[];errors=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(150):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Disposable servers unavailable')
 w=app.state.workflow;app.state.worker.close()
 actor=app.state.auth.store.create_user('internal-owner@example.invalid','Internal owner','member','offline-password-123')
 s=fixture(settings,w);s.owner_user_id=actor['id'];w.storage.save_run_state(s)
 original=s.model_copy(deep=True);costs=w.storage.research_costs(str(s.run_id));jobs=w.storage.list_jobs(str(s.run_id))
 session,csrf=app.state.auth.session(actor['id']);origin=settings.auth_public_origin
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
  ctx=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1000})
  ctx.route('**/*',lambda r:r.continue_() if r.request.url.startswith((origin+'/','data:','blob:')) else r.abort())
  ctx.add_cookies([{'name':'review_session','value':session,'url':origin},{'name':'review_csrf','value':csrf,'url':origin}])
  page=ctx.new_page();page.set_default_timeout(25000);page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(origin);page.get_by_role('button',name=str(s.run_id)[:8],exact=False).click()
  panel=page.get_by_role('region',name='Real internal review and export')
  panel.get_by_label('Use actual owner-led internal review and export for this run, professionally unapproved.',exact=True).check()
  panel.get_by_role('button',name='Use internal-review workflow — Free',exact=True).click()
  panel.get_by_label('I have inspected the article for internal editorial review.',exact=True).check()
  panel.get_by_label('I have inspected the source references and their stated limitations; I am not providing professional tax attestation.',exact=True).check()
  panel.get_by_label('I have inspected the attached infographic.',exact=True).check()
  panel.get_by_label('I acknowledge all outstanding release requirements and the professionally unapproved status.',exact=True).check()
  panel.get_by_label('Internal review note',exact=True).fill('Inspected the synthetic article, references and infographic for internal use. Release requirements remain unresolved.')
  gap=panel.locator('.internal-actions').first.evaluate('(e)=>e.getBoundingClientRect().top-e.previousElementSibling.getBoundingClientRect().bottom');assert gap>=24
  panel.get_by_role('button',name='Record my internal review — Free',exact=True).click()
  expect(panel.get_by_role('heading',name='7. Final internal approval',exact=True)).to_be_visible()
  page.reload();page.get_by_role('button',name=str(s.run_id)[:8],exact=False).click()
  expect(panel.get_by_role('heading',name='7. Final internal approval',exact=True)).to_be_visible()
  panel.get_by_label('I approve this revision for internal use and internal export only.',exact=True).check()
  panel.get_by_label('I understand that tax-adviser approval, outstanding company inputs and publication approval remain separate.',exact=True).check()
  panel.get_by_role('button',name='Record my final internal approval — Free',exact=True).click()
  panel.get_by_role('button',name='Create internally reviewed export — Free',exact=True).click()
  link=panel.get_by_role('link',name='Download internally reviewed package — professionally unapproved · Free',exact=True)
  expect(link).to_be_visible()
  for width,label in [(1440,'desktop'),(768,'tablet'),(390,'mobile')]:
   page.set_viewport_size({'width':width,'height':1000});panel.scroll_into_view_if_needed()
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
   with page.expect_download() as event:link.click()
   file=ART/('synthetic-internal-export-'+label+'.zip');event.value.save_as(file)
   with zipfile.ZipFile(file) as z:
    assert z.testzip() is None
    r=json.loads(z.read('internal-approval.json'));assert r['professional_approval'] is False and r['publication_approved'] is False
    assert r['records']['review']['actor_id']==actor['id']
   panel.screenshot(path=str(ART/('internal-complete-'+label+'.png')))
   report=ctx.request.get(origin+'/api/runs/'+str(s.run_id)+'/progress').json()
   assert all(x['status']=='complete' for x in report['workflow']['steps'])
   views.append({'width':width,'all_eight_internal_steps_complete':True,'zip_valid':True,'initial_review_action_gap':gap})
  panel.evaluate("e=>e.style.fontSize='200%'");assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  page.reload();page.get_by_role('button',name=str(s.run_id)[:8],exact=False).click();expect(link).to_be_visible()
  browser.close()
 current=w.get(str(s.run_id));assert current.professional_review is None and current.status!='exported' and not current.publication
 assert current.revision==original.revision and current.editorial==original.editorial and current.seo_check==original.seo_check
 assert costs==w.storage.research_costs(str(s.run_id)) and jobs==w.storage.list_jobs(str(s.run_id)) and not calls and not errors
 assert len(current.internal_workflow['events'])==4
 result={'views':views,'durable_real_internal_records':4,'reload_preserves_review_and_export':True,'professional_approval_created':False,'ordinary_approved_export_created':False,'provider_calls':0,'publication':False,'browser_errors':errors}
 (ART/'browser-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
finally:
 front.terminate();front.wait(timeout=20);server.should_exit=True;thread.join(timeout=20);patch.undo();log.close()
