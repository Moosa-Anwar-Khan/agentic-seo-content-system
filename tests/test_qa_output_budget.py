"""Full-draft QA has room for findings without accepting partial paid output."""
import json
from uuid import uuid4

import pytest

from src.orchestrator.state import RunState
from src.orchestrator.workflow import Workflow
from src.schemas.content import CheckResult
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.services.llm import ModelRouter, ModelRequestError
from src.services.model_routes import resolve_route, text_request_options
from src.services.recovery import running_job
from tests.test_paid_recovery import live_settings, model_response


@pytest.mark.parametrize('global_limit,configured,expected', [
    (12000, {}, 6000),
    (4000, {}, 4000),
    (12000, {'max_output_tokens': 8000}, 8000),
    (4000, {'max_output_tokens': 3000}, 3000),
])
def test_full_draft_qa_limit_respects_task_override_and_global_cap(settings, global_limit, configured, expected):
    live_settings(settings)
    settings.seo_qa_provider='deepseek';settings.seo_qa_model='deepseek-flash'
    settings.max_output_tokens=global_limit
    settings.ai_task_options=json.dumps({'seo_qa':configured})
    options=text_request_options(settings,resolve_route(settings,'seo_qa'),'CheckResult')
    assert options['max_tokens']==expected
    assert options['reasoning_effort']=='none'
    # Brief/schema defaults for other tasks are not inflated by this repair.
    assert text_request_options(settings,resolve_route(settings,'seo_strategy'),'CheckResult')['max_tokens']==2000


def test_long_qa_result_and_explicit_retry_keep_saved_draft_and_paid_guards(settings, monkeypatch):
    import litellm
    live_settings(settings)
    settings.seo_qa_provider='deepseek';settings.seo_qa_model='deepseek-flash'
    body=json.dumps({'passed':False,'errors':[
        f'L04, Aussage {i}: Prüfen Sie die Bedingungen der amtlichen Quelle und die genaue Zuordnung zum Entwurf.'
        for i in range(30)],'warnings':[]},ensure_ascii=False)
    calls=[]
    finish='length'
    def completion(**kwargs):
        calls.append(kwargs)
        return model_response(body,finish,3500)
    monkeypatch.setattr(litellm,'completion',completion)
    with Workflow(settings) as workflow:
        run=RunState(icp=icp_1_buy_and_hold)
        job=Job(run_id=run.run_id,kind='quality_checks',reviewer='Sam',
                idempotency_key=uuid4(),status='failed')
        workflow.storage.create_job(job,run)
        before=workflow.storage.get_run_state(str(run.run_id))
        router=ModelRouter(settings,workflow.storage)
        with running_job(job,workflow.storage):
            for _ in range(2):
                # Even syntactically valid JSON with finish_reason=length is rejected.
                with pytest.raises(ModelRequestError) as error:
                    router.generate(str(run.run_id),CheckResult,'Audit saved draft',{},model_role='seo_qa')
                assert error.value.code=='output_limit'
        assert len(calls)==1 and calls[0]['max_tokens']==6000
        assert calls[0]['num_retries']==0
        assert workflow.storage.get_run_state(str(run.run_id))==before
        assert workflow.storage.tokens_used(str(run.run_id))==3520
        # Only an explicit durable job retry permits another provider submission.
        retried=workflow.storage.retry_job(str(job.id));finish='stop'
        with running_job(retried,workflow.storage):
            result=router.generate(str(run.run_id),CheckResult,'Audit saved draft',{},model_role='seo_qa')
        assert not result.passed and len(result.errors)==30
        assert len(calls)==2 and calls[1]['max_tokens']==6000
        records=workflow.storage.list_ai_calls(str(run.run_id))
        assert len(records)==2 and records[-1].status=='success'
        assert records[0].error_code=='output_limit'
        assert workflow.storage.get_run_state(str(run.run_id))==before
