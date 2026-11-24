"""Failure evidence and access tests: disposable stores, fake providers, no paid calls."""
import json
from uuid import uuid4
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.orchestrator.workflow import Workflow
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job, view
from src.schemas.ai import AICall
from src.schemas.content import CheckResult
from src.services.failures import boundary, capture
from src.services.jobs import JobWorker
from src.services.llm import ModelRequestError
from src.services.model_routes import resolve_route
from src.services.progress import progress
from src.services.recovery import current_job, UncertainOperation
from src.services.quality import QualityCheckFailed
from src.storage.sqlite_storage import SQLiteStorage, ResearchBudgetExceeded
from tests.conftest import authenticated_headers, own_fixture
from tests.test_private_accounts import account, use

SECRET='sk-secret-private-message@example.org'


def queued(workflow):
    state=workflow.start(icp_1_buy_and_hold)
    job=Job(run_id=state.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4())
    workflow.storage.create_job(job)
    return state,job


@pytest.mark.parametrize('component,operation',[
 ('seo_strategy_agent','Analyze SEO evidence'),('topic_map_agent','Prepare topic plan'),
 ('briefing_agent','Prepare briefing'),('content_agent','Write article'),
 ('brand_agent','Check deterministic brand rules'),('seo_qa_agent','Review claim'),
 ('DataForSEO','Retrieve keywords'),('Google Search Console','Read search evidence'),
 ('Website Evidence','Read WordPress'),('Official Source Research','Retrieve original text'),
 ('social_content','Prepare social drafts'),('Image Generation','Generate master'),
 ('Image Processing','Process channel variant'),('Local Export','Write export'),
])
def test_every_boundary_records_confirmed_component_and_sanitized_unknown_cause(settings,monkeypatch,component,operation):
    with Workflow(settings) as workflow:
        state,job=queued(workflow);before=state.html
        worker=JobWorker(settings,workflow)
        def fail(_):
            with boundary(component,operation):raise RuntimeError(SECRET)
        monkeypatch.setattr(worker,'_execute',fail)
        worker.process_one()
        saved=workflow.storage.get_job(str(job.id));events=workflow.storage.list_failures(str(job.id))
        assert len(events)==1 and saved.failure_summary['reference']==events[0]['id']
        summary=events[0]['summary']
        assert summary['component']!='Workflow' and summary['operation']==operation
        assert summary['reason']=='Unexpected software error — cause not yet confirmed.'
        assert summary['category']=='local_software_failure'
        assert summary['attempt_id']==saved.attempt_id and summary['revision']==state.revision
        assert SECRET not in json.dumps(events)
        assert workflow.storage.get_run_state(str(state.run_id)).html==before
        assert progress(workflow,workflow.get(str(state.run_id)))['current_failure']==summary
        assert not worker.process_one()


def test_output_limit_correlates_exact_call_and_excludes_unrelated_and_other_attempts(settings,monkeypatch):
    with Workflow(settings) as workflow:
        state,job=queued(workflow);worker=JobWorker(settings,workflow)
        def fail(active_job):
            call=AICall(run_id=state.run_id,job_id=str(active_job.id),attempt_id=active_job.attempt_id,
                task='seo_qa',provider='deepseek',model='deepseek-v4-flash',latency_ms=1,status='failure',
                finish_reason='length',max_output_tokens=6000,output_tokens=6000,provider_request_id='req-test-123')
            workflow.storage.log_ai_call(call)
            unrelated=call.model_copy(update={'id':uuid4(),'attempt_id':'unrelated','max_output_tokens':999})
            workflow.storage.log_ai_call(unrelated)
            current_job.get().details(component='seo_qa',operation='Review article unit',unit_id='claim:C04',call_id=str(call.id))
            raise ModelRequestError(resolve_route(settings,'seo_qa'),'output_limit')
        monkeypatch.setattr(worker,'_execute',fail);worker.process_one()
        event=workflow.storage.list_failures(str(job.id))[0]
        assert '6000 tokens' in event['summary']['reason']
        assert event['summary']['unit_id']=='claim:C04'
        assert event['technical']['provider_request_id']=='req-test-123'
        assert event['technical']['max_output_tokens']==6000
        assert 'technical' not in view(workflow.storage.get_job(str(job.id))).model_dump()
        assert 'provider_request_id' not in json.dumps(view(workflow.storage.get_job(str(job.id))).model_dump(mode='json'))


