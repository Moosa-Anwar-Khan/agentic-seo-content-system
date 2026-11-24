"""Explicit, read-only account checks. No scrape, search or model generation."""
from datetime import datetime,timezone
import httpx


def check_accounts(settings,storage,run_id,transport=None):
    checks=[]
    targets=[('firecrawl','https://api.firecrawl.dev/v2/team/credit-usage',
        {'Authorization':'Bearer '+settings.firecrawl_api_key.get_secret_value()},None),
        ('dataforseo','https://api.dataforseo.com/v3/ai_optimization/gemini/llm_responses/models',{},
         (settings.dataforseo_login.get_secret_value(),settings.dataforseo_password.get_secret_value()))]
    for tool,url,headers,auth in targets:
        if tool=='firecrawl' and (not settings.firecrawl_enabled or not settings.firecrawl_api_key.get_secret_value()):
            checks.append({'tool':tool,'checked_at':datetime.now(timezone.utc).isoformat(),'status':'unverified','reason':'Firecrawl is disabled or its key is missing; no account request was made.'})
            continue
        result={'tool':tool,'checked_at':datetime.now(timezone.utc).isoformat(),'status':'unverified',
            'reason':'Live account access has not been established.'}
        try:
            with httpx.Client(transport=transport,timeout=20,follow_redirects=False) as client:
                response=client.get(url,headers=headers,auth=auth)
                result['http_status']=response.status_code
                if response.status_code in {401,403}:result.update(status='failed',reason='The provider rejected these API credentials or account permissions.')
                elif response.status_code==429:result.update(status='failed',reason='The provider rate limit blocked the account check.')
                elif response.status_code!=200:result.update(status='failed',reason='The account endpoint did not return a successful response.')
                elif len(response.content)>2_000_000:result.update(status='failed',reason='The account response exceeded the allowed size.')
                else:
                    body=response.json()
                    if tool=='firecrawl':
                        credits=(body.get('data') or {}).get('remainingCredits')
                        if body.get('success') is True and type(credits) in {int,float}:
                            result.update(status='account_verified' if credits>0 else 'failed',credits_available=credits>0,
                                reason='Account access and available credits were verified; no extraction was requested.' if credits>0 else 'The account reports no remaining credits.')
                    else:
                        tasks=body.get('tasks') or []
                        def models(value):
                            if isinstance(value,dict):
                                for key,item in value.items():
                                    if key=='model_name' and isinstance(item,str) and value.get('web_search_supported') is True:yield item
                                    yield from models(item)
                            elif isinstance(value,list):
                                for item in value:yield from models(item)
                        names=list(models(tasks))
                        if body.get('status_code')==20000 and tasks and all(t.get('status_code')==20000 for t in tasks):
                            found=settings.ai_visibility_model in names
                            result.update(status='account_verified' if found else 'failed',model_available=found,
                                reason='Account access and the configured Gemini model were verified; no generation was requested.' if found else 'The configured Gemini model was not listed. Check the supported models before collection.')
        except (httpx.HTTPError,ValueError,TypeError):
            result.update(status='failed',reason='The read-only account check failed; the cause needs account or connection inspection.')
        checks.append(result)
    key='tool-accounts:'+run_id
    storage.begin_operation(key,run_id);storage.finish_operation(key,{'checks':checks})
    return checks
