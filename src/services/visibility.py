"""Private, provenance-labelled AI observations; reading never calls providers."""
from datetime import datetime,timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID,uuid4
import re
from pydantic import Field,field_validator,model_validator
from src.schemas.common import StrictModel
from src.security.sanitize import sanitize_text

QUERIES=[
 'Wie finde ich einen passenden Steuerberater in Deutschland?',
 'Welche Steuerberatung unterstützt Unternehmen digital in Deutschland?',
 'Worauf sollte ich bei der Wahl eines Online-Steuerberaters achten?',
 'Welche Unterlagen brauche ich für den Wechsel des Steuerberaters?',
 'Welche Steuerberatung unterstützt Unternehmen bei der Finanzbuchhaltung?',
 'Wer unterstützt Unternehmen beim Jahresabschluss in Deutschland?',
 'Welche Steuerberatung unterstützt Selbstständige bei Steuererklärungen?',
 'Welche Steuerberatung arbeitet mit sevDesk oder Lexware Office?',
 'Wie finde ich eine digitale Steuerberatung in Hamburg?',
 'Welche Steuerberater in Hamburg betreuen kleine Unternehmen?',
 'Wie finde ich einen Steuerberater für private Vermieter?',
 'Welche Steuerberatung unterstützt Immobilieninvestoren bei Anlage V und AfA?']
PLATFORMS=('claude','gemini','grok','kimi','deepseek','codex','chatgpt','bing')


class Observation(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    platform: Literal['claude','gemini','grok','kimi','deepseek','codex','chatgpt','bing']
    query: str = Field(min_length=3,max_length=500)
    model: str = Field(min_length=1,max_length=150)
    search_mode: Literal['web_search','no_web_search','unknown']
    evidence_type: Literal['consumer_interface','provider_interface','controlled_api','bing_report']
    observed_at: datetime
    excerpt: str = Field(min_length=1,max_length=6000)
    cited_urls: list[str] = Field(default_factory=list,max_length=30)
    provenance: str = Field(min_length=3,max_length=500)
    brand_mentioned: bool = False
    observer_user_id: str | None = None

    @field_validator('query','model','excerpt','provenance')
    @classmethod
    def public_text(cls,value):return sanitize_text(value,6000)

    @field_validator('cited_urls')
    @classmethod
    def safe_urls(cls,values):
        result=[]
        for value in values:
            parsed=urlsplit(value)
            if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or
                    parsed.port not in {None,443} or parsed.query or len(value)>2000):
                raise ValueError('Use public HTTPS source URLs without tracking or credentials')
            result.append(sanitize_text(value,2000))
        return list(dict.fromkeys(result))

    @model_validator(mode='after')
    def label(self):
        now=datetime.now(timezone.utc)
        if not self.observed_at.tzinfo or self.observed_at>now:
            raise ValueError('Observation needs a past or current timezone-aware timestamp')
        # Derive the claim from evidence; caller-supplied flags are not trusted.
        object.__setattr__(self,'brand_mentioned',bool(re.search(r'\bmuster\s*(?:(?:&|und)\s*)?partner\b|steuerkanzlei\.example',self.excerpt,re.I)))
        if 'muster' in self.query.casefold():
            raise ValueError('The unprompted baseline must not name the brand')
        if self.evidence_type=='bing_report' and self.platform!='bing':
            raise ValueError('Bing evidence cannot be assigned to another platform')
        return self


def view(state):
    from src.services.visibility_audit import audit
    observations=state.visibility_observations
    return {'content_audit':audit(state),'queries':[{'id':i,'query':q,'category':'general' if i<4 else 'service' if i<8 else 'Hamburg' if i<10 else 'property_investors'} for i,q in enumerate(QUERIES)],
        'platforms':[{'platform':name,'automation':'controlled_api' if name=='gemini' else 'manual_import',
          'required_access':'DataForSEO API access' if name=='gemini' else 'An authorized observation or supported report export',
          'status':'observed' if any(o['platform']==name for o in observations) else 'not_tested',
          'limitations':'Controlled API responses do not establish consumer-app rankings.' if name=='gemini' else 'No automatic equivalent is configured; missing observations are not zero visibility.'} for name in PLATFORMS],
        'observations':observations,'limitations':['Mentions, citations, consumer observations and model API responses are different measurements.',
         'This baseline does not guarantee rankings, leads or inclusion in future answers.']}


