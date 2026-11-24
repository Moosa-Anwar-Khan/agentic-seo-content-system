"""Disposable solo evaluation browser journey. All providers are fake; network is local only."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest  # strips credentials and blocks non-loopback network
import io,json,os,subprocess,tempfile,threading,time,zipfile
from cryptography.fernet import Fernet
import httpx,uvicorn,pytest
from playwright.sync_api import sync_playwright,expect
from src.config.settings import Settings
from src.api.app import create_app
from tests.test_solo_evaluation import fixture,provider

ART=ROOT/'var/assessment/solo-evaluation';ART.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='browser-',dir=ART));patch=pytest.MonkeyPatch()
settings=Settings(_env_file=None,demo_mode=True,combined_mode=True,seo_decisions_enabled=True,
 data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),auth_public_origin='http://127.0.0.1:3111')
calls=provider(patch)
app=create_app(settings)
server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8021,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
node=ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node';build=Path(os.environ.get('PORTAL_FRONTEND_DIR',str(ART/'build/frontend')))
log=(work/'frontend.log').open('w');front=subprocess.Popen([str(node),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3111'],cwd=build,env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8021','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
checks={};errors=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(150):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Disposable servers did not start')
 w=app.state.workflow;member=app.state.auth.store.create_user('solo@example.invalid','Solo tester','member','offline-password-123')
 s=fixture(settings,w,vision=True);s.owner_user_id=member['id'];w.storage.save_run_state(s)
 if os.environ.get('VERIFY_EVALUATION_IMAGES'):
  settings.image_generation_enabled=True;settings.image_generation_provider='openai';settings.image_generation_model='gpt-image-2.5-flare';settings.image_request_max_usd=.25;settings.image_quality='medium'
  caps=json.loads(settings.ai_model_capabilities);caps['openai/gpt-image-2.5-flare']={'image_generation':True};settings.ai_model_capabilities=json.dumps(caps)
  from src.services.images import OpenAIImageProvider
  from PIL import Image
  image_calls=[]
  def fake_image(self,prompt,size):
   image_calls.append(size);buffer=io.BytesIO();Image.new('RGB',size,'#1f5c99').save(buffer,'PNG');return buffer.getvalue(),.01
  patch.setattr(OpenAIImageProvider,'generate',fake_image)
 before=s.model_dump(exclude={'tokens_used','updated_at'})
 session,csrf=app.state.auth.session(member['id'])
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True,args=['--no-first-run'])
  context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
  context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('http://127.0.0.1:3111/','data:','blob:')) else route.abort())
  context.add_cookies([{'name':'review_session','value':session,'url':settings.auth_public_origin},{'name':'review_csrf','value':csrf,'url':settings.auth_public_origin}])
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.set_default_timeout(15000)
  page.goto(settings.auth_public_origin);page.evaluate('(args)=>localStorage.setItem("muster:"+args[0]+":last-run",args[1])',[member['id'],str(s.run_id)]);page.reload()
  panel=page.locator('#solo-evaluation');panel.get_by_role('button',name='Evaluate outputs — Free to open',exact=True).click()
  expect(panel.get_by_text('Review estimate',exact=True)).to_be_visible()
  expect(panel.get_by_text('Article QA:',exact=False)).to_contain_text('compatible checks reused')
  run=panel.get_by_role('button',name='Run confirmed evaluation · May incur a charge',exact=True)
  expect(run).to_be_disabled();assert calls==[];checks['free_open_and_confirmation_required']=True
  panel.screenshot(path=str(ART/'solo-desktop-estimate.png'))
  panel.get_by_role('checkbox',name='I confirm only this displayed',exact=False).check();run.click()
  for _ in range(120):
   jobs=w.storage.list_jobs(str(s.run_id))
   if jobs and jobs[0].status in {'completed','failed','paused'}:break
   time.sleep(.2)
  assert jobs[0].status=='completed',jobs[0].error
  if os.environ.get('VERIFY_ASSISTANT_REVIEW'):
   from tests.test_assistant_review import record
   record(settings,w.storage,s)
  panel.get_by_role('button',name='Refresh evaluation results · Free',exact=True).click()
  if os.environ.get('VERIFY_ASSISTANT_REVIEW'):
   expect(panel.get_by_role('heading',name='Independent image review',exact=True)).to_be_visible()
   expect(panel.get_by_text('The configured API image check remains pending.',exact=False)).to_be_visible()
   before_open=len(calls)
   with page.expect_popup() as popup_event:
    panel.get_by_role('button',name='Open all outputs and review · Free',exact=True).click()
   popup=popup_event.value;popup.wait_for_load_state();expect(popup.get_by_text('Independent assistant review; unapproved.',exact=True)).to_be_visible();popup.close()
   assert len(calls)==before_open
   checks['independent_observation_and_private_combined_view']=True
  expect(panel.get_by_text('Saved evaluation:',exact=False)).to_contain_text('evaluated unapproved')
  expect(panel.get_by_role('heading',name='LinkedIn preview — unapproved')).to_be_visible()
  panel.get_by_role('button',name='View German article and infographic · Free',exact=True).click()
  expect(panel.locator('iframe')).to_be_visible()
  expect(panel.locator('figure img').first).to_be_visible()
  page.wait_for_function('()=>{const img=document.querySelector("#solo-evaluation img");return img && img.complete && img.naturalWidth===1400;}',timeout=15000)
  checks['member_without_adviser_completes_fake_evaluation']=True
  if os.environ.get('VERIFY_EVALUATION_IMAGES'):
   images=page.get_by_role('region',name='Test images and export',exact=True)
   images.get_by_role('button',name='Refresh saved images · Free',exact=True).click()
   generate=images.get_by_role('button',name='Generate platform images · May incur a charge',exact=True)
   expect(generate).to_be_disabled();assert not image_calls
   images.get_by_role('checkbox').check();generate.click()
   for _ in range(150):
    image_jobs=[j for j in w.storage.list_jobs(str(s.run_id)) if j.kind=='evaluation_images']
    if image_jobs and image_jobs[0].status in {'completed','failed','paused'}:break
    time.sleep(.2)
   assert image_jobs[0].status=='completed',image_jobs[0].error
   images.get_by_role('button',name='Refresh saved images · Free',exact=True).click()
   expect(images.get_by_text('Three test images saved — unapproved.',exact=True)).to_be_visible()
   expect(images.locator('img')).to_have_count(3)
   assert len(image_calls)==1 and len(calls)==3
   checks['one_confirmed_fake_master_three_private_images']=True

  with page.expect_download() as event:panel.get_by_role('button',name='Download evaluation package — unapproved · Free',exact=True).click()
  download=event.value;data=Path(download.path()).read_bytes();z=zipfile.ZipFile(io.BytesIO(data))
  assert 'linkedin-evaluation.txt' in z.namelist() and 'infographic-unapproved.webp' in z.namelist()
  if os.environ.get('VERIFY_EVALUATION_IMAGES'):
   assert len([n for n in z.namelist() if n.startswith('channel-images/')])==4 and 'START_HERE.html' in z.namelist()
   checks['test_export_contains_three_images_and_start_page']=True
  assert 'unapproved' in z.read('README.txt').decode();checks['partial_and_complete_downloads_labelled_unapproved']=True
  panel.screenshot(path=str(ART/'solo-desktop-outputs.png'))
  page.set_viewport_size({'width':390,'height':844});panel.scroll_into_view_if_needed();page.wait_for_timeout(500)
  if not page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'):
   print('Viewport',page.evaluate('({inner:innerWidth,root:document.documentElement.scrollWidth,body:document.body.scrollWidth,x:scrollX})'));print('Overflow elements',page.evaluate('()=>Array.from(document.querySelectorAll("body *")).filter(e=>e.scrollWidth>e.clientWidth+1).slice(0,25).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent?.slice(0,70),width:e.getBoundingClientRect().width,scroll:e.scrollWidth,overflow:getComputedStyle(e).overflowX,right:e.getBoundingClientRect().right}))'))
   page.screenshot(path=str(ART/'overflow.png'))
   raise AssertionError('Mobile horizontal overflow')
  page.screenshot(path=str(ART/'solo-mobile.png'));checks['mobile_layout_and_navigation']=True
  previous=len(calls);panel.get_by_role('button',name='Refresh evaluation results · Free',exact=True).click();expect(panel.get_by_text('Evaluation checks reused:',exact=False)).to_contain_text('3')
  assert len(calls)==previous==3;checks['refresh_reuses_without_new_calls']=True
  assert w.get(str(s.run_id)).model_dump(exclude={'tokens_used','updated_at'})==before
  assert not w.storage.list_assets(str(s.run_id),'post') and not w.get(str(s.run_id)).professional_review
  checks['normal_release_and_social_assets_unchanged']=True
  response=context.request.get(settings.auth_public_origin+'/api/jobs/'+str(jobs[0].id)+'/diagnostics');assert response.status==403
  checks['diagnostics_administrator_only']=True
  assert not errors,errors;checks['no_browser_errors_or_real_provider_calls']=True
  context.close();browser.close()
 (ART/'browser-results.json').write_text(json.dumps(checks,indent=2));print(json.dumps(checks,indent=2))
finally:
 front.terminate()
 try:front.wait(timeout=10)
 except subprocess.TimeoutExpired:front.kill()
 log.close();server.should_exit=True;thread.join(timeout=10);patch.undo()
