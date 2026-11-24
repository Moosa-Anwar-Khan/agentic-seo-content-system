"""Isolated browser checks for account setup, private history and sharing.

Run after `npm --prefix frontend run build`. Disposable SQLite data, credentials,
loopback-only requests and existing offline content fixtures; no paid calls.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest  # Blocks external network and prevents reading the real .env.
import argparse,json,os,re,subprocess,threading,time,tempfile
import httpx,uvicorn
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright,expect
from src.api.app import create_app
from src.config.settings import Settings
from src.security.auth import totp_code

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pending-administrator',action='store_true',help='Exercise first-admin setup after ordinary registration with the same email')
options=parser.parse_args()

ART=ROOT/'var/assessment/accounts-browser';ART.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='session-',dir=ART));PASSWORD='disposable-browser-password-123'
settings=Settings(_env_file=None,demo_mode=True,combined_mode=False,seo_decisions_enabled=False,
 data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode(),auth_public_origin='http://127.0.0.1:3109')
app=create_app(settings);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8019,log_level='error',access_log=False))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
node=ROOT/'.assessment-tools/node-v24.22.0-darwin-arm64/bin/node';log=(work/'next.log').open('w')
front=subprocess.Popen([str(node),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3109'],cwd=Path(os.environ.get('PORTAL_FRONTEND_DIR',str(ROOT/'frontend'))),env={**os.environ,'API_BASE_URL':'http://127.0.0.1:8019','NEXT_TELEMETRY_DISABLED':'1'},stdout=log,stderr=subprocess.STDOUT)
checks={};errors=[];registrations=[]
try:
 with httpx.Client(timeout=2,trust_env=False) as http:
  for _ in range(100):
   try:
    if server.started and http.get(settings.auth_public_origin).status_code==200:break
   except httpx.TransportError:pass
   time.sleep(.2)
  else:raise AssertionError('Servers did not start')
 code=app.state.auth.prepare_bootstrap()
 with sync_playwright() as pw:
  chrome=os.environ.get('PORTAL_CHROME_EXECUTABLE')
  if not chrome and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').exists():
   chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  browser=pw.chromium.launch(**({'executable_path':chrome} if chrome else {}),headless=True,args=['--no-first-run'])
  def context():
   c=browser.new_context(viewport={'width':1440,'height':1050},accept_downloads=True)
   c.route('**/*',lambda r:r.continue_() if r.request.url.startswith(('http://127.0.0.1:3109/','blob:','data:')) else r.abort())
   return c
  admin_context=context();member_context=context();other_context=context()
  admin=admin_context.new_page();member=member_context.new_page();other=other_context.new_page()
  member.on('request',lambda r:registrations.append(r.url) if r.method=='POST' and r.url.endswith('/auth/register') else None)
  for page in [admin,member,other]:page.on('pageerror',lambda e:errors.append(str(e)))
  def fill_registration(page,name,email):
   page.get_by_label('Name',exact=True).fill(name);page.get_by_label('Email address',exact=True).fill(email)
   page.get_by_label('Password (12–128 characters)',exact=True).fill(PASSWORD)
   page.get_by_label('Confirm password',exact=True).fill(PASSWORD)
  def finish_mfa(page):
   expect(page.get_by_role('heading',name='Set up authenticator',exact=True)).to_be_visible()
   expect(page.locator('section.account-panel code')).to_have_text(re.compile(r'[A-Z2-7]{32}'))
   secret=page.locator('section.account-panel code').inner_text()
   page.get_by_label('Six-digit code',exact=True).fill(totp_code(secret))
   page.get_by_role('button',name='Confirm setup',exact=True).click()
   expect(page.get_by_role('heading',name='Recovery codes',exact=True)).to_be_visible()
   recovery=page.locator('section.panel').filter(has=page.get_by_role('heading',name='Recovery codes',exact=True)).locator('pre').inner_text().splitlines()
   page.get_by_role('button',name='I have saved the codes',exact=True).click()
   return secret,recovery
  def signin(page,email):
   page.get_by_label('Email address',exact=True).fill(email);page.get_by_label('Password',exact=True).fill(PASSWORD)
   page.get_by_role('button',name='Sign in',exact=True).click()
  admin.goto(settings.auth_public_origin)
  pending_admin_id=None
  if options.pending_administrator:
   admin.get_by_role('button',name='Create account',exact=True).click()
   fill_registration(admin,'Morgan','admin@example.org')
   admin.get_by_role('button',name='Create account',exact=True).click()
   expect(admin.get_by_role('heading',name='Registration submitted',exact=True)).to_be_visible()
   pending_admin_id=app.state.auth.store.user('email','admin@example.org')['id']
   admin.get_by_role('button',name='Back to sign in',exact=True).click()
  admin.get_by_role('button',name='Set up administrator',exact=True).click()
  fill_registration(admin,'Morgan','admin@example.org');admin.get_by_label('Local setup code',exact=True).fill(code)
  admin.get_by_label('Local setup code',exact=True).press('Enter')
  admin_secret,_=finish_mfa(admin)
  checks['first_administrator_browser_setup_password_mfa_recovery']=True
  if options.pending_administrator:
   assert app.state.auth.store.user('email','admin@example.org')['id']==pending_admin_id
   assert len(app.state.auth.store.users())==1
   checks['pending_registration_reused_without_duplicate_account']=True
  member.goto(settings.auth_public_origin);expect(member.get_by_role('button',name='Set up administrator',exact=True)).to_have_count(0)
  member.get_by_role('button',name='Create account',exact=True).click()
  fill_registration(member,'Alice','alice@example.org')
  member.screenshot(path=str(ART/'registration-desktop.png'),full_page=True)
  member.get_by_role('button',name='Create account',exact=True).evaluate('(b)=>{b.click();b.click()}')
  expect(member.get_by_role('heading',name='Registration submitted')).to_be_visible()
  assert len(registrations)==1
  member.get_by_role('button',name='Back to sign in').click();signin(member,'alice@example.org')
  expect(member.get_by_role('heading',name='Administrator approval required',exact=True)).to_be_visible()
  assert app.state.workflow.storage.list_runs()==[]
  checks['pending_registration_blocks_runs_and_explains_activation']=True
  admin.get_by_text('Account & registration management',exact=True).click()
  admin.get_by_role('button',name='Load accounts',exact=True).click()
  admin.get_by_label('Administrator password',exact=True).fill(PASSWORD)
  admin.get_by_label('Current authenticator code',exact=True).fill(totp_code(admin_secret))
  admin.get_by_role('button',name='Activate member',exact=True).click()
  expect(admin.get_by_role('button',name='Activate member',exact=True)).to_have_count(0)
  # Trusted final reviewer is assigned by an administrator, never registration.
  admin.get_by_label('Name',exact=True).fill('Alex');admin.get_by_label('Email address',exact=True).fill('alex@example.org')
  admin.get_by_label('Administrator password',exact=True).fill(PASSWORD)
  admin.get_by_label('Current authenticator code',exact=True).fill(totp_code(admin_secret))
  admin.get_by_role('button',name='Create reviewer account',exact=True).click()
  expect(admin.get_by_text('One-time temporary password:',exact=False)).to_be_visible()
  # Use a separate disposable session for the role/isolation checks below.
  signin(member,'alice@example.org');_,recovery=finish_mfa(member)
  member.get_by_role('button',name='Start research',exact=True).click()
  stages=member.get_by_role('navigation',name='Workflow stages');expect(stages).to_be_visible(timeout=30000)
  expect(member.locator('button.run')).to_have_count(1)
  run_id=str(app.state.workflow.storage.list_runs()[0].run_id);alice=app.state.auth.store.user('email','alice@example.org')
  assert app.state.workflow.get(run_id).owner_user_id==alice['id']
  stages.get_by_role('button',name='Draft & Quality Checks',exact=False).click()
  frame=member.locator('iframe[title="German article preview"]');expect(frame).to_be_visible()
  expect(frame.content_frame.locator('body')).to_contain_text('Sie')
  stages.get_by_role('button',name='Professional Review & Approval',exact=False).click()
  expect(member.get_by_role('button',name='Approve and export locally',exact=True)).to_be_disabled()
  with member.expect_download() as downloaded:member.get_by_role('button',name='Download HTML package',exact=True).click()
  assert downloaded.value.suggested_filename.endswith('.zip')
  checks['activated_member_creates_owned_german_draft_downloads_but_cannot_approve']=True
  member.get_by_text('Private history & sharing',exact=True).click()
  member.screenshot(path=str(ART/'sharing-before-selection.png'),full_page=True)
  member.get_by_label('Select a qualified reviewer',exact=True).select_option(label='Alex · Final reviewer')
  member.get_by_role('button',name='Grant access',exact=True).click()
  expect(member.get_by_role('button',name='Revoke access',exact=True)).to_be_visible()
  alex=app.state.auth.store.user('email','alex@example.org');session,csrf=app.state.auth.session(alex['id'])
  other_context.add_cookies([{'name':'review_session','value':session,'url':settings.auth_public_origin},{'name':'review_csrf','value':csrf,'url':settings.auth_public_origin}])
  other.goto(settings.auth_public_origin);other.get_by_role('button',name='Shared with me',exact=True).click()
  expect(other.locator('button.run')).to_have_count(1)
  other.locator('button.run').click();expect(other.get_by_role('navigation',name='Workflow stages')).to_be_visible()
  member.get_by_role('button',name='Revoke access',exact=True).click()
  expect(other.locator('button.run')).to_have_count(0,timeout=15000)
  expect(other.get_by_role('navigation',name='Workflow stages')).to_have_count(0)
  checks['owner_sharing_shared_history_and_revocation_clear_visible_work']=True
  member.evaluate('scrollTo(0,0)');member.screenshot(path=str(ART/'private-history-desktop.png'),full_page=False)
  member.get_by_role('button',name='Sign out',exact=True).click()
  expect(member.locator('button.run')).to_have_count(0)
  expect(member.locator('iframe')).to_have_count(0)
  assert app.state.workflow.get(run_id).owner_user_id==alice['id']
  signin(member,'alice@example.org');expect(member.get_by_role('heading',name='Authenticator code',exact=True)).to_be_visible()
  member.get_by_label('Authenticator or recovery code',exact=True).fill(recovery[0]);member.get_by_role('button',name='Sign in',exact=True).click()
  expect(member.locator('button.run')).to_have_count(1)
  keys=member.evaluate('Object.keys(localStorage)')
  assert all(k.startswith('muster:'+alice['id']+':') for k in keys)
  checks['logout_clears_private_views_and_history_survives_login_with_user_scoped_cache']=True
  # Cookie account changes in another tab clear the currently displayed private view.
  tab=member_context.new_page();tab.goto(settings.auth_public_origin)
  expect(tab.locator('button.run')).to_have_count(1)
  tab.get_by_role('button',name='Sign out',exact=True).click()
  member.bring_to_front();expect(member.locator('button.run')).to_have_count(0,timeout=15000)
  checks['cross_tab_logout_clears_private_history']=True
  member.set_viewport_size({'width':390,'height':844});member.get_by_role('button',name='Create account',exact=True).click()
  expect(member.get_by_label('Name',exact=True)).to_be_visible()
  assert member.evaluate('document.documentElement.scrollWidth<=innerWidth')
  member.get_by_label('Name',exact=True).focus();member.keyboard.press('Tab')
  assert member.get_by_label('Email address',exact=True).evaluate('(e)=>e===document.activeElement')
  member.screenshot(path=str(ART/'registration-mobile.png'),full_page=True)
  checks['mobile_registration_no_overflow_and_keyboard_order']=True
  assert not errors,errors
  checks['no_browser_runtime_errors']=True
  browser.close()
 (ART/'results.json').write_text(json.dumps(checks,indent=2)+'\n');print(json.dumps(checks,indent=2))
finally:
 front.terminate()
 try:front.wait(timeout=10)
 except subprocess.TimeoutExpired:front.kill();front.wait()
 server.should_exit=True;thread.join(timeout=10);log.close()