def test_explicit_retry_creates_separate_attempt_persists_restart_and_keeps_immutable_event(settings,monkeypatch):
    with Workflow(settings) as workflow:
        _,job=queued(workflow);worker=JobWorker(settings,workflow)
        monkeypatch.setattr(worker,'_execute',lambda job:(_ for _ in ()).throw(UncertainOperation(SECRET)))
        worker.process_one();first=workflow.storage.list_failures(str(job.id))[0]
        assert workflow.storage.get_job(str(job.id)).status=='paused'
        retried=workflow.storage.retry_job(str(job.id))
        assert retried.attempt_id!=first['attempt_id'] and retried.failure_summary is None
        worker.process_one();events=workflow.storage.list_failures(str(job.id))
        assert len(events)==2 and events[0]==first
        workflow.storage.save_failure({**first,'technical':{'changed':True}})
        assert workflow.storage.list_failures(str(job.id))[0]==first
        path=workflow.storage.path
    assert SQLiteStorage(path).list_failures(str(job.id))==events
    assert SECRET not in json.dumps(events)


def test_quality_result_is_content_defect_and_historical_failure_not_current(settings,monkeypatch):
    with Workflow(settings) as workflow:
        state,job=queued(workflow);worker=JobWorker(settings,workflow)
        def fail(_):
            state.brand_check=CheckResult(passed=False,errors=['Informelle Ansprache gefunden.'])
            workflow.storage.save_run_state(state)
            raise QualityCheckFailed(SECRET)
        monkeypatch.setattr(worker,'_execute',fail);worker.process_one()
        summary=workflow.storage.get_job(str(job.id)).failure_summary
        assert summary['component']=='Brand Check' and summary['category']=='content_corrections'
        assert 'informal address' in summary['reason']
        state.revision+=1;state.updated_at=datetime.now(timezone.utc)
        assert progress(workflow,state)['current_failure'] is None


def test_schema_fields_budget_and_diagnostic_storage_failure(settings,monkeypatch,caplog):
    with Workflow(settings) as workflow:
        state,job=queued(workflow)
        error=ModelRequestError(resolve_route(settings,'content'),'schema_validation',['modules.0.markdown',SECRET])
        event=capture(workflow.storage,code='schema_validation',exc=error,job=job,state=state)
        assert 'modules.0.markdown' in event['summary']['reason'] and SECRET not in json.dumps(event)
        error=ResearchBudgetExceeded(SECRET,required=100000,available=30000,scope='monthly')
        event=capture(workflow.storage,code='research_budget',exc=error,job=job,state=state)
        assert '$0.1000' in event['summary']['reason'] and '$0.0300' in event['summary']['reason']
        assert 'monthly' in event['summary']['reason']
        monkeypatch.setattr(workflow.storage,'save_failure',lambda _:(_ for _ in ()).throw(RuntimeError(SECRET)))
        event=capture(workflow.storage,code='processing',exc=RuntimeError(SECRET),job=job,state=state)
        assert event['summary']['reference'] in caplog.text and SECRET not in caplog.text


def test_admin_only_api_and_safe_immediate_upload_failure(settings,monkeypatch):
    with TestClient(create_app(settings),raise_server_exceptions=False) as client:
        client.app.state.worker.close();workflow=client.app.state.workflow;auth=client.app.state.auth
        owner=account(auth,'Alice');headers=use(client,owner)
        state=workflow.start(icp_1_buy_and_hold,owner_user_id=owner['id'])
        job=Job(run_id=state.run_id,kind='quality_checks',reviewer=owner['name'],reviewer_id=owner['id'],idempotency_key=uuid4())
        workflow.storage.create_job(job)
        event=capture(workflow.storage,code='processing',job=job,state=state,exc=RuntimeError(SECRET))
        job.error='Retained compatible error';job.error_code='processing';job.status='failed';workflow.storage.save_job(job)
        assert client.get('/jobs/'+str(job.id)+'/diagnostics').status_code==403
        public=client.get('/jobs/'+str(job.id)).json()
        assert public['error_info']['failure']['reference']==event['id']
        assert 'technical' not in json.dumps(public)
        admin=account(auth,'Morgan','admin');use(client,admin)
        response=client.get('/jobs/'+str(job.id)+'/diagnostics')
        assert response.status_code==200 and response.json()['events'][0]['id']==event['id']
        assert client.get('/runs/'+str(state.run_id)+'/diagnostics').status_code==200
        headers=use(client,owner)
        response=client.post('/runs/'+str(state.run_id)+'/graphic',headers=headers,json={'content_base64':'not-base64','expected_revision':state.revision})
        assert response.status_code==409
        info=response.json()['user_error'];assert info['failure']['component']=='Infographic Upload'
        assert 'diagnostic_report' not in info and SECRET not in response.text
        immediate_reference=info['reference']
        use(client,admin)
        history=client.get('/runs/'+str(state.run_id)+'/diagnostics').json()['events']
        assert any(e['id']==immediate_reference and e['job_id'] is None for e in history)
        use(client,owner)
        assert client.get('/runs/'+str(state.run_id)+'/diagnostics').status_code==403
        stranger=account(auth,'Bob');use(client,stranger)
        assert client.get('/jobs/'+str(job.id)).status_code==404
        assert client.get('/jobs/'+str(job.id)+'/diagnostics').status_code==404
        client.cookies.clear();assert client.get('/jobs/'+str(job.id)+'/diagnostics').status_code==401


