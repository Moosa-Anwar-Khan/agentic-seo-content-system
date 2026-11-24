"""Offline local diagnostics. Never authenticate against paid providers."""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from src.config.settings import Settings, ROOT
from src.services.model_routes import resolve_route, validate_text_route, credential
from src.services.research_budget import text_upper_price
from src.services.images import image_generation_options


def readiness(settings: Settings, storage=None):
    checks=[]
    def add(key,label,status,message):checks.append({'id':key,'label':label,'status':status,'message':message})
    def check(key,label,action,success,remedy,warning=False):
        try:action();add(key,label,'pass',success)
        except Exception:add(key,label,'warning' if warning else 'fail',remedy)
    def required(value):
        if not value:raise ValueError('Missing configuration')
    check('encryption','Encrypted local records',lambda:Fernet(settings.auth_encryption_key.get_secret_value().encode()),'Encryption key is configured.','Configure a valid AUTH_ENCRYPTION_KEY. Preserve the existing key for saved records.')
    for key,path in [('storage',settings.data_dir),('exports',settings.output_dir)]:
        def writable(path=path):
            ancestor=path
            while not ancestor.exists() and ancestor!=ancestor.parent:ancestor=ancestor.parent
            required(ancestor.is_dir() and os.access(ancestor,os.W_OK|os.X_OK))
        check(key,key.title()+' folder',writable,'The local folder is accessible for saving progress.','Give the application write access to its configured local folders.')
    def database():
        if storage is not None:
            with storage.connect() as db:db.execute('SELECT 1')
        elif settings.storage_backend=='sqlite':
            file=settings.data_dir/'runs.sqlite'
            if file.exists():
                with sqlite3.connect(file.as_uri()+'?mode=ro',uri=True) as db:db.execute('SELECT 1')
        else:raise ValueError('Not checked offline')
    check('database','Saved progress database',database,'Database access is available; an absent SQLite database is initialized at startup.','Check database access. For PostgreSQL, confirm connectivity separately.',settings.storage_backend=='postgres' and storage is None)
    assets=ROOT/'src/assets/brand'
    for name in ('logo.svg','montserrat-600-latin.woff2','cabin-400-latin.woff2','cabin-500-latin.woff2'):
        check('asset-'+name,'Brand asset: '+name,lambda name=name:required((assets/name).is_file()),'Bundled brand asset is available.','Restore the bundled brand asset before starting the portal.')
    for name in ('rules.json','visual-rules.json','editorial-rules.json'):
        check('rules-'+name,'Brand rules: '+name,lambda name=name:json.loads((settings.input_dir/'brand-guidelines'/name).read_text()),'Brand rules are readable.','Restore the required brand rules JSON file.')
    from src.combined.editorial import prompt_rules
    from src.security.sanitize import untrusted_context
    from src.services.editorial_brand import public_contact_emails
    for page_type in ('blog', 'landing_page'):
        def brand_prompt(page_type=page_type):
            untrusted_context(prompt_rules(settings, page_type), public_emails=public_contact_emails(settings))
        check('prompt-'+page_type, page_type.replace('_', ' ').title()+' brand prompt', brand_prompt,
              'Complete brand reference passes input checks; only the approved public company email is exempted.',
              'Check the local brand reference, approved email fact and prompt segment sizes. Private addresses and unsafe instructions remain blocked.')
    check('budgets','Spending limits',lambda:required(0<settings.max_run_usd<=5 and settings.max_monthly_usd>0),'Run and monthly limits are configured. Reservations do not guarantee provider invoices.','Configure a positive monthly budget and a run budget no greater than USD 5.')
    if settings.demo_mode:
        add('mode','Offline demo','pass','Synthetic research and text drafts are enabled. Optional image requests still require an explicit click and provider access.')
    for task in ('seo_strategy','topic_map','briefing','content','seo_qa','social_content'):
        def model(task=task):
            route=resolve_route(settings,task);validate_text_route(settings,route,1000,settings.max_output_tokens);credential(settings,route)
            text_upper_price(settings,route,1000,1000)
        check('model-'+task,task.replace('_',' ').title()+' model',model,'Local route, credential presence, capabilities and dated pricing pass. Live access is unverified.','Check the model route, API key presence, capability declaration and current dated pricing.',settings.demo_mode)
    if not settings.demo_mode:
        check('dataforseo','DataForSEO configuration',lambda:required(settings.dataforseo_login.get_secret_value() and settings.dataforseo_password.get_secret_value()),'API credentials are present; live access is unverified.','Configure the DataForSEO API login and API password.')
        def gsc():
            if settings.gsc_access_token.get_secret_value():return
            required(settings.gsc_service_account_file and settings.gsc_service_account_file.is_file())
            from google.oauth2.service_account import Credentials
            Credentials.from_service_account_file(str(settings.gsc_service_account_file),scopes=['https://www.googleapis.com/auth/webmasters.readonly'])
        check('gsc','Search Console configuration',gsc,'Credentials are present; property access is not tested by this check.','Configure a valid local service-account JSON or access token. Property permissions require a separate live check.')
        for provider,enabled,key,allowance in [('Tavily',settings.official_discovery_enabled,settings.tavily_api_key,settings.tavily_request_max_usd),('Firecrawl',settings.firecrawl_enabled,settings.firecrawl_api_key,settings.firecrawl_request_max_usd)]:
            if enabled:check(provider.lower(),provider+' configuration',lambda key=key,allowance=allowance:required(key.get_secret_value() and allowance),'Key and request reservation are present; live access is unverified.','Configure the provider API key and a checked USD request allowance.')
    if settings.image_generation_enabled:
        check('images','Optional image generation',lambda:required(image_generation_options(settings)['configured']),'Local image configuration is present. Generation is optional and account access is unverified.','Configure the image model, provider key and request allowance, or disable optional images.',True)
    failures=sum(c['status']=='fail' for c in checks)
    return {'status':'needs_attention' if failures else 'ready','label':'Configuration needs attention' if failures else 'Ready for local use',
            'mode':'demo' if settings.demo_mode else 'live','checked_at':datetime.now(timezone.utc).isoformat(),
            'live_validation':'Not performed. Key presence does not verify account access, available credits, legal suitability or production readiness.',
            'checks':checks}
