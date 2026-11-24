"""Offline contract, privacy, campaign and research-expansion regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from uuid import uuid4
import copy,json
import httpx
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.orchestrator.state import RunState
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.decisions import AssessmentBatch,PageAssessment,PageEvidence,ResearchCoverage
from src.schemas.content import SEOAnalysis,Keyword
from src.schemas.jobs import Job
from src.services.visibility import Observation,add,view,tools,collect
from src.storage.sqlite_storage import SQLiteStorage,BudgetExceeded
from src.services.recovery import running_job
from tests.conftest import authenticated_headers,own_fixture


def observation(**kwargs):
    return Observation(platform='claude',query='Welche Steuerberatung passt zu Unternehmen?',model='observed-model',
        search_mode='web_search',evidence_type='consumer_interface',observed_at=datetime.now(timezone.utc)-timedelta(seconds=1),
        excerpt='Muster & Partner wird hier genannt.',provenance='Manual public interface observation',**kwargs)


def campaign(settings,storage):
    root=RunState(icp=copy.deepcopy(icp_1_buy_and_hold),run_budget_usd=5,research_scope='site')
    root.campaign_id=root.run_id;root.campaign_budget_usd=5;storage.save_run_state(root)
    child=RunState(icp=root.icp,run_budget_usd=5,campaign_id=root.campaign_id,campaign_budget_usd=5,parent_run_id=root.run_id,research_scope='site')
    storage.save_run_state(child)
    return root,child


def test_shared_campaign_atomic_across_children_and_restart(settings):
    storage=SQLiteStorage(settings.data_dir/'campaign.sqlite');root,child=campaign(settings,storage)
    def request(i):
        try:return storage.reserve_research_cost(str((root if i%2 else child).run_id),750000,5000000)
        except BudgetExceeded:return None
    with ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(request,range(10)))
    assert sum(x is not None for x in results)==6
    restarted=SQLiteStorage(storage.path);costs=restarted.campaign_costs(str(root.run_id))
    assert costs['reserved_microusd']==4500000 and costs['checkpoint_reached']
    with pytest.raises(BudgetExceeded):restarted.reserve_research_cost(str(child.run_id),500001,5000000)
    first=next(x for x in results if x);restarted.settle_research_cost(first,700000)
    assert restarted.campaign_costs(str(child.run_id))['recorded_microusd']==700000


def test_legacy_limit_and_immutable_campaign(settings):
    storage=SQLiteStorage(settings.data_dir/'legacy.sqlite');state=RunState(icp=icp_1_buy_and_hold);storage.save_run_state(state)
    with pytest.raises(BudgetExceeded):storage.reserve_research_cost(str(state.run_id),1000001,5000000)
    state.research_scope='site'
    with pytest.raises(ValueError):storage.save_run_state(state)


@pytest.mark.parametrize('field,value',[
 ('query','Warum Muster wählen?'),('excerpt','private.person@example.org'),
 ('cited_urls',['http://example.org/']),('cited_urls',['https://example.org/?secret=value']),
 ('observed_at',datetime.now(timezone.utc)+timedelta(days=1)),('observed_at',datetime.now()),
 ('evidence_type','imaginary'),('platform','all_llms')])
def test_observation_rejects_invalid_or_private_evidence(field,value):
    data=observation().model_dump();data[field]=value
    with pytest.raises(ValueError):Observation.model_validate(data)


def test_brand_claim_is_derived_and_import_id_is_immutable(settings):
    store=SQLiteStorage(settings.data_dir/'observations.sqlite');state=RunState(icp=icp_1_buy_and_hold);store.save_run_state(state)
    item=observation();assert item.brand_mentioned
    data=item.model_dump();data['excerpt']='Keine Kanzlei genannt.';data['brand_mentioned']=True
    assert not Observation.model_validate(data).brand_mentioned
    saved=add(store,state,item,'synthetic-user');assert add(store,state,item,'synthetic-user')==saved
    changed=item.model_copy(update={'excerpt':'Anderer öffentlicher Text.'})
    with pytest.raises(ValueError):add(store,state,changed,'synthetic-user')
    state=store.get_run_state(str(state.run_id));report=view(state)
    assert len(report['queries'])==12 and len(report['observations'])==1
    assert next(x for x in report['platforms'] if x['platform']=='gemini')['status']=='not_tested'
    assert report['observations'][0]['collection_method']=='manual_submission'


def test_missing_tools_are_not_reported_as_live_verified(settings):
    store=SQLiteStorage(settings.data_dir/'tools.sqlite');state=RunState(icp=icp_1_buy_and_hold)
    settings.firecrawl_enabled=True;settings.firecrawl_api_key='fake';settings.firecrawl_request_max_usd=.01
    report=tools(settings,state,store);fire=next(t for t in report if t['tool']=='firecrawl')
    assert fire['enabled'] and fire['configured'] and fire['live_status']=='unverified'


def test_website_tool_date_comes_from_actual_saved_collection(settings):
    store=SQLiteStorage(settings.data_dir/'tool-date.sqlite');when=datetime.now(timezone.utc)
    state=RunState(icp=icp_1_buy_and_hold,research_snapshot={'website':{'content_complete':False}},
        analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],
            page_evidence=[PageEvidence(id='p',url='https://steuerkanzlei.example/',title='Start',retrieved_at=when)]))
    website=next(t for t in tools(settings,state,store) if t['tool']=='website')
    assert website['last_verified_at']==when.isoformat()
    assert state.research_snapshot['website']['content_complete'] is False


def test_automated_collection_provenance_and_saved_unit_reuse(settings,monkeypatch):
    from src.integrations.dataforseo import DataForSEO
    settings.demo_mode=False;settings.max_run_usd=5
    store=SQLiteStorage(settings.data_dir/'automated.sqlite');state=RunState(icp=icp_1_buy_and_hold,run_budget_usd=5);store.save_run_state(state)
    job=Job(run_id=state.run_id,kind='ai_visibility',reviewer='Synthetic',idempotency_key=uuid4());store.create_job(job)
    calls=[]
    def lookup(self,route,payload):
        calls.append((route,payload));return [{'sections':[{'text':'Muster und Partner wird genannt.','annotations':[{'direct_url':'https://steuerkanzlei.example/'}]}]}]
    monkeypatch.setattr(DataForSEO,'_lookup',lookup)
    monkeypatch.setattr('src.services.tool_health.check_accounts',lambda *args:[{'tool':'dataforseo','status':'account_verified'}])
    workflow=SimpleNamespace(settings=settings,storage=store,get=store.get_run_state)
    with running_job(job,store):collect(workflow,job);collect(workflow,job)
    assert len(calls)==4 and all('muster' not in p['user_prompt'].casefold() for _,p in calls)
    observations=store.get_run_state(str(state.run_id)).visibility_observations
    assert len(observations)==4 and all(o['evidence_type']=='controlled_api' and o['collection_method']=='recorded_provider_response' for o in observations)


def test_visibility_private_api_and_observation_permissions(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam')
        state=RunState(icp=icp_1_buy_and_hold);client.app.state.workflow.storage.save_run_state(state);own_fixture(client,state)
        rid=str(state.run_id)
        assert client.get('/runs/'+rid+'/visibility').status_code==200
        response=client.post('/runs/'+rid+'/visibility/observations',headers=headers,json=observation().model_dump(mode='json'))
        assert response.status_code==200,response.text
        assert client.get('/runs/'+rid+'/research-evidence?format=csv').status_code==200
        assert client.get('/runs/'+rid+'/analysis-report.pdf?language=en').content.startswith(b'%PDF')
        client.post('/session/logout',headers=headers)
        assert client.get('/runs/'+rid+'/visibility').status_code==401


def test_live_scope_and_linked_campaign_are_persisted_before_work(settings):
    settings.max_run_usd=5
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam')
        body={'kind':'start','icp_id':'icp_1_buy_and_hold','research_scope':'site','run_budget_usd':5,'internal_link_policy':'starter_library','idempotency_key':str(uuid4())}
        response=client.post('/jobs',headers=headers,json=body);assert response.status_code==202,response.text
        assert client.post('/jobs',headers=headers,json=body).json()['id']==response.json()['id']
        store=client.app.state.workflow.storage;state=store.get_run_state(response.json()['run_id'])
        assert state.campaign_id==state.run_id and state.campaign_budget_usd==5 and state.research_scope=='site'
        job=store.get_job(response.json()['id']);job.status='completed';store.save_job(job)
        response=client.post('/jobs',headers=headers,json={'kind':'research_refresh','run_id':str(state.run_id),'idempotency_key':str(uuid4())})
        assert response.status_code==202,response.text
        child=store.get_run_state(response.json()['run_id'])
        assert child.campaign_id==state.campaign_id and child.internal_link_policy=='starter_library' and child.research_scope=='site'


def test_csv_escapes_spreadsheet_formulas():
    from src.services.evidence_report import export_evidence
    text=export_evidence({'recommendations':[{'id':'x','title':'=HYPERLINK(1)','action':'investigate','metrics':[]}]})
    assert "'=HYPERLINK" in text


def test_unknown_country_cannot_support_a_german_recommendation(settings):
    from src.services.verified_recommendations import recommend
    state=RunState(icp=icp_1_buy_and_hold,analysis=SEOAnalysis(data_mode='live',opportunities=[],gaps=[],limitations=[],keywords=[Keyword(keyword='afa immobilien',impressions=1000,position=2,page='https://steuerkanzlei.example/afa/',source='GSC')],existing_pages=[]))
    rec=recommend(SimpleNamespace(settings=settings),state,{'id':'afa','title':'AfA','keyword':'afa immobilien','intent':'informational','page_type':'blog'},[])
    assert not rec.drafting_eligible and not rec.measured_keywords


def test_query_wording_keeps_geography_separate_from_specialist_topic():
    from src.services.visibility import query_groups
    assert query_groups('Steuerberater für Immobilien in Hamburg')==('hamburg','property_investors')
    assert query_groups('Online Steuerberatung für Einzelunternehmen')==('national_online','specialist_tax')
    assert query_groups('Steuerberater München')==('unspecified','general_tax')
    assert query_groups('Steuerberatung')==('unspecified','general_tax')


def test_firecrawl_website_fallback_requires_same_url(settings,monkeypatch):
    from src.integrations.official_research import extract_website_fallback
    from src.integrations.website_scan import WebsiteScan
    settings.firecrawl_enabled=True
    monkeypatch.setattr(WebsiteScan,'validate_url',lambda self,url:None)
    monkeypatch.setattr('src.integrations.official_research.paid_json',lambda *a:{'success':True,'data':{'metadata':{'sourceURL':'https://other.example/'},'html':'<h1>Not ours</h1>'}})
    with pytest.raises(ValueError):extract_website_fallback(settings,None,'run','https://steuerkanzlei.example/page/')


def test_bounded_coverage_keeps_order_and_reuses_saved_assessment(settings,monkeypatch):
    from src.services.research_assessments import assess,CoverageAssessment
    from src.services.seo_decisions import DecisionService
    settings.demo_mode=False
    store=SQLiteStorage(settings.data_dir/'coverage.sqlite')
    page=PageEvidence(id='p',url='https://steuerkanzlei.example/page/',title='AfA',content_truncated=False,
        sections=[{'id':'one','text':'Erster vollständiger Abschnitt.'},{'id':'two','text':'Zweiter vollständiger Abschnitt.'}])
    state=RunState(icp=icp_1_buy_and_hold,research_scope='site');store.save_run_state(state)
    topic={'id':'afa','title':'AfA','keyword':'afa immobilien','intent':'informational','page_type':'blog'}
    calls=[]
    def generate(*args,**kwargs):
        payload=args[3];calls.append(payload)
        return CoverageAssessment(assessments=[PageAssessment(topic_id='afa',page_url=page.url,relation='dedicated',evidence_quote='Erster vollständiger Abschnitt.',reasoning='Textbeleg geprüft.')])
    service=DecisionService(SimpleNamespace(settings=settings,storage=store,llm=SimpleNamespace(generate=generate)))
    assess(service,state,[topic],[page]);assess(service,state,[topic],[page])
    assert len(calls)==1 and calls[0]['pages'][0]['sections']==page.sections
    assert state.research_snapshot['coverage_review']['complete']


def test_read_only_account_checks_validate_search_capability_and_redact_bodies(settings):
    from src.services.tool_health import check_accounts
    settings.firecrawl_enabled=True;settings.firecrawl_api_key='fake';settings.dataforseo_login='fake';settings.dataforseo_password='fake'
    store=SQLiteStorage(settings.data_dir/'accounts.sqlite');state=RunState(icp=icp_1_buy_and_hold);store.save_run_state(state)
    seen=[]
    def handler(request):
        seen.append(request.method)
        if request.url.host=='api.firecrawl.dev':return httpx.Response(200,json={'success':True,'data':{'remainingCredits':50,'secret':'never-report'}})
        return httpx.Response(200,json={'status_code':20000,'tasks':[{'status_code':20000,'result':[{'model_name':settings.ai_visibility_model,'web_search_supported':True}]}]})
    report=check_accounts(settings,store,str(state.run_id),httpx.MockTransport(handler))
    assert all(c['status']=='account_verified' for c in report) and seen==['GET','GET']
    assert 'never-report' not in json.dumps(report)
    assert store.research_costs(str(state.run_id))=={'recorded_microusd':0,'reserved_microusd':0}


def test_observations_survive_graph_revisions_and_restart(settings):
    from src.orchestrator.workflow import Workflow
    with Workflow(settings) as workflow:
        state=workflow.start(icp_1_buy_and_hold)
        item=observation();add(workflow.storage,state,item,None)
        assert len(workflow.get(str(state.run_id)).visibility_observations)==1
        # Saving an older graph envelope must not discard independent observations.
        checkpoint=workflow.graph.get_state(workflow._config(str(state.run_id)))
        stale=RunState.model_validate(checkpoint.values['run']);assert stale.visibility_observations==[]
        workflow._save(stale)
        assert len(workflow.get(str(state.run_id)).visibility_observations)==1
    with Workflow(settings) as restarted:
        assert len(restarted.get(str(state.run_id)).visibility_observations)==1


def test_concurrent_visibility_clicks_queue_only_one_job(settings):
    store=SQLiteStorage(settings.data_dir/'duplicate.sqlite');state=RunState(icp=icp_1_buy_and_hold);store.save_run_state(state)
    def queue(_):
        try:return store.create_job(Job(run_id=state.run_id,kind='ai_visibility',reviewer='Synthetic',idempotency_key=uuid4()))
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=6) as pool:jobs=list(pool.map(queue,range(6)))
    assert sum(j is not None for j in jobs)==1


def test_broader_profile_can_research_but_needs_approved_facts_before_drafting(settings):
    from src.services.preflight import inspect
    from src.schemas.icp import load_icp
    profile=load_icp(settings.input_dir/'icps/site_tax_services.json')
    store=SQLiteStorage(settings.data_dir/'profile.sqlite');state=RunState(icp=profile,research_scope='site');store.save_run_state(state)
    workflow=SimpleNamespace(settings=settings,storage=store)
    assert any(f['code']=='audience_approval' for f in inspect(workflow,state)['findings'])
    state.icp.drafting_approved=True;state.icp.approved_fact_sources=['https://steuerkanzlei.example/leistungen/']
    assert not any(f['code']=='audience_approval' for f in inspect(workflow,state)['findings'])


def test_supported_opportunity_keeps_evidence_status_when_profile_blocks_drafting(monkeypatch):
    from src.schemas.decisions import SEORecommendation,SelectionRequest
    from src.services.visibility import annotate
    from src.services.seo_decisions import validate_selection,DecisionService
    rec=SEORecommendation(id='supported',topic_id='service',title='Einzelunternehmen',
        keyword='steuerberater für einzelunternehmen',search_intent='commercial',page_type='landing_page',
        action='optimize',reasoning='Synthetischer belegter Strukturhinweis.',evidence_refs=['keyword-0'],
        target_url='https://steuerkanzlei.example/zielgruppen/einzelunternehmen/')
    state=RunState(icp=icp_1_buy_and_hold,research_scope='site',status='awaiting_selection',
        analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],recommendations=[rec]))
    annotate(state)
    assert rec.evidence_status=='evidence_backed' and not rec.profile_ready and not rec.drafting_eligible
    assert rec.audience_profile_id=='sole_traders'
    # Professional topic support cannot bypass an unrelated or unapproved service profile.
    monkeypatch.setattr(DecisionService,'validate_recommendations',lambda *args:None)
    request=SelectionRequest(recommendation_id=rec.id,expected_revision=state.revision,
        professional_support=True,notes='Synthetic professional topic support for this regression test only.')
    with pytest.raises(ValueError,match='matching approved audience/service profile'):
        validate_selection(state,request)


def test_service_profile_is_available_beyond_icp1_in_live_mode(settings):
    settings.demo_mode=False
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam')
        profiles=client.get('/icps').json()
        assert any(p['id']=='site_tax_services' and not p['drafting_approved'] for p in profiles)
        response=client.post('/jobs',headers=headers,json={'kind':'start','icp_id':'site_tax_services','research_scope':'site','idempotency_key':str(uuid4())})
        assert response.status_code==202,response.text


def test_source_failure_preserves_research_and_does_not_buy_later_models(settings,monkeypatch):
    from tests.combined_harness import install_combined_fakes
    from src.integrations.dataforseo import DataForSEO,SourceRequestError
    fake=install_combined_fakes(settings,monkeypatch)
    def fail(*args,**kwargs):raise SourceRequestError('source_api_error')
    monkeypatch.setattr(DataForSEO,'research',fail)
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam')
        response=client.post('/jobs',headers=headers,json={'kind':'start','icp_id':'icp_1_buy_and_hold','research_scope':'site','idempotency_key':str(uuid4())})
        assert response.status_code==202
        assert client.app.state.worker.process_one()
        job=client.get('/jobs/'+response.json()['id']).json()
        assert job['status']=='failed' and job['error_code']=='source_api_error'
        state=client.app.state.workflow.get(job['run_id'])
        assert state.analysis and state.analysis.keywords and state.research_snapshot['stages']['website']
        assert not fake['calls']


def test_technical_findings_are_observations_not_ranking_cause_claims():
    from src.services.search_audit import findings
    page=PageEvidence(id='page',url='https://steuerkanzlei.example/service/',title='Leistung',indexable=False,meta_description='',canonical='https://other.example/')
    state=RunState(icp=icp_1_buy_and_hold,analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],page_evidence=[page]))
    result=findings(state)
    assert {f['code'] for f in result}=={'noindex','empty_description','external_canonical'}
    assert all(f['ranking_cause_confirmed'] is False for f in result)


def test_unrelated_account_cannot_fetch_visibility_or_evidence(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();authenticated_headers(client,'Sam')
        state=RunState(icp=icp_1_buy_and_hold);own_fixture(client,state);client.app.state.workflow.storage.save_run_state(state)
        authenticated_headers(client,'Unrelated')
        for suffix in ['visibility','research-evidence?format=csv','analysis-report.pdf?language=en']:
            assert client.get('/runs/'+str(state.run_id)+'/'+suffix).status_code==404


def test_large_privacy_page_cannot_block_relevant_review_or_leak_into_model_input(settings):
    from src.services.research_assessments import assess,CoverageAssessment
    from src.services.seo_decisions import DecisionService
    settings.demo_mode=False;store=SQLiteStorage(settings.data_dir/'privacy-scope.sqlite')
    policy=PageEvidence(id='privacy',url='https://steuerkanzlei.example/datenschutz/',title='Datenschutz',sections=[{'id':str(i),'text':'Datenschutzhinweise. '*100} for i in range(50)])
    service_page=PageEvidence(id='service',url='https://steuerkanzlei.example/afa/',title='AfA Immobilien',content_truncated=False,sections=[{'id':'intro','text':'Sie bereiten Ihre Fragen zur Abschreibung vor.'}])
    state=RunState(icp=icp_1_buy_and_hold,research_scope='site');store.save_run_state(state)
    captured=[]
    def generate(*args,**kwargs):
        captured.append(args[3]);return CoverageAssessment(assessments=[PageAssessment(topic_id='afa',relation='dedicated',page_url=service_page.url,evidence_quote=service_page.sections[0]['text'],reasoning='Originaler Textbeleg.')])
    service=DecisionService(SimpleNamespace(settings=settings,storage=store,llm=SimpleNamespace(generate=generate)))
    topic={'id':'afa','title':'AfA','keyword':'afa immobilien','intent':'informational','page_type':'blog'}
    result=assess(service,state,[topic],[policy,service_page])
    assert result.assessments[0].relation=='dedicated' and len(captured)==1
    assert [p['url'] for p in captured[0]['pages']]==[service_page.url]
    assert state.research_snapshot['coverage_review']['excluded_pages'][0]['url']==policy.url
    assert len(policy.sections)==50


def test_visibility_html_audit_is_bounded_and_does_not_verify_people_or_claims():
    from src.integrations.website_scan import extract_page
    from src.services.seo_decisions import snapshot_pages
    from src.services.visibility_audit import audit
    html='''<h1>Muster &amp; Partner</h1><h2>Welche Unterlagen benötigen Sie?</h2>
    <p>Ein synthetisches öffentliches Beispiel für diesen Test.</p>
    <a href="https://www.gesetze-im-internet.de/estg/">Originalquelle</a>
    <a href="https://forum.elster.de/">Forum ist keine amtliche Quelle</a>
    <script type="application/ld+json">{"@graph":[{"@type":"AccountingService","name":"Muster & Partner"},{"@type":"Person","name":"SYNTHETIC SECRET NEVER STORED"}]}</script>'''
    page=extract_page(html,'https://steuerkanzlei.example/service/')
    signals=page['visibility_signals']
    assert signals['structured_types']==['AccountingService','Person']
    assert signals['attribution_candidate'] and signals['organization_names_not_found_in_html_text']==0
    assert signals['official_citation_links']==['https://www.gesetze-im-internet.de/estg/']
    assert 'SYNTHETIC SECRET' not in json.dumps(signals)
    pages=snapshot_pages([page],'https://steuerkanzlei.example')
    state=RunState(icp=icp_1_buy_and_hold,analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],page_evidence=pages))
    result=audit(state)
    assert result['business_identity_pages']==result['question_heading_pages']==result['structured_data_inspected_pages']==1
    assert result['official_link_pages']==1 and not result['observations'][0]['professional_qualification_verified']


def test_visibility_audit_preserves_unknowns_mismatches_and_malformed_schema():
    from src.integrations.website_scan import extract_page
    from src.services.visibility_audit import audit
    page=extract_page('<h1>Beispiel</h1><p>Synthetischer öffentlicher Beispieltext.</p>'
        '<script type="application/ld+json">{"@type":"Organization","name":"Wrong company"}</script>'
        '<script type="application/ld+json">{"unfinished":</script>', 'https://steuerkanzlei.example/')
    assert page['visibility_signals']['invalid_json_ld_blocks']==1
    assert page['visibility_signals']['organization_names_not_found_in_html_text']==1
    huge=extract_page('<script type="application/ld+json">{"value":"'+('x'*65000)+'"}</script>','https://steuerkanzlei.example/')
    assert huge['visibility_signals']['bounded_parse_incomplete']
    state=RunState(icp=icp_1_buy_and_hold,analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],page_evidence=[PageEvidence(id='old',url='https://steuerkanzlei.example/',title='Legacy')]))
    result=audit(state)
    assert result['structured_data_inspected_pages']==0 and result['observations'][0]['structured_data'] is None


def test_independent_html_audit_survives_graph_restore_without_crossing_source_hashes(settings):
    from src.orchestrator.workflow import Workflow
    from src.services.visibility_audit import restore_saved_signals
    with Workflow(settings) as workflow:
        state=workflow.start(icp_1_buy_and_hold)
        state.analysis=SEOAnalysis(data_mode='demo',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],
            page_evidence=[PageEvidence(id='p',url='https://steuerkanzlei.example/',title='Synthetic',content_hash='original-hash')])
        workflow.graph.update_state(workflow._config(str(state.run_id)),workflow._save(state))
        snapshot=workflow.get(str(state.run_id));saved=copy.deepcopy(snapshot)
        saved.analysis.page_evidence[0].visibility_signals={'collected':True,'retrieved_at':'2026-10-20T12:00:00+00:00'}
        workflow.storage.save_run_state(saved)
        assert workflow.get(str(state.run_id)).analysis.page_evidence[0].visibility_signals['collected']
        workflow._save(snapshot)
        assert workflow.storage.get_run_state(str(state.run_id)).analysis.page_evidence[0].visibility_signals['collected']
        changed=copy.deepcopy(snapshot);changed.analysis.page_evidence[0].content_hash='changed-source';changed.analysis.page_evidence[0].visibility_signals={}
        restore_saved_signals(changed,saved)
        assert changed.analysis.page_evidence[0].visibility_signals=={}
    with Workflow(settings) as restarted:
        assert restarted.get(str(state.run_id)).analysis.page_evidence[0].visibility_signals['collected']
