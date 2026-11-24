"""Recovered errors become history; incomplete content checks remain visible."""
from copy import deepcopy
from datetime import datetime, timezone

from src.orchestrator.workflow import Workflow
from src.services.progress import progress
from src.services.qa_recovery import recover_saved_unit
from tests.test_qa_explanation_recovery import saved_failure


def attach_failure(workflow,state,call):
    job=workflow.storage.get_job(call.job_id)
    job.error_code='schema_validation';job.error='Saved response failed schema validation'
    job.execution_context={'call_id':str(call.id)}
    job.failure_summary={'reference':'original-failure','component':'SEO QA','code':'schema_validation',
        'revision':state.revision,'reason':'The explanation exceeded the old character limit.',
        'next_action':'Inspect saved results.','retry':'may_charge','category':'provider_failure'}
    job.updated_at=datetime.now(timezone.utc)
    workflow.storage.save_job(job)
    return job


def test_recovery_removes_only_the_resolved_banner_and_displays_partial_findings(settings):
    with Workflow(settings) as workflow:
        state,call,key,unit,payload=saved_failure(workflow,settings)
        job=attach_failure(workflow,state,call)
        before=progress(workflow,state)
        assert before['current_failure']['reference']=='original-failure'
        recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        stored=deepcopy(workflow.storage.get_job(str(job.id)))
        result=progress(workflow,state)
        assert result['current_failure'] is None
        assert result['local_recovery']['call_id']==str(call.id)
        assert result['state']=='Draft saved — QA incomplete'
        assert 'not a new failure' in result['next_action'] and 'has not passed' in result['next_action']
        assert result['latest_attempt']['historical'] and result['latest_attempt']['recovered']
        findings=[f for f in result['findings'] if f.get('provisional')]
        assert len(findings)==1 and findings[0]['blocking']
        assert findings[0]['message']==payload['findings'][0]['message_en']
        assert payload['findings'][0]['problem'] in findings[0]['original_finding']
        assert not any(f['code']=='schema_validation' for f in result['findings'])
        assert not result['qa']['complete'] and result['qa']['completed']==2
        assert workflow.storage.get_job(str(job.id))==stored
        assert workflow.storage.get_operation(key)['status']=='failed'
        assert state.seo_check is None and state.professional_review is None


def test_an_unrelated_new_failure_is_not_hidden_by_an_older_recovery(settings):
    with Workflow(settings) as workflow:
        state,call,key,unit,payload=saved_failure(workflow,settings)
        job=attach_failure(workflow,state,call)
        recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        job.execution_context['call_id']='another-request'
        workflow.storage.save_job(job)
        result=progress(workflow,state)
        assert result['current_failure']['reference']=='original-failure'
        assert result['local_recovery'] is None
        assert not result['latest_attempt']['recovered']


def test_changed_content_does_not_reuse_an_old_recovery_or_finding(settings):
    with Workflow(settings) as workflow:
        state,call,key,unit,payload=saved_failure(workflow,settings)
        attach_failure(workflow,state,call)
        recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        state.editorial['modules'][0]['markdown']+=' Geänderter Inhalt.'
        result=progress(workflow,state)
        assert result['local_recovery'] is None
        assert result['current_failure']['reference']=='original-failure'
        assert not [f for f in result['findings'] if f.get('provisional')]


def test_reading_progress_never_submits_or_changes_saved_work(settings,monkeypatch):
    with Workflow(settings) as workflow:
        state,call,key,unit,payload=saved_failure(workflow,settings)
        attach_failure(workflow,state,call)
        recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        monkeypatch.setattr(workflow.context.llm,'generate',lambda *a,**kw:__import__('pytest').fail('No paid request'))
        before=deepcopy(workflow.storage.list_operations(str(state.run_id)))
        for _ in range(2):progress(workflow,state)
        assert workflow.storage.list_operations(str(state.run_id))==before