def add(storage,state,observation,actor,*,collection_method='manual_submission'):
    item=observation.model_dump(mode='json');item['observer_user_id']=actor;item['collection_method']=collection_method
    existing=next((o for o in state.visibility_observations if o['id']==item['id']),None)
    if existing:
        if existing!=item:raise ValueError('An observation reference cannot overwrite existing evidence')
        return existing
    if len(state.visibility_observations)>=500:raise ValueError('Observation storage limit reached; export and use a new campaign')
    state.visibility_observations.append(item);storage.save_run_state(state)
    return item


def collect(workflow,job):
    """Four deterministic baseline questions, one request each, explicit durable job."""
    from src.integrations.dataforseo import DataForSEO
    from src.services.recovery import current_job
    with workflow.storage.run_lock(str(job.run_id)):
        state=workflow.get(str(job.run_id));client=DataForSEO(workflow.settings)
        if workflow.settings.demo_mode:raise ValueError('Paid AI observations are disabled in demo mode')
        from src.services.tool_health import check_accounts
        accounts=check_accounts(workflow.settings,workflow.storage,str(state.run_id))
        if not any(c['tool']=='dataforseo' and c['status']=='account_verified' for c in accounts):
            from src.integrations.dataforseo import SourceRequestError
            raise SourceRequestError('source_auth')
        model=workflow.settings.ai_visibility_model
        for index in [0,4,8,10]:
            identity=str(__import__('uuid').uuid5(job.id,'gemini-'+str(index)))
            if any(o['id']==identity for o in state.visibility_observations):continue
            context=current_job.get()
            if context:context.step('ai_visibility')
            items=client._lookup('ai_gemini',{'model_name':model,'user_prompt':QUERIES[index],
                'max_output_tokens':500,'temperature':0,'web_search':True,'use_reasoning':False})
            texts=[];urls=[]
            for item in items:
                for section in item.get('sections') or []:
                    if isinstance(section.get('text'),str):texts.append(section['text'])
                    for annotation in section.get('annotations') or []:
                        address=annotation.get('direct_url') or annotation.get('url')
                        if address:
                            parsed=urlsplit(address)
                            if parsed.scheme=='https' and parsed.hostname and not parsed.username:
                                urls.append(parsed._replace(query='',fragment='').geturl())
            if not texts:
                from src.integrations.dataforseo import SourceRequestError
                raise SourceRequestError('source_invalid_response')
            text='\n'.join(texts)
            # Never silently shorten a response or accept an invalid observation.
            observation=Observation(id=identity,platform='gemini',query=QUERIES[index],model=model,
                search_mode='web_search',evidence_type='controlled_api',observed_at=datetime.now(timezone.utc),
                excerpt=text,cited_urls=urls,provenance='DataForSEO Gemini LLM Responses API; controlled web-search request')
            add(workflow.storage,state,observation,job.reviewer_id,collection_method='recorded_provider_response')


def query_groups(query):
    text=query.casefold()
    market='hamburg' if re.search(r'\bhamburg\b',text) else 'national_online' if re.search(r'\b(online|digital\w*|deutschland|bundesweit)\b',text) else 'unspecified'
    property_topic=any(w in text for w in ['immobil','vermiet','anlage v','afa','kaufpreis','schuldzins'])
    specialist=any(w in text for w in ['einzelunternehm','selbstst','freiberuf','finanzbuch','jahresabschluss','steuererklär','steuererklaer','gewerbe','gmbh','lohn'])
    return market,'property_investors' if property_topic else 'specialist_tax' if specialist else 'general_tax'


