"""Disposable, network-blocked browser verification of the English portal."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest
import json,os,re,subprocess,threading,time,tempfile
from uuid import uuid4
from cryptography.fernet import Fernet
import httpx,uvicorn
from playwright.sync_api import sync_playwright,expect
from src.config.settings import Settings
from src.api.app import create_app
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.orchestrator.state import RunState
from src.schemas.content import SEOAnalysis, CheckResult
from src.schemas.decisions import SEORecommendation
from src.services.social import SocialService
from src.services.images import OpenAIImageProvider
from src.security.auth import totp_code
from tests.test_model_agnostic_images import MasterProvider

ART=ROOT/'var/assessment/portal-browser';ART.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='session-',dir=ART))
settings=Settings(_env_file=None,demo_mode=True,combined_mode=False,seo_decisions_enabled=False,
 data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),auth_public_origin='http://127.0.0.1:3108',
 image_generation_enabled=True,openai_api_key='fake-browser-only',image_generation_provider='openai',image_generation_model='gpt-image-2.5-flare',image_quality='medium',image_request_max_usd=.25)
provider=MasterProvider()
def fake_generate(self,prompt,size):time.sleep(.4);return provider.generate(prompt,size)
OpenAIImageProvider.generate=fake_generate
app=create_app(settings);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8018,access_log=False,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
node=ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node';log=(work/'next.log').open('w')
frontend_dir=Path(os.environ.get('PORTAL_FRONTEND_DIR',str(ROOT/'frontend'))).resolve()
front=subprocess.Popen([str(node),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3108'],cwd=frontend_dir,env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8018','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
checks={};console=[];writes=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(100):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Servers failed to start')
 workflow=app.state.workflow
 user=app.state.auth.store.create_user('portal@example.invalid','Alex','reviewer','temporary-browser-password-123')
 first=workflow.start(icp_1_buy_and_hold,owner_user_id=user['id']);SocialService(settings,workflow.storage).prepare(first)
 second=workflow.start(icp_1_buy_and_hold,owner_user_id=user['id'])
 # Combined runs have no editorial fields before article drafting. Keep this
 # research-only fixture distinct from the completed legacy/demo article fixtures.
 research=RunState(icp=icp_1_buy_and_hold,owner_user_id=user['id'],combined_version=1,
  status='awaiting_selection',analysis=SEOAnalysis(data_mode='demo',keywords=[],existing_pages=[],
   opportunities=[],gaps=[],limitations=['Synthetischer Browser-Test.'],recommendations=[
    SEORecommendation(id='browser-topic',topic_id='browser-topic',title='Unterlagen für Ihre Vermietung',
     keyword='Vermietung Unterlagen',search_intent='informational',page_type='blog',action='create_new',
     reasoning='Synthetische Empfehlung für die Prüfung der Benutzeroberfläche.',demo=True),
    SEORecommendation(id='browser-service',topic_id='browser-service',title='Beratung zur Eigentumsstruktur',
     keyword='Eigentumsstruktur Immobilien',search_intent='informational',page_type='landing_page',action='create_new',
     reasoning='Synthetische Leistungsseiten-Empfehlung für die Prüfung der Benutzeroberfläche.',demo=True),
    SEORecommendation(id='browser-blocked-profile',topic_id='browser-blocked-profile',title='Synthetische Leistungsseite ohne freigegebenes Profil',
     keyword='steuerberater einzelunternehmen',search_intent='commercial',page_type='landing_page',action='optimize',
     reasoning='Synthetischer Profil-Blocker für die Prüfung der Benutzeroberfläche.',demo=True,
     profile_ready=False,drafting_eligible=False,
     next_action_en='Approve and configure the matching audience/service facts before drafting. The research and evidence remain available.')]))
 workflow.storage.save_run_state(research)
 session,csrf=app.state.auth.session(user['id'])
 with sync_playwright() as pw:
  chrome=os.environ.get('PORTAL_CHROME_EXECUTABLE')
  if not chrome and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').exists():chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  browser=pw.chromium.launch(**({'executable_path':chrome} if chrome else {}),headless=True,args=['--no-first-run'])
  context=browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True)
  context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('http://127.0.0.1:3108/','blob:','data:')) else route.abort())
  context.add_cookies([{'name':'review_session','value':session,'url':settings.auth_public_origin},{'name':'review_csrf','value':csrf,'url':settings.auth_public_origin}])
  page=context.new_page();page.on('pageerror',lambda e:console.append(str(e)))
  page.on('request',lambda r:writes.append(r.url) if r.method=='POST' else None)
  page.goto(settings.auth_public_origin)
  page.evaluate('([user,id])=>localStorage.setItem("muster:"+user+":last-run",id)',[user['id'],str(research.run_id)]);page.reload()
  stages=page.get_by_role('navigation',name='Run stages')
  expect(page.get_by_role('heading',name='Choose an SEO recommendation',exact=True)).to_be_visible()
  expect(page.get_by_text('Unterlagen für Ihre Vermietung',exact=False)).to_be_visible()
  assert research.editorial=={}
  for label in ['Research & Topic Selection','Draft & Quality Checks','Professional Review & Approval','Social & Images','Manual Publishing']:
   stages.get_by_role('button',name=label,exact=False).click()
  stages.get_by_role('button',name='Draft & Quality Checks',exact=False).click()
  expect(page.get_by_role('heading',name='No article draft yet',exact=True)).to_be_visible()
  expect(page.get_by_text('Research is saved. Return to Research & Topic Selection',exact=False)).to_be_visible()
  assert not writes and not console
  checks['combined_research_without_editorial_renders_all_stages_without_requests']=True
  stages.get_by_role('button',name='Research & Topic Selection',exact=False).click()
  page.get_by_role('radio',name='Unterlagen für Ihre Vermietung',exact=False).check()
  expect(page.get_by_role('button',name='Create briefing and draft',exact=True)).to_be_enabled()
  blog_card=page.locator('.recommendation-card').filter(has=page.get_by_role('radio',name='Unterlagen für Ihre Vermietung',exact=False))
  service_card=page.locator('.recommendation-card').filter(has=page.get_by_role('radio',name='Beratung zur Eigentumsstruktur',exact=False))
  expect(blog_card.locator('.recommendation-type')).to_have_text('Generates: Blog article')
  expect(service_card.locator('.recommendation-type')).to_have_text('Generates: Service page')
  expect(page.locator('.selected-output')).to_contain_text('Selected draft output: Blog article')
  service_radio=page.get_by_role('radio',name='Beratung zur Eigentumsstruktur',exact=False)
  service_radio.check()
  expect(service_card).to_have_class('asset recommendation-card is-selected')
  expect(page.locator('.selected-output')).to_contain_text('Selected draft output: Service page')
  expect(page.locator('.selected-output')).to_contain_text('Saving link recommendations only does not generate a page.')
  service_card.screenshot(path=str(ART/'recommendation-type-desktop.png'))
  page.set_viewport_size({'width':390,'height':844})
  service_card.screenshot(path=str(ART/'recommendation-type-mobile.png'))
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  service_radio.focus();page.keyboard.press('ArrowUp')
  expect(page.get_by_role('radio',name='Unterlagen für Ihre Vermietung',exact=False)).to_be_checked()
  expect(page.locator('.selected-output')).to_contain_text('Selected draft output: Blog article')
  assert not writes
  checks['prominent_output_types_selection_summary_and_keyboard_switch_make_no_requests']=True
  page.get_by_role('radio',name='Synthetische Leistungsseite ohne freigegebenes Profil',exact=False).check()
  expect(page.get_by_role('button',name='Create briefing and draft',exact=True)).to_be_disabled()
  expect(page.get_by_text('Approve and configure the matching audience/service facts before drafting.',exact=False)).to_be_visible()
  assert page.get_by_role('checkbox',name='I confirm professional support',exact=False).count()==0
  assert not writes
  checks['missing_profile_blocks_drafting_without_paid_request_or_professional_override']=True
  page.set_viewport_size({'width':390,'height':844})
  page.reload();expect(page.get_by_role('heading',name='Choose an SEO recommendation',exact=True)).to_be_visible()
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(ART/'research-mobile.png'),full_page=True)
  page.set_viewport_size({'width':1440,'height':1100})
  page.screenshot(path=str(ART/'research-desktop.png'),full_page=True)
  checks['research_selection_and_mobile_restore_preserve_saved_work']=True
  page.evaluate('([user,id])=>localStorage.setItem("muster:"+user+":last-run",id)',[user['id'],str(first.run_id)]);page.reload()
  stages=page.get_by_role('navigation',name='Run stages');expect(stages).to_be_visible()
  assert page.locator('html').get_attribute('lang')=='en'
  expect(page.get_by_role('heading',name='Content workspace',exact=True)).to_be_visible()
  for label in ['Research & Topic Selection','Draft & Quality Checks','Professional Review & Approval','Social & Images','Manual Publishing']:
   stages.get_by_role('button',name=label,exact=False).click()
  assert not writes;checks['stage_navigation_submits_no_work']=True
  expect(page.get_by_role('heading',name='Local approval is required first')).to_be_visible()
  stages.get_by_role('button',name='Draft & Quality Checks',exact=False).click()
  frame=page.locator('iframe[title="German article preview"]')
  expect(frame).to_be_visible();frame.scroll_into_view_if_needed();expect(frame.content_frame.locator('body')).to_contain_text('Sie');assert 'Sie' in frame.get_attribute('srcdoc')
  checks['english_interface_german_draft_preserved']=True
  stages.get_by_role('button',name='Social & Images',exact=False).click()
  panel=page.locator('section.panel').filter(has=page.get_by_role('heading',name='Images (optional)',exact=True))
  no=panel.get_by_role('radio',name='No AI images',exact=True);yes=panel.get_by_role('radio',name='Generate AI images',exact=True)
  expect(no).to_be_checked();yes.check();assert not provider.calls
  generate=panel.get_by_role('button',name='Generate image set',exact=True);expect(generate).to_be_enabled()
  generate.evaluate('(b)=>{b.click();b.click()}');expect(generate).to_be_disabled()
  page.wait_for_function('document.querySelectorAll(".asset-image").length===3',timeout=30000)
  assert len(provider.calls)==1
  checks['explicit_generation_double_click_one_master_three_variants']=True
  no.check();expect(panel.locator('.asset-image')).to_have_count(3)
  image=panel.locator('article.asset').first
  image.get_by_role('button',name='Approve',exact=True).click();expect(image.get_by_role('button',name='Approve',exact=True)).to_be_disabled()
  with page.expect_download() as downloaded:image.get_by_role('button',name='Download image',exact=True).click()
  assert downloaded.value.suggested_filename.endswith('.webp')
  checks['approvals_downloads_preserved_when_images_skipped']=True
  stages.get_by_role('button',name='Draft & Quality Checks',exact=False).click()
  frame.scroll_into_view_if_needed();frame.screenshot(path=str(ART/'article-preview.png'));page.evaluate('scrollTo(0,0)');page.screenshot(path=str(ART/'workspace-desktop.png'),full_page=False)
  # Run switching resets the image choice and keeps the original run's assets.
  page.get_by_text('Workspace, budget & run history',exact=True).click()
  page.locator('button.run').filter(has_text=str(second.run_id)[:8]).click()
  expect(stages).to_have_attribute('data-run-id',str(second.run_id))
  stages.get_by_role('button',name='Social & Images',exact=False).click()
  expect(no).to_be_checked();yes.check();expect(panel.get_by_role('button',name='Generate image set',exact=True)).to_be_disabled()
  checks['run_switch_resets_opt_in_and_explains_missing_social']=True
  paused=Job(run_id=second.run_id,kind='social',idempotency_key=uuid4(),reviewer='Alex',reviewer_id=user['id'],status='paused',error='private diagnostic must not appear',error_code='uncertain_outcome',requires_paid_retry=True,payload={'source_revision':second.revision})
  workflow.storage.create_job(paused)
  page.reload()
  page.get_by_text('Workspace, budget & run history',exact=True).click()
  page.get_by_text('Task history and previous attempts',exact=True).click()
  expect(page.get_by_role('heading',name='Check the previous request before retrying',exact=True)).to_be_visible()
  before=len(writes)
  page.once('dialog',lambda dialog:dialog.dismiss())
  page.get_by_role('button',name='Retry task',exact=True).click()
  assert len(writes)==before and workflow.storage.get_job(str(paused.id)).status=='paused'
  checks['uncertain_paid_retry_requires_confirmation_and_cancel_submits_nothing']=True

  # Readiness crosses the real proxy and never charges the provider.
  page.goto(settings.auth_public_origin+'/system')
  expect(page.get_by_role('heading',name='Ready for local use',exact=True)).to_be_visible()
  expect(page.get_by_role('heading',name='Live validation remains separate')).to_be_visible()
  assert len(provider.calls)==1;checks['authenticated_readiness_no_generation']=True
  page.screenshot(path=str(ART/'readiness-desktop.png'),full_page=True)
  page.goto(settings.auth_public_origin+'/dashboard')
  expect(page.get_by_role('heading',name='SEO overview',exact=True)).to_be_visible()
  expect(page.get_by_text('No performance reporting period collected',exact=True)).to_be_visible()
  page.screenshot(path=str(ART/'dashboard-desktop.png'),full_page=True)
  # Preserve the loaded report when a new filter request loses connection.
  route='**/api/runs/*/dashboard?*'
  page.route(route,lambda r:r.abort())
  page.get_by_label('Country',exact=True).select_option('all')
  expect(page.get_by_text('Connection interrupted',exact=True)).to_be_visible()
  expect(page.get_by_text('Showing the last saved report.',exact=False)).to_be_visible()
  expect(page.get_by_role('heading',name='Pages with the most clicks')).to_be_visible()
  page.unroute(route);page.get_by_label('Country',exact=True).select_option('de')
  expect(page.get_by_text('Connection interrupted',exact=True)).to_have_count(0)
  checks['disconnect_preserves_report_and_reconnect_clears_notice']=True
  page.set_viewport_size({'width':390,'height':844})
  menu=page.get_by_role('button',name='Menu',exact=True);menu.click();expect(page.get_by_role('navigation',name='Main navigation')).to_be_visible()
  page.keyboard.press('Escape');expect(menu).to_have_attribute('aria-expanded','false')
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.evaluate('scrollTo(0,0)');page.screenshot(path=str(ART/'dashboard-mobile.png'),full_page=False)
  checks['mobile_menu_escape_and_no_overflow']=True
  # Expired session is an actionable English notice, not a raw browser exception.
  context.clear_cookies();page.goto(settings.auth_public_origin+'/system')
  expect(page.get_by_role('heading',name='Please sign in again',exact=True)).to_be_visible()
  checks['expired_session_english_recovery']=True
  # Verify first-use password and MFA setup using a disposable reviewer.
  page.goto(settings.auth_public_origin)
  page.get_by_label('Email address',exact=True).fill('portal@example.invalid')
  page.get_by_label('Password',exact=True).fill('temporary-browser-password-123')
  page.get_by_role('button',name='Sign in',exact=True).click()
  page.get_by_label('New password (at least 12 characters)',exact=True).fill('fresh-browser-password-456')
  page.get_by_role('button',name='Continue',exact=True).click()
  expect(page.get_by_role('heading',name='Set up authenticator',exact=True)).to_be_visible()
  expect(page.locator('section.account-panel code')).to_have_text(re.compile(r'[A-Z2-7]{32}'))
  secret=page.locator('section.account-panel code').inner_text()
  page.get_by_label('Six-digit code',exact=True).fill(totp_code(secret))
  page.get_by_role('button',name='Confirm setup',exact=True).click()
  expect(page.get_by_role('heading',name='Recovery codes',exact=True)).to_be_visible()
  checks['english_password_mfa_and_recovery_flow']=True
  # A retained failed draft must show its actual blockers and offer a separate,
  # confirmed correction, rather than asking the operator to restart research.
  failed=first.model_copy(deep=True)
  failed.run_id=uuid4();failed.status='failed';failed.combined_version=1
  failed.brand_check=CheckResult(passed=False,errors=[
   'Hauptkeyword fehlt in mindestens zwei H2.', 'Behauptung gehört nicht zum Entwurf: browser-claim'])
  failed.seo_check=None;failed.error=None
  failed.release_blockers=['Freigegebene Preise fehlen.']
  failed.professional_review=None;failed.graphic_file=''
  workflow.storage.save_run_state(failed)
  workflow.storage.create_job(Job(run_id=failed.run_id,kind='selection',idempotency_key=uuid4(),
   reviewer='Alex',reviewer_id=user['id'],status='failed',error='saved quality failure',error_code='quality_failed'))
  page.evaluate('([user,id])=>localStorage.setItem("muster:"+user+":last-run",id)',[user['id'],str(failed.run_id)])
  page.reload()
  page.get_by_text('Workspace, budget & run history',exact=True).click()
  page.locator('button.run').filter(has_text=str(failed.run_id)[:8]).click()
  expect(page.get_by_role('heading',name='Content quality',exact=True)).to_be_visible()
  quality_panel=page.locator('section[aria-label="Current progress"]')
  expect(quality_panel.get_by_text('At least two H2 headings',exact=False)).to_be_visible()
  expect(quality_panel.get_by_role('heading',name='Requirements before release',exact=True)).to_be_visible()
  expect(quality_panel.get_by_text('Approved pricing information is missing.',exact=False)).to_be_visible()
  review=page.locator('section.panel').filter(has=page.get_by_role('heading',name='Review',exact=True))
  expect(review).to_be_visible()
  request=review.get_by_role('button',name='Request revision',exact=True)
  expect(request).to_be_disabled()
  review.get_by_label('Specific feedback',exact=True).fill('Bitte korrigieren Sie die Struktur des gespeicherten Entwurfs.')
  expect(request).to_be_enabled()
  expect(review.get_by_role('button',name='Approve and export locally',exact=True)).to_be_disabled()
  page.set_viewport_size({'width':1440,'height':1100})
  page.screenshot(path=str(ART/'quality-correction-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844})
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(ART/'quality-correction-mobile.png'),full_page=True)
  checks['saved_failed_draft_shows_english_findings_and_keeps_approval_blocked']=True
  before=len(writes)
  page.once('dialog',lambda dialog:dialog.dismiss());request.click()
  assert len(writes)==before
  page.once('dialog',lambda dialog:dialog.accept());request.click()
  expect(page.get_by_role('navigation',name='Run stages').get_by_role('button',name='Professional Review & Approval',exact=False)).to_have_attribute('aria-current','step',timeout=30000)
  actual=workflow.get(str(failed.run_id))
  assert actual.revision==failed.revision+1 and actual.human_revisions==failed.human_revisions+1
  assert len([url for url in writes[before:] if url.endswith('/api/jobs')])==1
  assert actual.analysis==failed.analysis and not actual.professional_review
  assert not (settings.output_dir/'runs'/str(failed.run_id)/'approved').exists()
  checks['failed_draft_correction_cancel_is_free_and_confirm_queues_one_revision']=True
  page.get_by_role('navigation',name='Run stages').get_by_role('button',name='Draft & Quality Checks',exact=False).click()
  editor=page.locator('details.panel').filter(has=page.locator('summary').filter(has_text='Edit saved German draft — no AI charges'))
  editor.locator('summary').first.click()
  editor.get_by_text('Section L01',exact=True).click()
  field=editor.get_by_label('German Markdown L01',exact=True)
  field.fill(field.input_value()+'\n\nSie bereiten Ihre Fragen für das persönliche Gespräch vor.')
  field.focus()
  page.screenshot(path=str(ART/'local-editor-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844})
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(ART/'local-editor-mobile.png'),full_page=True)
  page.set_viewport_size({'width':1440,'height':1100})
  before_edit=workflow.get(str(failed.run_id));before_posts=len(writes)
  editor.get_by_role('button',name='Save edited draft — no AI charges',exact=True).evaluate('(button)=>{button.click();button.click()}')
  page.wait_for_function('!document.querySelector(".workspace [role=status]")?.textContent?.includes("Processing your request")')
  expect(page.get_by_role('navigation',name='Run stages')).to_have_attribute('data-run-id',str(failed.run_id))
  for _ in range(100):
   edited=workflow.get(str(failed.run_id))
   if edited.revision==before_edit.revision+1:break
   page.wait_for_timeout(100)
  assert edited.revision==before_edit.revision+1 and edited.status=='draft_created'
  assert edited.seo_check is None and not edited.professional_review
  assert len([url for url in writes[before_posts:] if url.endswith('/editorial')])==1
  assert not any(url.endswith('/jobs') for url in writes[before_posts:])
  assert len(provider.calls)==1
  checks['local_editor_saves_one_revision_without_ai_jobs_or_approval']=True
  # A NEW combined run crosses the actual API/graph/review/export path. All
  # external transports and identities below are explicitly disposable fakes.
  from pytest import MonkeyPatch
  from tests.combined_harness import install_combined_fakes
  from src.security.run_access import RunAccess
  from io import BytesIO
  from PIL import Image
  patches=MonkeyPatch();fake=install_combined_fakes(settings,patches)
  settings.image_request_max_usd=.01
  stages=page.get_by_role('navigation',name='Run stages')
  stages.get_by_role('button',name='Research & Topic Selection',exact=False).click()
  expect(page.get_by_role('combobox',name='Internal linking policy',exact=True)).to_have_value('starter_library')
  checks['starter_library_creation_choice_is_visible_and_default']=True
  page.get_by_label('Maximum run allowance (USD)',exact=True).fill('0.25')
  page.get_by_role('button',name='Start research',exact=True).click()
  expect(page.get_by_role('heading',name='Choose an SEO recommendation',exact=True)).to_be_visible(timeout=30000)
  selected_run=next(s for s in workflow.storage.list_runs() if s.owner_user_id==user['id'] and s.status=='awaiting_selection' and s.run_budget_usd==.25)
  assert selected_run.internal_link_policy=='starter_library'
  before_preflight=len(fake['calls'])
  response=page.request.get(settings.auth_public_origin+'/api/runs/'+str(selected_run.run_id)+'/preflight')
  assert response.status==200 and response.json()['internal_link_policy']['policy']=='starter_library'
  assert len(fake['calls'])==before_preflight
  checks['authenticated_preflight_is_read_only_and_preserves_creation_policy']=True
  chosen=next(r for r in selected_run.analysis.recommendations if r.topic_id=='immobiliensteuerberater')
  page.locator('input[type=radio][value="'+chosen.id+'"]').check()
  check=page.get_by_role('checkbox',name='I have reviewed the open assumptions',exact=False)
  if check.count():check.check()
  page.get_by_label('Reasoning and instructions',exact=False).fill('Synthetische vollständige Prüfung der gespeicherten Verarbeitung.')
  page.get_by_role('button',name='Create briefing and draft',exact=True).click()
  expect(page.get_by_role('heading',name='Professional attestation',exact=True)).to_be_visible(timeout=30000)
  reviewed=workflow.get(str(selected_run.run_id));assert reviewed.status=='seo_check_passed' and reviewed.qa_manifest['complete']
  expect(page.get_by_role('button',name='Approve and export locally',exact=True)).to_be_disabled()
  buf=BytesIO();Image.new('RGB',(1200,600),'#dce8f5').save(buf,format='WEBP')
  page.locator('input[type=file]').set_input_files({'name':'synthetic-infographic.webp','mimeType':'image/webp','buffer':buf.getvalue()})
  expect(page.get_by_text('A reviewed infographic for this revision is missing',exact=False)).to_have_count(0)
  adviser=app.state.auth.store.create_user('browser-qualified@example.org','Synthetic qualified adviser','tax_adviser','synthetic-password-123')
  RunAccess(app.state.auth.store).grant(workflow.get(str(reviewed.run_id)),adviser,app.state.auth.store.user('id',user['id']),True)
  adviser_session,adviser_csrf=app.state.auth.session(adviser['id'])
  adviser_context=browser.new_context(viewport={'width':1440,'height':1100})
  adviser_context.route('**/*',lambda r:r.continue_() if r.request.url.startswith(('http://127.0.0.1:3108/','blob:','data:')) else r.abort())
  adviser_context.add_cookies([{'name':'review_session','value':adviser_session,'url':settings.auth_public_origin},{'name':'review_csrf','value':adviser_csrf,'url':settings.auth_public_origin}])
  adviser_page=adviser_context.new_page();adviser_page.goto(settings.auth_public_origin)
  adviser_page.evaluate('([id,user])=>localStorage.setItem("muster:"+user+":last-run",id)',[str(reviewed.run_id),adviser['id']]);adviser_page.reload()
  expect(adviser_page.get_by_role('heading',name='Professional attestation',exact=True)).to_be_visible()
  adviser_page.get_by_role('checkbox',name='The professional review of the full text',exact=False).check()
  adviser_page.get_by_role('checkbox',name='I have checked the infographic',exact=False).check()
  adviser_page.get_by_label('Professional review note',exact=True).fill('Synthetische Testprüfung, keine reale fachliche Freigabe.')
  adviser_page.get_by_role('button',name='Record professional attestation',exact=True).click()
  expect(adviser_page.get_by_text('Attestation recorded by',exact=False)).to_be_visible()
  adviser_context.close();page.reload()
  expect(page.get_by_text('Attestation recorded by',exact=False)).to_be_visible()
  page.get_by_role('checkbox',name='The professional review of the full text',exact=False).check()
  page.get_by_role('checkbox',name='I have checked the infographic',exact=False).check()
  page.get_by_role('checkbox',name='I confirm that all publishing',exact=False).check()
  page.get_by_role('button',name='Approve and export locally',exact=True).click()
  expect(page.get_by_role('heading',name='German social drafts',exact=True)).to_be_visible(timeout=30000)
  page.get_by_role('button',name='Prepare LinkedIn and Google Business Profile drafts',exact=True).click()
  social=page.locator('section.panel').filter(has=page.get_by_role('heading',name='German social drafts',exact=True))
  expect(social.locator('article.asset')).to_have_count(2,timeout=30000)
  for item in social.locator('article.asset').all():item.get_by_role('button',name='Approve',exact=True).click()
  images_panel=page.locator('section.panel').filter(has=page.get_by_role('heading',name='Images (optional)',exact=True))
  images_panel.get_by_role('radio',name='Generate AI images',exact=True).check()
  images_panel.get_by_role('button',name='Generate image set',exact=True).click()
  expect(images_panel.locator('.asset-image')).to_have_count(3,timeout=30000)
  for item in images_panel.locator('article.asset').all():item.get_by_role('button',name='Approve',exact=True).click()
  with page.expect_download() as download:images_panel.locator('article.asset').first.get_by_role('button',name='Download image',exact=True).click()
  assert download.value.suggested_filename.endswith('.webp')
  assert len(fake['master'].calls)==1
  assert all(value>0 for key,value in fake['sources'].items() if key!='competitor'),fake['sources']
  assert selected_run.research_scope=='site' and 'CoverageAssessment' in fake['calls']
  progress_card=page.get_by_role('region',name='Current progress',exact=True)
  expect(progress_card).to_contain_text('Approved for local export')
  page.set_viewport_size({'width':1440,'height':1100});page.evaluate('scrollTo(0,0)')
  page.screenshot(path=str(ART/'combined-journey-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(ART/'combined-journey-mobile.png'),full_page=True)
  checks['fresh_combined_browser_journey_preserves_professional_gates_and_all_downloads']=True
  image_list_url='**/api/runs/'+str(reviewed.run_id)+'/images'
  context.route(image_list_url,lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic optional-image interruption"}'))
  page.reload()
  page.get_by_role('navigation',name='Run stages').get_by_role('button',name='Draft & Quality Checks',exact=False).click()
  preview=page.locator('iframe[title="German article preview"]')
  expect(preview).to_be_visible();expect(preview.content_frame.locator('body')).to_contain_text('Sie')
  expect(page.get_by_role('region',name='Current progress',exact=True)).to_contain_text('Approved for local export')
  context.unroute(image_list_url);page.reload()
  checks['optional_image_list_failure_preserves_current_run_and_german_preview']=True
  # Link-only recommendations are a free planning action, not paid drafting.
  previous_runs={str(item.run_id) for item in workflow.storage.list_runs()}
  page.get_by_role('navigation',name='Run stages').get_by_role('button',name='Research & Topic Selection',exact=False).click()
  page.get_by_role('button',name='Start research',exact=True).click()
  expect(page.get_by_role('heading',name='Choose an SEO recommendation',exact=True)).to_be_visible(timeout=30000)
  link_run=next(item for item in workflow.storage.list_runs() if str(item.run_id) not in previous_runs)
  link_choice=next(item for item in link_run.analysis.recommendations if item.topic_id=='immobiliensteuerberater')
  page.locator('input[type=radio][value="'+link_choice.id+'"]').check()
  before_link_models=len(fake['calls']);preflight_reads=[]
  page.on('request',lambda request:preflight_reads.append(request.url) if '/preflight' in request.url else None)
  page.get_by_role('button',name='Save link recommendations only',exact=True).click()
  expect(page.get_by_role('heading',name='Research is saved',exact=True)).to_be_visible(timeout=30000)
  assert workflow.get(str(link_run.run_id)).status=='recommendations_only'
  assert len(fake['calls'])==before_link_models and not preflight_reads
  checks['link_only_selection_remains_free_without_drafting_preflight']=True
  patches.undo()
  assert not console,console
  checks['no_browser_runtime_errors']=True
  browser.close()
 (ART/'results.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
finally:
 front.terminate()
 try:front.wait(timeout=10)
 except subprocess.TimeoutExpired:front.kill();front.wait()
 server.should_exit=True;thread.join(timeout=10);log.close()
