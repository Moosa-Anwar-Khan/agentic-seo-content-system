"""Disposable guided-workflow browser checks; external network and providers blocked."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest
import json,os,subprocess,tempfile,threading,time
from uuid import uuid4
import httpx,uvicorn
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright,expect
from src.api.app import create_app
from src.config.settings import Settings
from src.schemas.content import CheckResult,ReviewFinding
from src.schemas.jobs import Job
from src.services.failures import capture
from src.services.quality import QualityReviewError
from src.services.llm import ModelRouter
from src.services.qa_units import plan,persist,schema_for
from src.services.progress import progress
from src.combined.editorial import render
from src.combined.models import EditorialOutput
from tests.test_pipeline_stabilization import prepared

ART=ROOT/'var/assessment/guided-portal';ART.mkdir(parents=True,exist_ok=True)
(ART/'browser-results.json').unlink(missing_ok=True)
work=Path(tempfile.mkdtemp(prefix='browser-',dir=ART))
settings=Settings(_env_file=None,demo_mode=True,combined_mode=True,seo_decisions_enabled=True,
 data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),auth_public_origin='http://127.0.0.1:3109')
ModelRouter.generate=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Paid model calls are disabled in this verification'))
app=create_app(settings);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8019,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
node=ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node'
build=Path(os.environ.get('PORTAL_FRONTEND_DIR',str(ART/'build/frontend'))).resolve()
log=(work/'frontend.log').open('w')
front=subprocess.Popen([str(node),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3109'],cwd=build,env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8019','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
checks={};errors=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(150):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Disposable servers did not start')
 w=app.state.workflow
 admin=app.state.auth.store.create_user('guided-admin@example.invalid','Sam','admin','offline-password-123')
 member=app.state.auth.store.create_user('guided-member@example.invalid','Member','member','offline-password-123')
 states={}
 for kind in ['suggestion','correction','review_error']:
  settings.demo_mode=True
  s=prepared(w,settings);s.owner_user_id=member['id'] if kind=='review_error' else admin['id'];s.seo_check=None;s.status='brand_check_passed'
  s.editorial['modules'][0]['markdown']+='\n\nSie prüfen diesen synthetischen Beispielsatz.'
  s.html=render(EditorialOutput.model_validate(s.editorial))
  f=ReviewFinding(module_id='L01',quote='Sie prüfen diesen synthetischen Beispielsatz.',
      severity='suggestion' if kind=='suggestion' else 'blocking',category='suggestion' if kind=='suggestion' else 'content_defect',
      rule='editorial' if kind=='suggestion' else 'required_module',message_en='Optional wording improvement.' if kind=='suggestion' else 'A synthetic essential reader outcome needs correction.',action_en='Keep the wording or make a free local edit.' if kind=='suggestion' else 'Correct the essential reader outcome using verified evidence.')
  if kind!='review_error':s.seo_check=CheckResult(passed=kind=='suggestion',findings=[f],errors=[] if kind=='suggestion' else ['Synthetischer belegter Mangel.'])
  if kind=='suggestion':s.status='seo_check_passed'
  s.release_blockers=['Geprüfte Hauptinfografik für diese Revision fehlt.']
  w.graph.update_state(w._config(str(s.run_id)),{'run':s.model_dump(mode='json')},as_node='seo_qa_agent')
  w.storage.save_run_state(s)
  if kind=='review_error':
   job=Job(run_id=s.run_id,kind='quality_checks',reviewer='Sam',reviewer_id=admin['id'],idempotency_key=uuid4(),status='failed',source_revision=s.revision,result_revision=s.revision,error_code='quality_evidence',attempt_id=str(uuid4()))
   w.storage.create_job(job)
   event=capture(w.storage,code='quality_evidence',exc=QualityReviewError('missing_contradiction_pair'),job=job,state=s,component='seo_qa',operation='Review page')
   job.failure_summary=event['summary'];w.storage.save_job(job)
  states[kind]=s
 settings.demo_mode=True
 session,csrf=app.state.auth.session(admin['id'])
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,args=['--no-first-run'])
  context=browser.new_context(viewport={'width':1440,'height':1000})
  context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('http://127.0.0.1:3109/','blob:','data:')) else route.abort())
  context.add_cookies([{'name':'review_session','value':session,'url':settings.auth_public_origin},{'name':'review_csrf','value':csrf,'url':settings.auth_public_origin}])
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.set_default_timeout(12000)
  page.goto(settings.auth_public_origin)
  def open_run(s):
   page.evaluate('([u,r])=>localStorage.setItem("muster:"+u+":last-run",r)',[admin['id'],str(s.run_id)])
   page.reload();expect(page.get_by_role('navigation',name='Workflow stages',exact=True)).to_be_visible()
  for kind,s in states.items():
   open_run(s)
   assert page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button').count()==8
   card=page.locator('.qa-'+kind).first;expect(card).to_be_visible()
   if kind!='review_error':
    card.get_by_role('button',name='Show in draft').click()
    frame=page.frame_locator('iframe[title="German article preview"]')
    # The draft fallback stays visible during the authenticated preview probe
    # and iframe navigation; wait for both read-only requests to complete.
    expect(frame.locator('mark.qa-'+kind)).to_have_count(1,timeout=15000)
    assert page.locator('iframe[title="German article preview"]').get_attribute('sandbox')==''
   if kind=='review_error':
    expect(page.get_by_text('Draft unchanged; valid QA still required',exact=False)).to_be_visible()
    page.get_by_text('Review details and debugging reference',exact=True).click()
    page.get_by_role('button',name='Technical details',exact=True).click()
    expect(page.get_by_role('button',name='Copy diagnostic report',exact=True)).to_be_visible()
    assert 'missing_contradiction_pair' in page.locator('.failure-details pre').inner_text()
   for width,label in [(1440,'desktop'),(390,'mobile')]:
    page.set_viewport_size({'width':width,'height':1000 if width==1440 else 844})
    page.locator('.guided-workflow').scroll_into_view_if_needed();page.screenshot(path=str(ART/(kind+'-'+label+'.png')))
    card.scroll_into_view_if_needed();card.screenshot(path=str(ART/(kind+'-notice-'+label+'.png')))
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),kind+' horizontal overflow'
   checks[kind+'_colour_labels_navigation_and_revision']=True
  open_run(states['suggestion'])
  page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Research',exact=False).focus()
  page.keyboard.press('Tab')
  expect(page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Topic selection',exact=False)).to_be_focused()
  checks['keyboard_step_navigation']=True
  page.route('**/review-preview?*',lambda route:route.abort())
  page.locator('.qa-suggestion').first.get_by_role('button',name='Show in draft').click()
  expect(page.get_by_text('Review highlights could not load.',exact=False)).to_be_visible()
  expect(page.frame_locator('iframe[title="German article preview"]').get_by_text('Sie prüfen diesen synthetischen Beispielsatz.',exact=True)).to_be_visible()
  checks['optional_preview_failure_keeps_german_draft']=True
  page.unroute('**/review-preview?*')
  # Suggestions do not prevent moving to infographic; no paid task is submitted.
  open_run(states['suggestion']);page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Infographic',exact=False).click()
  expect(page.locator('#guided-infographic')).to_be_visible();checks['yellow_allows_next_stage']=True
  # Exercise an actual upload only in the disposable test run. Live runs are never attached here.
  from PIL import Image
  from io import BytesIO
  image_bytes=BytesIO();Image.new('RGB',(1400,1000),'white').save(image_bytes,format='WEBP')
  before_upload=w.storage.research_costs(str(states['suggestion'].run_id))
  page.locator('#infographic-file').set_input_files({'name':'test-infographic.webp','mimeType':'image/webp','buffer':image_bytes.getvalue()})
  expect(page.get_by_text('Infographic saved for revision',exact=False)).to_be_visible(timeout=20000)
  expect(page.locator('.infographic-preview')).to_be_visible()
  assert page.locator('.infographic-preview').evaluate('(img)=>img.complete&&img.naturalWidth===1400')
  uploaded=w.get(str(states['suggestion'].run_id));assert uploaded.graphic_revision==uploaded.revision and uploaded.professional_review is None
  assert w.storage.research_costs(str(uploaded.run_id))==before_upload
  checks['upload_shows_saved_revision_and_preview_without_charge']=True
  open_run(states['suggestion'])
  page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Infographic',exact=False).click()
  expect(page.get_by_text('Infographic saved for revision',exact=False)).to_be_visible()
  expect(page.locator('.infographic-preview')).to_be_visible()
  checks['saved_infographic_survives_refresh']=True
  # A rejected replacement must retain the already saved image and state.
  unchanged=w.storage.get_run_state(str(uploaded.run_id)).model_dump_json()
  with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/graphic')) as rejected_upload:
   page.locator('#infographic-file').set_input_files({'name':'bad.webp','mimeType':'image/webp','buffer':b'invalid image'})
  assert rejected_upload.value.status==409
  error_title=rejected_upload.value.json()['user_error']['title']
  expect(page.get_by_role('heading',name=error_title,exact=True)).to_be_visible(timeout=15000)
  expect(page.locator('.infographic-preview')).to_be_visible()
  assert unchanged==w.storage.get_run_state(str(uploaded.run_id)).model_dump_json()
  checks['failed_replacement_preserves_saved_image']=True
  for width,label in [(1440,'desktop'),(390,'mobile')]:
   page.set_viewport_size({'width':width,'height':1000 if width==1440 else 844})
   page.locator('#guided-infographic').screenshot(path=str(ART/('infographic-saved-'+label+'.png')))
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  checks['infographic_desktop_mobile_preview']=True
  open_run(states['correction'])
  page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Final approval',exact=False).click()
  expect(page.get_by_role('button',name='Approve and export locally',exact=False)).to_be_disabled();checks['red_blocks_final_approval']=True
  # Local edit is explicit; attempting to navigate away can be cancelled.
  page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Draft',exact=False).click()
  page.get_by_text('Edit saved German draft — no AI charges',exact=True).click()
  editor=page.locator('form').filter(has=page.get_by_role('button',name='Save changes — Free'))
  editor.locator('details').first.locator('summary').click()
  field=editor.locator('textarea').first;field.fill(field.input_value()+'\nEine lokale Präzisierung.')
  expect(page.get_by_text('Unsaved changes — save explicitly before leaving.',exact=True)).to_be_visible()
  page.once('dialog',lambda dialog:dialog.dismiss())
  page.get_by_role('navigation',name='Workflow stages',exact=True).get_by_role('button',name='Infographic',exact=False).click()
  expect(field).to_be_visible();checks['cancel_navigation_preserves_unsaved_edit']=True
  before=w.storage.research_costs(str(states['correction'].run_id))
  editor.get_by_role('button',name='Save changes — Free').click()
  expect(page.get_by_text('Changes saved locally as revision',exact=False)).to_be_visible()
  page.get_by_text('Edit saved German draft — no AI charges',exact=True).click()
  expect(page.get_by_text('No unsaved changes.',exact=True)).to_be_visible()
  assert w.get(str(states['correction'].run_id)).revision==states['correction'].revision+1
  assert w.storage.research_costs(str(states['correction'].run_id))==before
  checks['explicit_local_save_has_no_provider_charge']=True
  # A member cannot access administrator diagnostics, even with a guessed job ID.
  member_session,member_csrf=app.state.auth.session(member['id'])
  failed=w.storage.list_jobs(str(states['review_error'].run_id))[0]
  member_context=browser.new_context()
  member_context.add_cookies([{'name':'review_session','value':member_session,'url':settings.auth_public_origin},{'name':'review_csrf','value':member_csrf,'url':settings.auth_public_origin}])
  response=member_context.request.get(settings.auth_public_origin+'/api/jobs/'+str(failed.id)+'/diagnostics')
  assert response.status==403,str(response.status)
  member_context.close();checks['diagnostics_admin_only']=True
  assert not errors,errors
  assert not any(w.storage.list_ai_calls(str(s.run_id)) for s in states.values())
  checks['zero_model_calls_and_no_browser_errors']=True
  context.close();browser.close()
 (ART/'browser-results.json').write_text(json.dumps(checks,indent=2))
 print(json.dumps(checks,indent=2))
finally:
 front.terminate()
 try:front.wait(timeout=10)
 except subprocess.TimeoutExpired:front.kill()
 log.close();server.should_exit=True;thread.join(timeout=10)
