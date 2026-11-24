"""Research evidence, USD gates, immutable history, API and report regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
from fastapi.testclient import TestClient
from src.config.settings import Settings
from src.storage.sqlite_storage import SQLiteStorage,BudgetExceeded
from src.orchestrator.state import RunState
from src.schemas.content import SEOAnalysis,Keyword,TopicMap
from src.schemas.decisions import PageEvidence,PageAssessment,ResearchCoverage,SelectionRequest
from src.schemas.icp import ICP
from src.schemas.jobs import Job
from src.services.dashboard import dashboard,summary
from src.services.research_budget import text_upper_price,micros
from src.services.recovery import running_job,UncertainOperation
from src.services.seo_decisions import DecisionService,snapshot_pages,topics,validate_selection
from src.integrations.website_scan import extract_page,WebsiteScan
from src.integrations.dataforseo import DataForSEO,SourceRequestError
from src.integrations.google_search_console import GoogleSearchConsole
from src.services.research import ResearchService,cohort
from tests.conftest import authenticated_headers, own_fixture

def make_state(settings):
    icp=ICP.model_validate_json((settings.input_dir/'icps/icp_1_buy_and_hold.json').read_text(encoding='utf-8'))
    s=RunState(icp=icp,decision_workflow_version=1,status='awaiting_selection')
    s.topic_map=TopicMap.model_validate_json((settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
    s.analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[])
    return s

def test_cost_reservations_are_atomic_and_unknown_requests_keep_allowance(settings):
    storage=SQLiteStorage(settings.data_dir/'budget.sqlite');rid=str(uuid4())
    def reserve(_):
        try:return storage.reserve_research_cost(rid,300000,1000000)
        except BudgetExceeded:return None
    with ThreadPoolExecutor(max_workers=8) as pool:values=list(pool.map(reserve,range(8)))
    assert len([v for v in values if v])==3
    assert storage.research_costs(rid)['reserved_microusd']==900000
    first=next(v for v in values if v);storage.settle_research_cost(first,200000);storage.settle_research_cost(first,0)
    assert storage.research_costs(rid)=={'recorded_microusd':200000,'reserved_microusd':600000}
    with pytest.raises(BudgetExceeded):storage.reserve_research_cost(rid,200001,1000000)
    with pytest.raises(ValueError):storage.reserve_research_cost(rid,1,1000001)

def test_unknown_or_expired_model_price_blocks_before_provider(settings):
    with pytest.raises(ValueError):text_upper_price(settings,SimpleNamespace(litellm_model='unknown/provider'),100,100)
    settings.research_price_rates=json.dumps({'unknown/provider':{'input':1,'output':2,'verified_at':'2020-01-01'}})
    with pytest.raises(ValueError):text_upper_price(settings,SimpleNamespace(litellm_model='unknown/provider'),100,100)
    settings.research_price_rates=json.dumps({'unknown/provider':{'input':1,'output':2,'verified_at':date.today().isoformat()}})
    assert micros(text_upper_price(settings,SimpleNamespace(litellm_model='unknown/provider'),1000,500))==2500

def test_full_elementor_extraction_recognizes_afa_and_privacy_exclusions():
    html='<html><title>AfA Rechner</title><head><style>'+('x'*30000)+'</style></head><body><main>Menü</main><div data-elementor-type="wp-page"><h1>AfA Immobilien</h1><p>Abschreibung und Kaufpreisaufteilung werden erklärt.</p><p>kontakt@example.org</p></div></body></html>'
    page=extract_page(html,'https://steuerkanzlei.example/rechner/afa-immobilien/')
    assert 'Abschreibung' in page['excerpt'] and 'example.org' not in page['excerpt']
    assert page['exclusions']==1 and page['content_truncated']
    assert page['headings'][0]['text']=='AfA Immobilien'
    assert cohort([page])[0]['url']==page['url']
    page['url']='https://steuerkanzlei.example/karriere/'
    assert not cohort([page])

def test_crawler_rejects_other_hosts_and_private_addresses(settings,monkeypatch):
    scanner=WebsiteScan(settings)
    with pytest.raises(ValueError):scanner.validate_url('https://evil.example/path')
    monkeypatch.setattr('src.integrations.website_scan.socket.getaddrinfo',lambda *a,**k:[(0,0,0,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):scanner.validate_url(settings.wordpress_base_url+'/')

def test_weak_rank_is_observation_not_missing_rank_and_draft_requires_professional_review(settings):
    state=make_state(settings);state.research_snapshot={'version':1,'dataforseo':{'usable':True}}
    url='https://steuerkanzlei.example/zielgruppen/immobilien/'
    state.analysis.page_evidence=[PageEvidence(id='page-0',url=url,title='Immobiliensteuerberater',content='Wir betreuen private Vermieter.')]
    state.analysis.existing_pages=[url];state.analysis.coverage=ResearchCoverage(inventory_complete=True,gsc_complete=True)
    state.analysis.keywords=[Keyword(keyword='steuerberater immobilien',source='GSC',page=url,impressions=656,clicks=0,position=44.6,measurement_context={'country':'de'})]
    batch=[PageAssessment(topic_id=state.topic_map.seed_id,page_url=url,relation='dedicated',evidence_quote='Wir betreuen private Vermieter.',reasoning='Gleicher Leserbedarf.')]
    service=DecisionService(SimpleNamespace(settings=settings));r=service._recommend(state,topics(state)[0],batch)
    assert r.action=='investigate' and not r.drafting_eligible
    assert r.measured_keywords==['steuerberater immobilien'] and 'Ranking-Beobachtungen' in r.reasoning
    state.analysis.recommendations=[r]
    selection=SelectionRequest(recommendation_id=r.id,expected_revision=0,acknowledge_uncertainty=True,notes='Die Unsicherheit wurde geprüft.')
    with pytest.raises(ValueError):validate_selection(state,selection)
    selection.notes='Fachlich geprüft: Die vorhandene Seite wird anhand der gespeicherten Textbelege gezielt ergänzt.';selection.professional_support=True
    assert validate_selection(state,selection).target_url==url

def test_dashboard_uses_aggregate_ctr_preserves_zero_and_missing_days(settings):
    state=make_state(settings);storage=SQLiteStorage(settings.data_dir/'dashboard.sqlite')
    state.research_snapshot={'version':1,'budget_microusd':1000000,'windows':{'current':['2026-10-01','2026-10-03'],'previous':['2026-09-01','2026-09-03']},'performance':{'icp_1_de':{
        'current':{'summary':{'available':True,'rows':[{'clicks':2,'impressions':100,'position':29.1}]},'daily':{'rows':[{'keys':['2026-10-01'],'clicks':0,'impressions':19,'position':29.1}]}},
        'previous':{'summary':{'available':True,'rows':[{'clicks':0,'impressions':20,'position':40}]}}}}}
    data=dashboard(state,storage)
    assert data['current']['ctr']==.02 and data['previous']['clicks']==0
    assert data['changes']['clicks']['percent'] is None
    assert data['daily'][0]['clicks']==0 and data['daily'][1]['clicks'] is None
    assert len(data['pillars'])==len(state.topic_map.pillars) and all(p['research_needed'] for p in data['pillars'])
    with pytest.raises(ValueError):summary({'available':True,'rows':[{},{}]})

def test_pdf_and_dashboard_share_values_and_legacy_has_no_fake_timeline(settings):
    from src.services.analysis_pdf import create_report
    state=make_state(settings);storage=SQLiteStorage(settings.data_dir/'pdf.sqlite')
    data=dashboard(state,storage)
    assert data['current']['clicks'] is None and not data['daily']
    pdf=create_report(data);assert pdf.startswith(b'%PDF-') and len(pdf)>3000
    data['created_at']='2026-10-13T12:00:00+00:00'
    assert create_report(data).startswith(b'%PDF-')

def test_dataforseo_empty_overview_stops_expansion_and_missing_is_not_zero(settings,monkeypatch):
    calls=[]
    def lookup(self,route,task):calls.append(route);return []
    monkeypatch.setattr(DataForSEO,'_lookup',lookup)
    rows,serps,diag=DataForSEO(settings).research(['Immobiliensteuerberater'],['AfA Immobilien'])
    assert not rows and not serps and calls==['keyword_overview']
    rows=DataForSEO.parse_keywords([{'keyword':'afa immobilien','keyword_info':{'search_volume':0}}],'overview',{'language_code':'de'})
    assert rows[0].volume==0 and rows[0].difficulty is None
    with pytest.raises(SourceRequestError):DataForSEO.parse_keywords([{'unexpected':1}],'overview',{})

def test_research_refresh_api_authenticated_idempotent_and_preserves_parent(settings,monkeypatch):
    from src.api.app import create_app
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();storage=client.app.state.workflow.storage;parent=make_state(settings);parent.status='exported';storage.save_run_state(parent)
        payload={'kind':'research_refresh','run_id':str(parent.run_id),'research_budget_usd':1,'idempotency_key':str(uuid4())}
        assert client.post('/jobs',json=payload).status_code==401
        headers=authenticated_headers(client,'Sam');own_fixture(client,parent);before=storage.get_run_state(str(parent.run_id)).model_dump()
        response=client.post('/jobs',headers=headers,json=payload);assert response.status_code==202,response.text
        child=response.json()['run_id'];assert child!=str(parent.run_id)
        assert client.post('/jobs',headers=headers,json=payload).json()['id']==response.json()['id']
        assert storage.get_run_state(str(parent.run_id)).model_dump()==before
        assert client.get('/runs/'+child+'/dashboard?scope=site&country=all').status_code==200
        assert client.get('/runs/'+child+'/dashboard?scope=bad').status_code==422
        assert client.get('/runs/'+child+'/analysis-report.pdf').content.startswith(b'%PDF-')
        assert client.post('/jobs',headers=headers,json={**payload,'idempotency_key':str(uuid4()),'research_budget_usd':1.01}).status_code==422

def test_complete_research_job_retains_pillars_sources_and_restart_does_not_repeat(settings,monkeypatch):
    from src.orchestrator.workflow import Workflow
    workflow=Workflow(settings);storage=workflow.storage;parent=make_state(settings);parent.status='exported';storage.save_run_state(parent)
    child=RunState(icp=parent.icp,parent_run_id=parent.run_id,decision_workflow_version=1);storage.save_run_state(child)
    calls={'gsc':0,'web':0,'dfs':0}
    def query(self,start,end,dimensions,country='all',urls=None):
        calls['gsc']+=1
        return {'available':True,'complete':True,'rows':[],'aggregation':'byProperty' if urls is None else 'byPage'}
    def website(self,*args):
        calls['web']+=1
        return [extract_page('<h1>AfA Immobilien</h1><p>Abschreibung wird erklärt.</p>','https://steuerkanzlei.example/afa/')],{'inventory_complete':True,'pages_retrieved':1,'limitations':[]}
    def dfs(self,*args):calls['dfs']+=1;return [],[],[]
    monkeypatch.setattr(GoogleSearchConsole,'query_window',query);monkeypatch.setattr(WebsiteScan,'collect',website);monkeypatch.setattr(DataForSEO,'research',dfs)
    job=Job(run_id=child.run_id,kind='research_refresh',idempotency_key=uuid4(),reviewer='Sam',payload={'budget_usd':1})
    with running_job(job,storage):result=ResearchService(workflow).execute(str(child.run_id))
    assert result.status=='awaiting_selection' and 6<=len(result.topic_map.pillars)<=8
    assert result.topic_map.model_dump()==parent.topic_map.model_dump()
    assert all(r.research_needed and not r.drafting_eligible for r in result.analysis.recommendations)
    assert storage.get_run_state(str(parent.run_id)).status=='exported'
    before=dict(calls)
    with running_job(job,storage):ResearchService(workflow).execute(str(child.run_id))
    assert calls==before
    assert workflow.graph.get_state(workflow._config(str(child.run_id))).next==('planning_selection',)
    workflow.stack.close()


def test_paid_dfs_cache_is_encrypted_reused_by_child_and_timeout_reservation_survives(settings,monkeypatch):
    import httpx
    settings.dataforseo_login='fake-login';settings.dataforseo_password='fake-password'
    storage=SQLiteStorage(settings.data_dir/'paid-cache.sqlite');parent=make_state(settings);storage.save_run_state(parent)
    child=RunState(icp=parent.icp,parent_run_id=parent.run_id);storage.save_run_state(child)
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,json):
            calls.append(url)
            if 'related_keywords' in url:raise httpx.ReadTimeout('unknown outcome')
            return httpx.Response(200,request=httpx.Request('POST',url),json={'status_code':20000,'cost':.01212,'tasks':[
                {'id':'task-one','status_code':20000,'result':[{'items':[{'keyword':'afa immobilien','keyword_info':{'search_volume':0}}]}]}]})
    monkeypatch.setattr('src.integrations.dataforseo.httpx.Client',Client)
    task={'keywords':['afa immobilien'],'location_code':2276,'language_code':'de'}
    first=Job(run_id=parent.run_id,kind='research_refresh',reviewer='Sam',idempotency_key=uuid4(),payload={'budget_usd':1})
    with running_job(first,storage):DataForSEO(settings)._lookup('keyword_overview',task)
    assert storage.research_costs(str(parent.run_id))['recorded_microusd']==12120
    saved=next(o for o in storage.list_operations(str(parent.run_id)) if o['status']=='completed')
    assert 'body' not in saved['value'] and 'afa immobilien' not in json.dumps(saved['value'])
    second=Job(run_id=child.run_id,kind='research_refresh',reviewer='Sam',idempotency_key=uuid4(),payload={'budget_usd':1})
    with running_job(second,storage):
        dfs=DataForSEO(settings);assert dfs._lookup('keyword_overview',task)[0]['keyword_info']['search_volume']==0
        assert dfs.diagnostics[0]['reused'] and dfs.diagnostics[0]['cost']==0
        for _ in range(2):
            with pytest.raises(UncertainOperation):dfs._lookup('related_keywords',{'keyword':'afa immobilien'})
    assert len(calls)==2 and storage.research_costs(str(child.run_id))['reserved_microusd']==100000


def test_gsc_aggregate_filters_use_final_data_and_original_rows(settings,monkeypatch):
    import httpx
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,json):
            calls.append(json)
            return httpx.Response(200,request=httpx.Request('POST',url),json={'responseAggregationType':'byProperty' if json['aggregationType']=='byProperty' else 'byPage',
                'rows':[{'keys':[],'clicks':2,'impressions':100,'ctr':.02,'position':44.6}]})
    monkeypatch.setattr(GoogleSearchConsole,'access_token',lambda _: 'fake')
    monkeypatch.setattr('src.integrations.google_search_console.httpx.Client',Client)
    gsc=GoogleSearchConsole(settings)
    whole=gsc.query_window(date(2026,7,13),date(2026,10,10),[],'all')
    selected=gsc.query_window(date(2026,7,13),date(2026,10,10),[],'de',['https://steuerkanzlei.example/afa/'])
    assert whole['aggregation']=='byProperty' and selected['aggregation']=='byPage'
    assert calls[0]['dataState']=='final' and 'dimensionFilterGroups' not in calls[0]
    assert selected['filters'][0]['expression']=='deu' and 'afa/' in selected['filters'][1]['expression']
    assert selected['rows'][0]['clicks']==2 and selected['retrieved_at']
    assert not gsc.query_window(date.today(),date.today(),[],'de',[])['available']


def test_sitemap_custom_content_and_robots_reconcile_complete_inventory(settings,monkeypatch):
    import httpx
    scanner=WebsiteScan(settings);base=scanner.base;calls=[]
    monkeypatch.setattr(scanner,'validate_url',lambda _:None)
    def get(client,url):
        calls.append(url)
        if url.endswith('robots.txt'):return ('User-agent: *\nAllow: /\nSitemap: '+base+'/sitemap.xml').encode(),url,{}
        if url.endswith('sitemap.xml'):return ('<urlset><url><loc>'+base+'/branche/immobilien/</loc></url><url><loc>'+base+'/afa/</loc></url></urlset>').encode(),url,{}
        if '/wp-json/' in url:return json.dumps([{'link':base+'/branche/immobilien/'}] if '/branchen?' in url else []).encode(),url,{'x-wp-totalpages':'1'}
        return b'<html><title>Immobilien</title><h1>AfA Immobilien</h1><p>Die Abschreibung wird hier bereits behandelt.</p></html>',url,{'content-type':'text/html'}
    monkeypatch.setattr(scanner,'get',get)
    pages,coverage=scanner.collect([base+'/afa/'])
    assert len(pages)==2 and coverage['inventory_complete'] and any('/branchen?' in u for u in calls)
    assert any('bereits behandelt' in s['text'] for p in pages for s in p['sections'])


def test_research_creation_is_atomic_for_same_parent(settings):
    storage=SQLiteStorage(settings.data_dir/'refresh.sqlite');parent=make_state(settings);storage.save_run_state(parent)
    def create(_):
        child=RunState(icp=parent.icp,parent_run_id=parent.run_id)
        job=Job(run_id=child.run_id,kind='research_refresh',reviewer='Sam',idempotency_key=uuid4(),payload={'budget_usd':1,'parent_run_id':str(parent.run_id)})
        try:storage.create_job(job,child);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(create,range(5)))
    assert sum(results)==1 and len(storage.list_runs())==2


def test_selected_package_is_immutable_and_shared_across_all_mocked_agents(settings,monkeypatch):
    import copy,hashlib
    from src.orchestrator.workflow import Workflow
    from src.services.seo_decisions import selected_context
    from src.schemas.content import Briefing,Article,CheckResult
    from src.agents.briefing_agent import BriefingAgent
    from src.agents.content_agent import ContentAgent
    from src.agents.brand_agent import BrandAgent
    from src.agents.seo_qa_agent import SEOQAAgent
    settings.demo_mode=False
    with Workflow(settings) as workflow:
        state=make_state(settings);state.research_snapshot={'version':1}
        package={'audience':state.icp.model_dump(mode='json'),'metrics':[{'clicks':2,'impressions':19}],
            'uncertainties':['Search volume unavailable'],'research_run_id':str(state.run_id)}
        package['evidence_hash']=hashlib.sha256(json.dumps(package,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        state.selected_evidence=package;seen=[]
        def generate(run_id,model_type,task,payload,**kwargs):
            evidence=payload.get('selected_context',payload.get('selected_evidence'));seen.append(copy.deepcopy(evidence))
            if model_type is CheckResult:return CheckResult(passed=True,errors=[],warnings=[],target='content_agent')
            filename='briefing.json' if model_type is Briefing else 'article.json'
            return model_type.model_validate_json((settings.input_dir/'fixtures'/filename).read_text(encoding='utf-8'))
        monkeypatch.setattr(workflow.context.llm,'generate',generate)
        for agent in [BriefingAgent,ContentAgent,BrandAgent,SEOQAAgent]:state=agent(workflow.context).execute(state)
        assert len(seen)==4 and all(p==package for p in seen) and state.seo_check.passed
        returned=selected_context(state);returned['metrics'][0]['clicks']=0
        assert state.selected_evidence['metrics'][0]['clicks']==2
        state.selected_evidence['metrics'][0]['clicks']=0
        with pytest.raises(ValueError):selected_context(state)


def test_invalid_coverage_claims_are_downgraded_and_new_content_needs_verified_parent(settings):
    from src.schemas.decisions import AssessmentBatch
    state=make_state(settings);state.research_snapshot={'version':1}
    url='https://steuerkanzlei.example/afa/'
    state.analysis.page_evidence=[PageEvidence(id='page-0',url=url,title='AfA Immobilien',content='AfA und Abschreibung werden erklärt.')]
    topic=topics(state)[1]
    batch=AssessmentBatch(assessments=[PageAssessment(topic_id=topic['id'],page_url=url,relation='dedicated',
        evidence_quote='AfA und Abschreibung werden erklärt.',issue='Eine AfA-Erklärung fehlt.',reasoning='Unbelegte Abwesenheit')])
    recs=DecisionService(SimpleNamespace(settings=settings)).build(state,batch)
    assert state.analysis.page_assessments[0].relation=='uncertain'
    assert all(not r.drafting_eligible for r in recs)
    state.analysis.recommendations=recs
    chosen=next(r for r in recs if r.topic_id==topic['id'])
    request=SelectionRequest(recommendation_id=chosen.id,expected_revision=0,professional_support=True,acknowledge_uncertainty=True,
        notes='Fachliche Einschätzung: ergänzender Bedarf für private Investoren bestätigt, Abdeckung vor Veröffentlichung prüfen.')
    with pytest.raises(ValueError):validate_selection(state,request)
    request.verified_parent_url=url
    assert validate_selection(state,request).parent_url==url


def test_private_investor_cohort_excludes_commercial_developers_and_noindex():
    private=extract_page('<title>Steuerberater für Vermietung</title><h1>Private Vermieter</h1><p>Ihre Anlage V und vermieteten Immobilien.</p>',
        'https://steuerkanzlei.example/vermietung/')
    commercial=extract_page('<title>Steuerberater Immobilienunternehmen</title><h1>Immobilien</h1><p>Immobiliengesellschaften und Projektentwickler.</p>',
        'https://steuerkanzlei.example/unternehmen/')
    noindex=extract_page('<title>AfA Immobilien</title><meta name="robots" content="noindex,follow"><h1>AfA</h1><p>Abschreibung</p>',
        'https://steuerkanzlei.example/afa/')
    assert cohort([private,commercial,noindex])==cohort([private])


def test_shared_template_sections_stay_traceable_but_are_omitted_from_research_excerpts():
    from src.integrations.website_scan import remove_repeated_templates
    pages=[extract_page(f'<h1>Seite {i}</h1><p>Wiederholtes Kanzlei-Template</p><p>Individueller Immobilieninhalt {i}</p>',
        f'https://steuerkanzlei.example/page-{i}/') for i in range(6)]
    assert remove_repeated_templates(pages)==6
    assert all('Kanzlei-Template' not in p['excerpt'] for p in pages)
    assert all(any(s['template'] and s['text']=='Wiederholtes Kanzlei-Template' for s in p['sections']) for p in pages)


def test_research_ai_reserves_usd_before_submission_and_reuse_does_not_charge_again(settings,monkeypatch):
    import litellm
    from src.services.llm import ModelRouter
    from src.schemas.content import CheckResult
    from tests.test_paid_recovery import live_settings,model_response
    from src.orchestrator.workflow import Workflow
    live_settings(settings)
    monkeypatch.setattr(litellm,'token_counter',lambda **kwargs:1)
    with Workflow(settings) as workflow:
        run=make_state(settings);job=Job(run_id=run.run_id,kind='research_refresh',reviewer='Sam',idempotency_key=uuid4(),payload={'budget_usd':1})
        workflow.storage.create_job(job,run);calls=[]
        def completion(**kwargs):
            assert workflow.storage.research_costs(str(run.run_id))['reserved_microusd']>0
            calls.append(1);return model_response()
        monkeypatch.setattr(litellm,'completion',completion)
        with running_job(job,workflow.storage):
            router=ModelRouter(settings,workflow.storage)
            assert router.generate(str(run.run_id),CheckResult,'Check',{},model_role='seo_strategy').passed
            before=workflow.storage.research_costs(str(run.run_id))
            assert router.generate(str(run.run_id),CheckResult,'Check',{},model_role='seo_strategy').passed
        assert calls==[1] and before==workflow.storage.research_costs(str(run.run_id))
        assert before['reserved_microusd']==0 and before['recorded_microusd']>0


def test_partial_keywords_survive_endpoint_failure_and_local_gsc_observations_support_investigation(settings,monkeypatch):
    calls=[]
    def lookup(self,route,task):
        calls.append(route)
        if route=='related_keywords':raise SourceRequestError('source_api_error')
        return [{'keyword':'immobiliensteuerberater','keyword_info':{'search_volume':1000}}]
    monkeypatch.setattr(DataForSEO,'_lookup',lookup)
    with pytest.raises(SourceRequestError) as error:DataForSEO(settings).research(['immobiliensteuerberater'],['afa immobilien'])
    assert len(error.value.keywords)==1 and calls==['keyword_overview','related_keywords']
    state=make_state(settings);state.research_snapshot={'version':1,'dataforseo':{'usable':True,'status':'source_api_error'}}
    url='https://steuerkanzlei.example/immobilien/'
    state.analysis.page_evidence=[PageEvidence(id='page-0',url=url,title='Immobiliensteuerberater',content='Private Vermieter werden unterstützt.')]
    state.analysis.keywords=[Keyword(keyword='steuerberater immobilien',source='GSC',page=url,clicks=0,impressions=656,position=44.6,
        reporting_start=date(2026,7,12),reporting_end=date(2026,10,9),measurement_context={'country':'de'}),error.value.keywords[0]]
    service=DecisionService(SimpleNamespace(settings=settings))
    batch=ResearchService.local_assessments(state,service)
    recs=service.build(state,batch)
    seed=next(r for r in recs if r.topic_id==state.topic_map.seed_id)
    assert seed.action=='investigate' and seed.target_url==url and not seed.drafting_eligible
    assert not any('Schätzwerte sind nicht verfügbar' in u for u in seed.uncertainties)
