"""Paid-call regressions use fake responses and cannot reach external providers."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from src.api.app import create_app
from src.config.settings import Settings
from src.integrations.dataforseo import DataForSEO, SourceRequestError
from src.integrations.sources import SourceCollection
from src.agents.base import AgentContext
from src.agents.seo_strategy_agent import SEOStrategyAgent
from src.orchestrator.state import RunState
from src.orchestrator.workflow import Workflow
from src.schemas.content import Keyword, CheckResult, SEOInterpretation, TopicMap
from src.schemas.decisions import ResearchCoverage
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.services.llm import ModelRouter, ModelRequestError
from src.services.model_routes import resolve_route, text_request_options
from src.services.recovery import running_job, UncertainOperation
from tests.conftest import authenticated_headers


def model_response(content='{"passed":true}', finish='stop', output=20):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(content=content, reasoning_content='private reasoning must never be stored'))],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=output, total_tokens=20+output,
                              completion_tokens_details=SimpleNamespace(reasoning_tokens=0)),
        _hidden_params={'response_cost': 0.001})


def live_settings(settings):
    settings.demo_mode = False
    settings.deepseek_api_key = 'fake-deepseek'
    settings.openai_api_key = 'fake-openai'
    settings.seo_strategy_provider = 'deepseek'
    settings.seo_strategy_model = 'deepseek-flash'
    return settings


def test_interpretation_never_reproduces_measured_rows(settings):
    settings.demo_mode = False
    records = [Keyword(keyword=f'Immobiliensteuerberater {i}', volume=i, source='DataForSEO',
                       reporting_start=date(2026, 7, 1), reporting_end=date(2026, 9, 28)) for i in range(601)]
    before = [k.model_dump(mode='json') for k in records]
    pages = [{'url': settings.wordpress_base_url+'/page', 'title': 'Immobiliensteuerberater',
              'excerpt': 'Wir begleiten Sie bei Ihren Fragen.', 'headings': [], 'content_truncated': False}]
    captured = []
    class Router:
        def generate(self, run_id, schema, task, payload, **kwargs):
            captured.append(payload)
            assert schema is SEOInterpretation and 'keywords' not in schema.model_json_schema()['properties']
            return SEOInterpretation(opportunities=['Prüfen Sie den vorhandenen Überblick.'], gaps=[], limitations=[])
    collection = SourceCollection(records, pages, ResearchCoverage())
    state = SEOStrategyAgent(AgentContext(settings, None, Router(),
                            SimpleNamespace(collect=lambda _: collection))).execute(RunState(icp=icp_1_buy_and_hold))
    assert len(captured[0]['keywords']) == 50
    assert [k.model_dump(mode='json') for k in state.analysis.keywords] == before
    assert state.analysis.existing_pages == [pages[0]['url']]


@pytest.mark.parametrize('options', ['[]', '{"content":{"api_key":"secret"}}',
                                   '{"content":{"max_output_tokens":20000}}',
                                   '{"content":{"reasoning_effort":"invalid"}}'])
def test_task_options_reject_unsafe_values(options):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_task_options=options)


def test_json_mode_reasoning_off_and_output_budget(settings, monkeypatch):
    import litellm
    live_settings(settings)
    calls = []
    monkeypatch.setattr(litellm, 'completion', lambda **kwargs: calls.append(kwargs) or
                        model_response('{"opportunities":[],"gaps":[],"limitations":[]}'))
    with Workflow(settings) as workflow:
        ModelRouter(settings, workflow.storage).generate(str(uuid4()), SEOInterpretation, 'Interpret evidence', {}, model_role='seo_strategy')
    assert calls[0]['max_tokens'] == 2000
    assert calls[0]['response_format'] == {'type': 'json_object'}
    assert calls[0]['reasoning_effort'] == 'none'
    assert calls[0]['num_retries'] == 0


def test_default_test_settings_never_load_live_credentials():
    isolated = Settings()
    assert isolated.demo_mode and not isolated.deepseek_api_key.get_secret_value()
    assert not isolated.dataforseo_password.get_secret_value()
    assert isolated.gsc_service_account_file is None
    assert 'muster-offline-tests-' in str(isolated.data_dir)


def test_external_network_is_blocked():
    import socket
    with socket.socket() as connection, pytest.raises(AssertionError, match='External networking'):
        connection.connect(('203.0.113.1', 443))


def test_task_options_and_reasoning_sampling_compatibility(settings):
    settings.content_provider='openai'
    settings.content_model='gpt-5.4-mini'
    options=text_request_options(settings,resolve_route(settings,'content'),'SelectedArticle')
    assert 'temperature' not in options  # The default reasoning mode may reject it.
    settings.ai_task_options='{"content":{"max_output_tokens":3500,"reasoning_effort":"none"}}'
    options=text_request_options(settings,resolve_route(settings,'content'),'SelectedArticle')
    assert options['max_tokens']==3500 and options['reasoning_effort']=='none'


def test_known_failure_has_safe_job_error_and_paid_retry_warning(settings, monkeypatch):
    from src.services.jobs import JobWorker
    from src.schemas.jobs import view
    with Workflow(settings) as workflow:
        job=Job(run_id=uuid4(),kind='start',reviewer='Sam',idempotency_key=uuid4())
        workflow.storage.create_job(job)
        worker=JobWorker(settings,workflow)
        route=resolve_route(settings,'seo_strategy')
        def fail(_): raise ModelRequestError(route,'output_limit')
        monkeypatch.setattr(worker,'_execute',fail)
        assert worker.process_one()
        failed=view(workflow.storage.get_job(str(job.id)))
        assert failed.error_code=='output_limit' and failed.requires_paid_retry
        assert 'Ausgabelimit' in failed.error
        assert not worker.process_one()  # Failed jobs never automatically requeue.


def test_uncertain_model_call_never_automatically_falls_back(settings, monkeypatch):
    import litellm
    live_settings(settings)
    settings.seo_strategy_fallback_provider='openai'
    settings.seo_strategy_fallback_model='gpt-4.1-mini'
    calls=[]
    def timeout(**kwargs):
        calls.append(kwargs['model'])
        raise litellm.Timeout(message='simulated response loss',model=kwargs['model'],llm_provider='deepseek')
    monkeypatch.setattr(litellm,'completion',timeout)
    with Workflow(settings) as workflow:
        job=Job(run_id=uuid4(),kind='start',reviewer='Sam',idempotency_key=uuid4())
        with running_job(job,workflow.storage):
            router=ModelRouter(settings,workflow.storage)
            for _ in range(2):
                with pytest.raises(UncertainOperation):
                    router.generate(str(job.run_id),CheckResult,'Check',{},model_role='seo_strategy')
        assert calls==['deepseek/deepseek-flash']


@pytest.mark.parametrize('finish,content,code', [('length','{"passed":','output_limit'),
                                              ('stop','not JSON','invalid_json'),
                                              ('stop','{"unknown":1}','schema_validation')])
def test_invalid_paid_response_is_saved_and_not_automatically_repeated(settings, monkeypatch, finish, content, code):
    import litellm
    live_settings(settings)
    settings.seo_strategy_fallback_provider='openai'
    settings.seo_strategy_fallback_model='gpt-4.1-mini'
    calls=[]
    monkeypatch.setattr(litellm,'completion',lambda **kwargs: calls.append(kwargs['model']) or model_response(content,finish,2000))
    with Workflow(settings) as workflow:
        run=RunState(icp=icp_1_buy_and_hold)
        job=Job(run_id=run.run_id,kind='start',reviewer='Sam',idempotency_key=uuid4(),status='failed')
        workflow.storage.create_job(job,run)
        with running_job(job,workflow.storage):
            router=ModelRouter(settings,workflow.storage)
            for _ in range(2):
                with pytest.raises(ModelRequestError) as error:
                    router.generate(str(run.run_id),CheckResult,'Check',{},model_role='seo_strategy')
                assert error.value.code==code
        assert calls==['deepseek/deepseek-flash']
        records=workflow.storage.list_ai_calls(str(run.run_id))
        assert len(records)==1 and records[0].finish_reason==finish
        assert records[0].error_code==code and records[0].max_output_tokens==2000
        with workflow.storage.connect() as db:
            data=db.execute('SELECT data FROM operations').fetchone()[0]
        assert 'private reasoning' not in data and 'encrypted_content' in data
        authorized=workflow.storage.retry_job(str(job.id))
        monkeypatch.setattr(litellm,'completion',lambda **kwargs: calls.append(kwargs['model']) or model_response())
        with running_job(authorized,workflow.storage):
            assert router.generate(str(run.run_id),CheckResult,'Check',{},model_role='seo_strategy').passed
        assert len(calls)==2 and workflow.storage.tokens_used(str(run.run_id))==2060
        with workflow.storage.connect() as db:
            assert db.execute("SELECT count(*) FROM operations WHERE status='archived_response'").fetchone()[0]==2


def test_topic_map_invalid_ids_are_identified_without_paid_retry(settings, monkeypatch):
    import litellm
    live_settings(settings)
    body=json.loads((settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
    body['pillars'][1]['clusters'][1]['id']='ungueltig-häuslich'
    calls=[]
    monkeypatch.setattr(litellm,'completion',lambda **kwargs: calls.append(1) or
                        model_response(json.dumps(body,ensure_ascii=False)))
    with Workflow(settings) as workflow:
        job=Job(run_id=uuid4(),kind='start',reviewer='Sam',idempotency_key=uuid4())
        with running_job(job,workflow.storage):
            router=ModelRouter(settings,workflow.storage)
            for _ in range(2):
                with pytest.raises(ModelRequestError) as error:
                    router.generate(str(job.run_id),TopicMap,'Plan topics',{},model_role='seo_strategy')
                assert error.value.code=='schema_validation'
                assert error.value.validation_fields==['pillars.1.clusters.1.id']
        assert calls==[1]
        assert workflow.storage.list_ai_calls(str(job.run_id))[0].validation_fields==['pillars.1.clusters.1.id']


def test_schema_diagnostics_never_expose_unexpected_input_fields(settings):
    route=resolve_route(settings,'seo_strategy')
    with pytest.raises(ModelRequestError) as error:
        ModelRouter._validate(route,CheckResult,'{"passed":true,"private-secret-as-field":"private input"}','stop')
    assert error.value.validation_fields==['<field>']
    assert 'private' not in str(error.value) and 'private' not in repr(error.value.validation_fields)


def test_received_response_survives_result_commit_failure(settings,monkeypatch):
    import litellm
    live_settings(settings)
    calls=[]
    monkeypatch.setattr(litellm,'completion',lambda **kwargs: calls.append(1) or model_response())
    with Workflow(settings) as workflow:
        run=RunState(icp=icp_1_buy_and_hold)
        job=Job(run_id=run.run_id,kind='start',reviewer='Sam',idempotency_key=uuid4())
        original=workflow.storage.finish_operation
        monkeypatch.setattr(workflow.storage,'finish_operation',lambda *_: (_ for _ in ()).throw(RuntimeError('simulated commit failure')))
        with running_job(job,workflow.storage):
            router=ModelRouter(settings,workflow.storage)
            with pytest.raises(RuntimeError): router.generate(str(run.run_id),CheckResult,'Check',{},model_role='seo_strategy')
            monkeypatch.setattr(workflow.storage,'finish_operation',original)
            assert router.generate(str(run.run_id),CheckResult,'Check',{},model_role='seo_strategy').passed
        assert len(calls)==1 and len(workflow.storage.list_ai_calls(str(run.run_id)))==1
        assert workflow.storage.tokens_used(str(run.run_id))==40


def test_endpoint_cache_retains_first_paid_result(settings,monkeypatch):
    settings.dataforseo_login='fake-login'
    settings.dataforseo_password='fake-password'
    calls=[]
    failures=[True]
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def post(self,url,json):
            route=url.split('/')[-2];calls.append(route)
            if route=='related_keywords' and failures[0]:
                failures[0]=False
                raise httpx.ReadTimeout('response lost')
            return httpx.Response(200,json={'status_code':20000,'cost':0.01,'tasks':[{'id':'fake-task',
                'status_code':20000,'result':[{'items':[{'keyword':'Immobiliensteuerberater',
                'keyword_info':{'search_volume':120},'keyword_properties':{'keyword_difficulty':42.7}}]}]}]},
                request=httpx.Request('POST',url))
    monkeypatch.setattr('src.integrations.dataforseo.httpx.Client',Client)
    with Workflow(settings) as workflow:
        job=Job(run_id=uuid4(),kind='start',reviewer='Sam',idempotency_key=uuid4(),status='paused')
        workflow.storage.create_job(job)
        with running_job(job,workflow.storage):
            with pytest.raises(UncertainOperation): DataForSEO(settings).keywords('Immobiliensteuerberater')
        retry=workflow.storage.retry_job(str(job.id))
        with running_job(retry,workflow.storage):
            rows=DataForSEO(settings).keywords('Immobiliensteuerberater')
        assert calls==['keyword_overview','related_keywords','related_keywords']
        assert rows[0].volume==120 and rows[0].difficulty==42.7


def test_retry_claim_is_atomic_and_preserves_identity(settings):
    with Workflow(settings) as workflow:
        job=Job(run_id=uuid4(),kind='start',reviewer='Sam',reviewer_id='stable-user',idempotency_key=uuid4(),status='failed')
        workflow.storage.create_job(job)
        def retry(_):
            try: return workflow.storage.retry_job(str(job.id))
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(retry,range(2)))
        accepted=[r for r in results if r]
        assert len(accepted)==1 and accepted[0].reviewer_id=='stable-user'


def test_complete_live_mode_job_pipeline_with_fake_providers(settings,monkeypatch):
    import litellm
    live_settings(settings)
    settings.seo_decisions_enabled=True
    settings.image_generation_enabled=True
    settings.image_prompt_enabled=True
    settings.content_provider='openai'
    settings.content_model='gpt-5.4-mini'
    settings.max_image_attempts_per_run=2
    url=settings.wordpress_base_url+'/zielgruppen/immobilien/'
    excerpt='Immobiliensteuerberater begleiten Ihre Vermietung. Unterlagen werden nur kurz erwähnt.'
    source_calls={'gsc':0,'dataforseo':0,'wordpress':0}
    def gsc(self,seed):
        source_calls['gsc']+=1
        self.coverage={'gsc_complete':True,'limitations':[]}
        return [Keyword(keyword=seed,page=url,position=12,impressions=150,clicks=2,ctr=2/150,
                        source='Aggregierte GSC-Daten',reporting_start=date(2026,7,1),reporting_end=date(2026,9,28))]
    def paid(self,seed):
        source_calls['dataforseo']+=1
        return [Keyword(keyword=seed,volume=120,source='DataForSEO')]
    def pages(self):
        source_calls['wordpress']+=1
        self.coverage={'inventory_complete':True,'pages_retrieved':1,'limitations':[]}
        return [{'url':url,'title':'Immobiliensteuerberater','excerpt':excerpt,
                 'headings':[{'level':1,'text':'Immobiliensteuerberater'}],'content_truncated':False}]
    monkeypatch.setattr('src.integrations.google_search_console.GoogleSearchConsole.keywords',gsc)
    monkeypatch.setattr(DataForSEO,'keywords',paid)
    monkeypatch.setattr('src.integrations.wordpress.WordPressReadOnly.pages',pages)
    text_calls=[]
    def completion(**kwargs):
        schema=json.loads(kwargs['messages'][0]['content'].split('JSON Schema: ',1)[1])['title']
        text_calls.append(schema)
        if schema=='SEOInterpretation': value={'opportunities':['Prüfen Sie die vorhandene Seite.'],'gaps':[],'limitations':[]}
        elif schema=='TopicMap': value=json.loads((settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
        elif schema=='AssessmentBatch': value={'assessments':[{'topic_id':'immobiliensteuerberater','page_url':url,
            'relation':'dedicated','evidence_quote':excerpt,'issue':'Unterlagen werden nur kurz erwähnt.',
            'reasoning':'Der gelieferte Überblick kann diese Frage genauer beantworten.'}]}
        elif schema in {'SelectedBriefing','SelectedArticle'}:
            fixture_name='briefing' if schema=='SelectedBriefing' else 'article'
            value=json.loads((settings.input_dir/'fixtures'/(fixture_name+'.json')).read_text(encoding='utf-8'))
            value.update(primary_keyword='Immobiliensteuerberater',page_type='landing_page')
            if schema=='SelectedArticle' and text_calls.count('SelectedArticle')>1:
                value['sections'][0]['paragraphs'].append('Bereiten Sie Ihre offenen Fragen für unser Gespräch vor.')
        elif schema=='CheckResult': value={'passed':True,'errors':[],'warnings':[],'target':'content_agent'}
        elif schema=='SocialDrafts': value={'linkedin':'Wir erläutern Ihnen wichtige Fragen zur Vermietung. Vereinbaren Sie ein persönliches Gespräch.',
            'google_business_profile':'Wir begleiten Sie bei Ihren Fragen zur Vermietung. Vereinbaren Sie ein persönliches Gespräch.'}
        elif schema=='ImagePrompt': value={'prompt':'Eine sachliche blaue architektonische Form steht vollständig im mittleren Bereich einer quadratischen Komposition mit großzügigen freien Rändern.'}
        else: raise AssertionError('Unexpected model contract: '+schema)
        return model_response(json.dumps(value,ensure_ascii=False))
    monkeypatch.setattr(litellm,'completion',completion)
    monkeypatch.setattr(litellm,'token_counter',lambda **kwargs:100)
    image_calls=[]
    def master(self,prompt,size):
        image_calls.append(size)
        output=BytesIO();Image.new('RGB',size,'#dce8f5').save(output,format='PNG')
        return output.getvalue(),None
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate',master)
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        headers=authenticated_headers(client,'Sam')
        def queue(kind,**values):
            request={'kind':kind,'idempotency_key':str(uuid4()),**values}
            response=client.post('/jobs',headers=headers,json=request)
            assert response.status_code==202,response.text
            duplicate=client.post('/jobs',headers=headers,json=request)
            assert duplicate.json()['id']==response.json()['id']
            assert client.app.state.worker.process_one()
            job=client.get('/jobs/'+response.json()['id'],headers=headers).json()
            assert job['status']=='completed',job
            return job['run_id']
        run_id=queue('start',icp_id=icp_1_buy_and_hold.id)
        base='/runs/'+run_id
        run=client.get(base,headers=headers).json()
        assert run['status']=='awaiting_selection'
        selected=run['analysis']['recommendations'][0]
        assert selected['action']=='optimize'
        queue('selection',run_id=run_id,selection={'recommendation_id':selected['id'],
            'expected_revision':run['revision'],'mode':'draft','acknowledge_uncertainty':True,
            'notes':'Wir prüfen die belegte Ergänzung vor der Freigabe.'})
        run=client.get(base,headers=headers).json()
        assert run['status']=='seo_check_passed'
        queue('review',run_id=run_id,review={'action':'needs_revision','expected_revision':run['revision'],
            'feedback_notes':'Bitte ergänzen Sie die Vorbereitung des Gesprächs.'})
        run=client.get(base,headers=headers).json()
        assert run['status']=='seo_check_passed' and text_calls.count('SelectedArticle')==2
        queue('social',run_id=run_id)
        posts=client.get(base+'/social-posts',headers=headers).json()
        assert len(posts)==2
        queue('image_set',run_id=run_id)
        assets=client.get(base+'/image-sets',headers=headers).json()[0]['variants']
        assert len(assets)==3 and image_calls==[(2048,2048)]
        for item in assets:
            assert item['optimization_status']=='completed'
            assert item['final_format']==('jpeg' if item['platform']=='google_business_profile' else 'webp')
            assert client.post(base+'/images/'+item['id']+'/reviews',headers=headers,json={'action':'approved'}).status_code==200
        for post in posts:
            assert client.post(base+'/social-posts/'+post['id']+'/reviews',headers=headers,json={'action':'approved'}).status_code==200
        queue('review',run_id=run_id,review={'action':'approved','expected_revision':run['revision']})
        bundle=client.get(base+'/bundle',headers=headers)
        assert bundle.status_code==200
        with ZipFile(BytesIO(bundle.content)) as archive:
            assert all('images/'+item['seo_filename'] in archive.namelist() for item in assets)
        assert text_calls.count('SEOInterpretation')==text_calls.count('TopicMap')==1
        assert source_calls=={'gsc':1,'dataforseo':1,'wordpress':1}