def test_real_model_router_correlates_calls_and_retries_only_when_explicit(settings,monkeypatch):
    import litellm
    from src.services.llm import ModelRouter
    settings.llm_api_key='fake-offline-key'
    with Workflow(settings) as workflow:
        state,job=queued(workflow);settings.demo_mode=False
        requests=[]
        monkeypatch.setattr(litellm,'token_counter',lambda **kw:20)
        def completion(**kwargs):
            requests.append(kwargs['model'])
            return SimpleNamespace(id='synthetic-model-request',usage=SimpleNamespace(prompt_tokens=20,completion_tokens=50,total_tokens=70),
                choices=[SimpleNamespace(finish_reason='length',message=SimpleNamespace(content=SECRET))],_hidden_params={})
        monkeypatch.setattr(litellm,'completion',completion)
        worker=JobWorker(settings,workflow)
        monkeypatch.setattr(worker,'_execute',lambda _:ModelRouter(settings,workflow.storage).generate(str(state.run_id),CheckResult,'Check',{'input':'safe'},model_role='seo_qa'))
        worker.process_one();event=workflow.storage.list_failures(str(job.id))[0]
        call=workflow.storage.list_ai_calls(str(state.run_id))[0]
        assert event['technical']['call_id']==str(call.id)
        assert call.job_id==str(job.id) and call.attempt_id==event['attempt_id']
        assert event['technical']['finish_reason']=='length' and event['technical']['output_tokens']==50
        assert SECRET not in json.dumps(event)
        assert not worker.process_one() and len(requests)==1
        workflow.storage.retry_job(str(job.id));worker.process_one()
        assert len(requests)==2  # Only the explicitly authorized retry can buy a second request.
        events=workflow.storage.list_failures(str(job.id))
        assert len(events)==2 and events[1]['attempt_id']!=events[0]['attempt_id']
        assert events[1]['technical']['call_id']!=events[0]['technical']['call_id']


@pytest.mark.parametrize('code,expected',[
 ('input_privacy','missing_prerequisites'),('preflight_blocked','missing_prerequisites'),
 ('invalid_json','provider_failure'),('schema_validation','provider_failure'),
 ('quality_repetition','provider_failure'),('authentication','provider_failure'),
 ('uncertain_outcome','uncertain_request'),('quality_evidence','invalid_review'),
 ('image_processing','local_software_failure'),
])
def test_failure_types_have_distinct_honest_categories(settings,code,expected):
    with Workflow(settings) as workflow:
        state,job=queued(workflow)
        event=capture(workflow.storage,code=code,job=job,state=state,exc=RuntimeError(SECRET))
        assert event['summary']['category']==expected
        assert SECRET not in json.dumps(event)


def test_provider_status_and_local_file_error_are_evidence_not_raw_messages(settings):
    with Workflow(settings) as workflow:
        state,job=queued(workflow)
        import httpx
        error=httpx.HTTPStatusError(SECRET,request=httpx.Request('GET','https://example.invalid/private'),response=httpx.Response(403))
        with pytest.raises(httpx.HTTPStatusError) as caught:
            with boundary('Google Search Console','Read performance'):raise error
        event=capture(workflow.storage,code='authentication',exc=caught.value,job=job,state=state)
        assert 'Google Search Console rejected the request with HTTP 403' in event['summary']['reason']
        assert SECRET not in json.dumps(event) and 'https://' not in json.dumps(event)
        event=capture(workflow.storage,code='processing',exc=PermissionError(13,SECRET),job=job,state=state,component='Local Export')
        assert 'operating-system error 13' in event['summary']['reason']


def test_direct_graph_failure_is_persisted_without_a_job(settings,monkeypatch):
    with Workflow(settings) as workflow:
        def fail(state):raise RuntimeError(SECRET)
        monkeypatch.setattr(workflow.agents['seo_strategy_agent'],'execute',fail)
        state=workflow.start(icp_1_buy_and_hold)
        assert state.status=='failed'
        events=workflow.storage.list_run_failures(str(state.run_id))
        assert len(events)==1 and events[0]['summary']['component']=='SEO Strategy'
        assert events[0]['job_id'] is None and SECRET not in json.dumps(events)
        assert progress(workflow,workflow.get(str(state.run_id)))['current_failure']['reference']==events[0]['id']


def test_same_node_recovery_does_not_reuse_previous_request_identity(settings):
    from src.services.recovery import running_job
    with Workflow(settings) as workflow:
        state,job=queued(workflow)
        with running_job(job,workflow.storage):
            context=current_job.get()
            context.step('seo_qa_agent')
            context.details(call_id='old-call',unit_id='claim:old')
            context.step('seo_qa_agent')
            assert 'call_id' not in job.execution_context and 'unit_id' not in job.execution_context