def annotate(state):
    if not state.analysis:return
    for rec in state.analysis.recommendations:
        rec.search_market,rec.service_segment=query_groups(rec.keyword)
        if state.research_scope:
            property_topic=any(w in rec.keyword.casefold() for w in ['immobil','vermiet','anlage v','afa','kaufpreis','schuldzins'])
            rec.audience_profile_id=('sole_traders' if 'einzelunternehm' in rec.keyword.casefold() else
                'icp_1_buy_and_hold' if property_topic else 'site_tax_services')
            rec.profile_ready=rec.audience_profile_id==state.icp.id and (state.icp.id=='icp_1_buy_and_hold' or bool(state.icp.drafting_approved and state.icp.approved_fact_sources))
            if not rec.profile_ready:
                rec.drafting_eligible=False
                message='Drafting requires the matching audience/service profile and its approved company facts. Professional topic support cannot replace this configuration.'
                if message not in rec.readiness_blockers:rec.readiness_blockers.append(message)
        supported=not rec.demo and not rec.research_needed and rec.action in {'protect','optimize','create_cluster','create_new'} and bool(rec.evidence_refs)
        rec.evidence_status='evidence_backed' if supported else 'needs_research'
        rec.reason_en={
         'protect':'Measured demand and matching existing content support protecting this page from unnecessary changes.',
         'optimize':'Measured demand and a quoted, specific content issue support a targeted improvement.',
         'create_cluster':'Measured demand and reviewed existing coverage support a distinct supporting article.',
         'create_new':'Measured demand and verified coverage support assessing this distinct new page.',
         'investigate':'Search observations exist, but the cause and the appropriate content change are not established.',
         'manual_review':'The available demand or website evidence does not yet support a specific content action.'}[rec.action]
        rec.next_action_en='Inspect the cited measurements and original website quotations before selecting this action.' if supported else 'Resolve the recorded evidence gaps before paying for a draft.'
        if not rec.profile_ready:rec.next_action_en='Approve and configure the matching audience/service facts before drafting. The research and evidence remain available.'


def tools(settings,state,storage):
    snap=state.research_snapshot
    stages=snap.get('stages',{})
    values=[('gsc',True,bool(settings.gsc_access_token.get_secret_value() or settings.gsc_service_account_file),stages.get('gsc_queries')),
        ('dataforseo',True,bool(settings.dataforseo_login.get_secret_value() and settings.dataforseo_password.get_secret_value()),snap.get('dataforseo')),
        ('website',True,bool(settings.wordpress_base_url),snap.get('website')),
        ('tavily',settings.official_discovery_enabled,bool(settings.tavily_api_key.get_secret_value() and settings.tavily_request_max_usd),None),
        ('firecrawl',settings.firecrawl_enabled,bool(settings.firecrawl_api_key.get_secret_value() and settings.firecrawl_request_max_usd),None)]
    result=[]
    operations=storage.list_operations(str(state.run_id))
    account_check=storage.get_operation('tool-accounts:'+str(state.run_id))
    accounts=((account_check or {}).get('value') or {}).get('checks',[])
    for name,enabled,configured,evidence in values:
        relevant=[o for o in operations if 'official-'+name+':' in o['key']]
        completed=[o for o in relevant if o['status']=='completed']
        failed=any(o['status'] in {'failed','pending'} for o in relevant)
        status='unverified';reason='No live result has been verified for this run.'
        if evidence:
            bad=evidence.get('available') is False or evidence.get('status') not in {None,'usable','empty'} or bool(evidence.get('failures'))
            status='failed' if bad else 'empty' if evidence.get('status')=='empty' else 'verified'
            reason='Saved collection reports missing or partial evidence.' if bad else 'The saved response was accepted; collection coverage is reported separately.'
        elif completed:status='verified';reason='An accepted saved response exists for this run.'
        elif failed:status='needs_inspection';reason='A saved request failed or its outcome remains uncertain. Inspect diagnostics before retrying.'
        account=next((c for c in accounts if c['tool']==name),None)
        if account and status=='unverified':
            status=account['status'];reason=account['reason']
        verified_at=(evidence or {}).get('retrieved_at') or (((completed[-1].get('value') or {}).get('retrieved_at')) if completed else None)
        if not verified_at and account and account['status']=='account_verified':verified_at=account.get('checked_at')
        if not verified_at and name=='website' and state.analysis:
            dates=[p.retrieved_at for p in state.analysis.page_evidence if p.retrieved_at]
            if dates:verified_at=max(dates).isoformat()
        result.append({'tool':name,'enabled':enabled,'configured':configured,'live_status':status,
            'reason':reason,'last_verified_at':verified_at,
            'next_action':'Review saved diagnostics and configuration; do not repeat an uncertain request automatically.' if status in {'failed','needs_inspection'} else 'Use only when the current workflow needs this tool.'})
    return result
