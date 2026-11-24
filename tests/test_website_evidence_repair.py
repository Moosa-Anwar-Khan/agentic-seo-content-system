"""Regression cases from the live coverage incident; all providers are mocked."""
import copy
import json
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient
from src.integrations.website_scan import WebsiteScan,extract_page,ExternalRedirect
from src.integrations.dataforseo import DataForSEO
from src.integrations.google_search_console import GoogleSearchConsole
from src.orchestrator.state import RunState
from src.orchestrator.workflow import Workflow
from src.schemas.jobs import Job
from src.schemas.content import Keyword,CheckResult
from src.schemas.decisions import PageAssessment,AssessmentBatch,ResearchCoverage,SelectionRequest
from src.services.recovery import running_job
from src.services.research import ResearchService,cohort
from src.services.website_evidence import scoped_coverage,contextual_changes
from src.services.seo_decisions import DecisionService,snapshot_pages,topics,validate_selection
from src.services.dashboard import dashboard
from src.services.llm import ModelRouter
from src.storage.sqlite_storage import SQLiteStorage
from tests.conftest import authenticated_headers, own_fixture
from tests.test_verified_research import make_state


def test_external_redirects_are_accounted_and_never_fetched(settings,monkeypatch):
    scanner=WebsiteScan(settings);base=scanner.base;seen=[]
    monkeypatch.setattr(scanner,'validate_url',lambda _:None)
    monkeypatch.setattr('src.integrations.website_scan.time.sleep',lambda _:None)
    original=httpx.Client
    def response(request):
        seen.append(str(request.url));url=str(request.url)
        if url.endswith('robots.txt'):return httpx.Response(200,text='User-agent: *\nAllow: /\nSitemap: '+base+'/sitemap.xml')
        if url.endswith('sitemap.xml'):
            return httpx.Response(200,text='<urlset><url><loc>'+base+'/old/</loc></url><url><loc>'+base+'/afa/</loc></url></urlset>')
        if '/wp-json/' in url:return httpx.Response(200,json=[],headers={'x-wp-totalpages':'1'})
        if url.endswith('/old/'):return httpx.Response(302,headers={'location':'https://other-firm.example/service/?tracking=1'})
        return httpx.Response(200,text='<h1>AfA Immobilien</h1><p>Die Abschreibung wird erklärt.</p>',headers={'content-type':'text/html'})
    monkeypatch.setattr('src.integrations.website_scan.httpx.Client',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    pages,coverage=scanner.collect()
    assert len(pages)==1 and coverage['inventory_complete'] and coverage['content_complete']
    assert coverage['resolved_html_urls']==2 and coverage['external_redirects']==1 and not coverage['failures']
    assert coverage['excluded'][0]['destination']=='https://other-firm.example/service/'
    assert not any('other-firm' in u for u in seen)


def test_free_get_retries_transport_only_without_leaving_host(settings,monkeypatch):
    scanner=WebsiteScan(settings);monkeypatch.setattr(scanner,'validate_url',lambda _:None)
    monkeypatch.setattr('src.integrations.website_scan.time.sleep',lambda _:None)
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        if len(calls)==1:raise httpx.ReadTimeout('temporary')
        return httpx.Response(200,text='ok')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert scanner.get(client,scanner.base+'/page/')[0]==b'ok'
    assert len(calls)==2


def test_elementor_accordion_questions_and_h4_are_saved_without_contact_data():
    page=extract_page('<div data-elementor-type="wp-page"><h1>Immobilien</h1><h4>Unterlagen</h4>'
        '<div class="elementor-tab-title"><a>Welche Unterlagen brauchen private Vermieter?</a></div>'
        '<p>Die Objektübersicht hilft bei der Vorbereitung.</p><p>kontakt@example.org</p></div>',
        'https://steuerkanzlei.example/immobilien/')
    assert any(s['text']=='Welche Unterlagen brauchen private Vermieter?' for s in page['sections'])
    assert any(h['level']==4 for h in page['headings'])
    assert 'example.org' not in json.dumps(page) and page['content_truncated']
    assert page['content_exclusions']==[{'reason':'privacy_or_untrusted_text','count':1}]


def test_javascript_only_heading_is_not_complete_content():
    page=extract_page('<h1>Immobilien</h1><script>renderContent()</script>','https://steuerkanzlei.example/immobilien/')
    assert page['content_truncated'] and page['content_exclusions'][0]['reason']=='no_substantive_text'


def test_malformed_policy_link_cannot_discard_page_or_leak_contact_values():
    page=extract_page('<h1>Datenschutz</h1><p>Allgemeine Angaben zum Datenschutz.</p>'
        '<a href="https://[invalid.example">Vorlage</a><a href="/contact/?email=info@example.org">Kontakt</a>'
        '<a href="/impressum/">Impressum</a>','https://steuerkanzlei.example/datenschutz/')
    assert page['title']=='' and page['sections'] and not page['links_complete']
    assert page['internal_links']==['https://steuerkanzlei.example/impressum/']
    assert 'info@example.org' not in json.dumps(page)
    assert page['content_exclusions'][-1]['reason']=='malformed_or_unsafe_link'


def test_template_contact_h1_does_not_override_primary_destination_heading(settings):
    state=supported_state(settings)
    from src.schemas.decisions import PageHeading
    state.analysis.page_evidence[1].headings.append(PageHeading(level=1,text='So werden Sie Mandant bei Muster & Partner'))
    assert contextual_changes(state,state.analysis.page_evidence[0].url)[0].anchor=='Vermietung & Verpachtung'


def private_pages():
    base='https://steuerkanzlei.example'
    main=extract_page('<title>Steuerberater für Immobilien</title><h1>Steuerberater für Immobilien</h1>'
        '<p>Wir unterstützen private Vermieter und Immobilieninvestoren.</p>'
        '<h3>Vermietung &amp; Verpachtung</h3><p>Steuererklärung und laufende Begleitung.</p>',base+'/immobilien/')
    rental=extract_page('<title>Steuerberater für Vermietung und Verpachtung</title>'
        '<h1>Steuerberater für Vermietung und Verpachtung</h1><p>Wir begleiten private Vermieter.</p>',base+'/vermietung/')
    return [main,rental]


def supported_state(settings):
    state=make_state(settings);pages=private_pages()
    state.analysis.page_evidence=snapshot_pages(pages,settings.wordpress_base_url)
    state.analysis.coverage=ResearchCoverage(inventory_complete=True,content_complete=False,gsc_complete=True)
    state.analysis.keywords=[Keyword(keyword='steuerberater immobilien',page=pages[0]['url'],source='GSC',
        impressions=656,clicks=0,position=44.6,measurement_context={'country':'de'})]
    state.research_snapshot={'version':2,'cohort':cohort(pages),'dataforseo':{'status':'usable'},'website':{'url_outcomes':[]}}
    return state


def test_contextual_link_is_supported_without_unlocking_full_rewrite(settings):
    state=supported_state(settings);page=state.analysis.page_evidence[0]
    service=DecisionService(SimpleNamespace(settings=settings))
    assessment=PageAssessment(topic_id=state.topic_map.seed_id,page_url=page.url,relation='dedicated',
        evidence_quote=page.headings[0].text,reasoning='Passende Leistungsseite.')
    rec=service._recommend(state,topics(state)[0],[assessment]);state.analysis.recommendations=[rec]
    assert rec.action=='investigate' and not rec.drafting_eligible and rec.links_ready
    assert rec.supported_changes[0].evidence_quote=='Vermietung & Verpachtung'
    assert rec.supported_changes[0].target_url.endswith('/vermietung/')
    assert rec.internal_link_suggestions[0].source_url==page.url
    request=SelectionRequest(recommendation_id=rec.id,expected_revision=0,mode='links_only')
    assert validate_selection(state,request).links_ready
    request.mode='draft'
    with pytest.raises(ValueError):validate_selection(state,request)
    rec.supported_changes[0].evidence_quote='Invented wording'
    with pytest.raises(ValueError):DecisionService.validate_recommendations(state,[rec])


@pytest.mark.parametrize('condition',['already_linked','redirect_alias','truncated_source','noindex_target','unverified_target'])
def test_contextual_link_cannot_ignore_existing_link_or_incomplete_evidence(settings,condition):
    state=supported_state(settings);source,target=state.analysis.page_evidence
    if condition=='already_linked':source.internal_links=[target.url]
    if condition=='redirect_alias':
        source.internal_links=['https://steuerkanzlei.example/old-rental/']
        state.research_snapshot['website']['url_outcomes']=[{'url':source.internal_links[0],'final_url':target.url}]
    if condition=='truncated_source':source.content_truncated=True
    if condition=='noindex_target':target.indexable=False
    if condition=='unverified_target':state.research_snapshot['cohort']=[]
    assert not contextual_changes(state,source.url)


def test_verified_icp_completeness_does_not_hide_site_redactions(settings):
    pages=private_pages();other=extract_page('<h1>Kontakt</h1><p>kontakt@example.org</p>','https://steuerkanzlei.example/contact/')
    website={'inventory_complete':True,'content_complete':False,'failures':[],
             'content_limitations':[{'url':other['url'],'exclusions':other['content_exclusions']}]}
    scope=scoped_coverage(pages+[other],website,cohort(pages))
    assert scope['icp_1']['complete'] and not scope['site']['complete']
    state=supported_state(settings);storage=SQLiteStorage(settings.data_dir/'scope.sqlite')
    values={'available':True,'rows':[{'clicks':0,'impressions':100,'position':44.6}]}
    state.research_snapshot.update(scope_coverage=scope,report_readiness='partial',budget_microusd=0,
        performance={key:{p:{'summary':values} for p in ['current','previous']} for key in ['icp_1_de','site_de']})
    assert dashboard(state,storage)['report_readiness']=='ready'
    assert dashboard(state,storage,scope='site')['report_readiness']=='partial'
    assert dashboard(state,storage)['overall_report_readiness']=='partial'
    assert dashboard(state,storage)['budget']['limit_usd']==0
    website['failures']=[{'url':'https://steuerkanzlei.example/immobilien-missing/','reason':'robots_blocked'}]
    assert not scoped_coverage(pages,website,cohort(pages))['icp_1']['complete']


def test_repair_guard_blocks_ai_and_dataforseo_before_any_request(settings,monkeypatch):
    storage=SQLiteStorage(settings.data_dir/'guard.sqlite');rid=str(uuid4())
    job=Job(run_id=rid,kind='research_repair',idempotency_key=uuid4(),reviewer='Sam',payload={'budget_usd':0})
    with running_job(job,storage):
        with pytest.raises(PermissionError):DataForSEO(settings)._lookup('keyword_overview',{'keywords':['afa']})
        with pytest.raises(PermissionError):ModelRouter(settings,storage).generate(rid,CheckResult,'check',{})
    assert storage.research_costs(rid)=={'recorded_microusd':0,'reserved_microusd':0}


def test_free_repair_reuses_verified_quotes_and_preserves_parent(settings,monkeypatch):
    with Workflow(settings) as workflow:
        parent=supported_state(settings);parent.research_snapshot.update(completed=True,dataforseo={
            'status':'usable','usable':True,'keywords':[],'serps':[],
            'diagnostics':[{'retrieved_at':datetime.now(timezone.utc).isoformat()}]})
        parent.analysis.page_assessments=[PageAssessment(topic_id=parent.topic_map.seed_id,
            page_url=parent.analysis.page_evidence[0].url,relation='dedicated',evidence_quote='Steuerberater für Immobilien',
            issue='Old unsupported issue',reasoning='Earlier assessment.')]
        before=parent.model_dump(mode='json');workflow.storage.save_run_state(parent)
        child=RunState(icp=parent.icp,parent_run_id=parent.run_id,decision_workflow_version=1)
        job=Job(run_id=child.run_id,kind='research_repair',reviewer='Sam',idempotency_key=uuid4(),payload={'budget_usd':0})
        workflow.storage.create_job(job,child)
        def query(self,*args,**kwargs):return {'available':True,'complete':True,'rows':[]}
        monkeypatch.setattr(GoogleSearchConsole,'query_window',query)
        monkeypatch.setattr(WebsiteScan,'collect',lambda *args:(private_pages(),{'inventory_complete':True,'content_complete':True,'limitations':[]}))
        def forbidden(*args,**kwargs):raise AssertionError('Repair attempted a new paid request')
        monkeypatch.setattr(DataForSEO,'research',forbidden);monkeypatch.setattr(workflow.context.llm,'generate',forbidden)
        with running_job(job,workflow.storage):result=ResearchService(workflow).execute(str(child.run_id))
        assert result.status=='awaiting_selection' and result.research_snapshot['assessment_reuse']['relationships_verified']==1
        assert result.analysis.page_assessments[0].issue=='' and result.analysis.recommendations[0].supported_changes
        assert workflow.storage.get_run_state(str(parent.run_id)).model_dump(mode='json')==before
        assert workflow.storage.research_costs(str(child.run_id))=={'recorded_microusd':0,'reserved_microusd':0}


def test_repair_api_requires_auth_completed_parent_and_rejects_paid_budget(settings):
    from src.api.app import create_app
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();db=client.app.state.workflow.storage
        parent=make_state(settings);db.save_run_state(parent)
        payload={'kind':'research_repair','run_id':str(parent.run_id),'idempotency_key':str(uuid4())}
        assert client.post('/jobs',json=payload).status_code==401
        headers=authenticated_headers(client,'Sam');own_fixture(client,parent)
        assert client.post('/jobs',json=payload,headers=headers).status_code==409
        parent.research_snapshot={'completed':True};db.save_run_state(parent)
        assert client.post('/jobs',json={**payload,'research_budget_usd':1},headers=headers).status_code==422
        result=client.post('/jobs',json=payload,headers=headers);assert result.status_code==202,result.text
        assert db.get_job(result.json()['id']).payload['budget_usd']==0
        assert client.post('/jobs',json=payload,headers=headers).json()['id']==result.json()['id']
