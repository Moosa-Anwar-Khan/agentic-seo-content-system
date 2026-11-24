"""Explanation length is presentation guidance, never a reason to discard QA."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.orchestrator.workflow import Workflow
from src.schemas.ai import AICall
from src.schemas.content import CheckResult, ReviewFinding
from src.schemas.jobs import Job
from src.services.llm import ModelRequestError, ModelRouter, request_messages, text_operation_identity
from src.services.model_routes import resolve_route, text_request_options
from src.services.qa_recovery import recover_saved_unit
from src.services.qa_units import QAUnitResult, digest, manifest_key, persist, plan, review, schema_for
from src.services.quality import QualityReviewError
from src.services.recovery import encrypt_response
from tests.test_pipeline_stabilization import prepared


def finding(module_id, length=190):
    return {'module_id':module_id,'quote':'Sie prüfen den genauen Sachverhalt.',
        'message_en':'Review this specific passage.','action_en':'Correct the saved passage.',
        'problem':('Prüfen Sie diesen sachlichen Hinweis. '*100)[:length],
        'correction':'Bitte korrigieren Sie die belegte Aussage.',
        'severity':'blocking','category':'content_defect','rule':'editorial','evidence_refs':[]}


@pytest.mark.parametrize('field',['problem','correction','message_en','action_en'])
@pytest.mark.parametrize('length',[180,181,190,361,2400])
def test_explanation_text_is_preserved_without_character_caps(field,length):
    item=finding('L01');item[field]='a'*length
    parsed=QAUnitResult(unit_id='module:L01',coverage_complete=True,passed=False,findings=[item])
    assert parsed.findings[0].model_dump()[field]==item[field]
    shared=ReviewFinding.model_validate({k:v for k,v in parsed.findings[0].model_dump().items()
                                       if k not in {'problem','correction'}})
    result=CheckResult(passed=False,findings=[shared])
    assert CheckResult.model_validate_json(result.model_dump_json())==result
    assert 'maxLength' not in QAUnitResult.model_json_schema()['$defs']['QAFinding']['properties'][field]


def test_long_warning_is_preserved_but_response_shape_and_completion_remain_strict(settings):
    route=resolve_route(settings,'seo_qa')
    value={'unit_id':'page','coverage_complete':True,'passed':True,'findings':[], 'warnings':['Hinweis. '*100]}
    assert ModelRouter._validate(route,QAUnitResult,json.dumps(value),'stop').warnings==value['warnings']
    for content,finish in [(json.dumps(value),'length'),(json.dumps(value)[:-1],'stop')]:
        with pytest.raises(ModelRequestError):ModelRouter._validate(route,QAUnitResult,content,finish)
    value.update(passed=False,findings=[finding('L01')]*2)
    with pytest.raises(ModelRequestError,match='quality_repetition'):
        ModelRouter._validate(route,QAUnitResult,json.dumps(value),'stop')
    value['findings']=[finding('L01')];value['coverage_complete']=False
    with pytest.raises(ValidationError):QAUnitResult.model_validate(value)


def saved_failure(workflow,settings,*,invalid=None):
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    state=prepared(workflow,settings)
    state.seo_check=None
    state.status='brand_check_passed'
    workflow.storage.save_run_state(state)
    fingerprint,units=plan(state,settings);unit=units[1]
    job=Job(run_id=state.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4(),
            status='failed',source_revision=state.revision,attempt_id=str(uuid4()))
    workflow.storage.create_job(job)
    route=resolve_route(settings,'seo_qa')
    call=AICall(run_id=str(state.run_id),task='seo_qa',job_id=str(job.id),attempt_id=job.attempt_id,
        provider=route.provider,model=route.model,status='failure',error_code='schema_validation',
        finish_reason='stop',validation_fields=['findings.0.problem'],cost=.001,latency_ms=10)
    workflow.storage.log_ai_call(call)
    payload={'unit_id':unit['id'],'coverage_complete':True,'passed':False,
             'findings':[finding(unit['module_ids'][0])],'warnings':[]}
    payload['assessments']=[{'criterion':c,'complete':True} for c in unit['request']['expected_criteria']]
    payload['findings'][0].update(rule='required_module',requirement_id='required_module',material_impact='The synthetic required reader outcome is absent.')
    if invalid=='evidence':payload['findings'][0]['evidence_refs']=['invented-source']
    if invalid=='quote':payload['findings'][0]['quote']='Nicht im aktuellen Entwurf enthalten.'
    if invalid=='duplicate':payload['findings']*=2
    schema=schema_for(unit['kind'])
    identity=text_operation_identity('seo_qa',route,schema,unit['request'],
        request_messages(settings,schema,unit['prompt'],unit['request']),text_request_options(settings,route,schema.__name__))
    key=str(job.id)+':text:'+digest(identity)
    workflow.storage.begin_operation(key,str(state.run_id))
    workflow.storage.save_operation_response(key,{'call':call.model_dump(mode='json'),
        'finish_reason':'length' if invalid=='truncated' else 'stop',
        'encrypted_content':encrypt_response(settings,json.dumps(payload))})
    workflow.storage.fail_operation(key)
    # A completed page unit must survive recovery and be reused on resume.
    persist(workflow.storage,'qa-unit:'+str(state.run_id)+':'+units[0]['dependency'],state.run_id,
        {'result':schema_for('page')(unit_id='page',coverage_complete=True,passed=True,findings=[],assessments=[{'criterion':c,'complete':True} for c in units[0]['request']['expected_criteria']]).model_dump(mode='json'),
         'dependency':units[0]['dependency'],'reviewed_revision':state.revision})
    persist(workflow.storage,manifest_key(state,fingerprint),state.run_id,
        {'fingerprint':fingerprint,'revision':state.revision,'complete':False,'completed':1})
    return state,call,key,unit,payload


def test_local_recovery_preserves_originals_and_survives_restart_without_provider_calls(settings,monkeypatch):
    with Workflow(settings) as workflow:
        state,call,key,unit,payload=saved_failure(workflow,settings)
        originals={name:deepcopy(value) for name,value in {
            'state':workflow.storage.get_run_state(str(state.run_id)).model_dump(),
            'operation':workflow.storage.get_operation(key),
            'jobs':workflow.storage.list_jobs(str(state.run_id)),
            'calls':workflow.storage.list_ai_calls(str(state.run_id)),
            'costs':workflow.storage.research_costs(str(state.run_id)),
            'tokens':workflow.storage.tokens_used(str(state.run_id))}.items()}
        monkeypatch.setattr(ModelRouter,'generate',lambda *a,**kw:pytest.fail('Recovery must never call a provider'))
        result=recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        assert result['recovered'] and result['completed_units']==2 and not result['unit_passed']
        assert result['semantic_review']=='incomplete' and result['paid_calls_added']==0
        assert workflow.storage.get_operation(key)==originals['operation']
        assert workflow.storage.get_run_state(str(state.run_id)).model_dump()==originals['state']
        assert workflow.storage.list_jobs(str(state.run_id))==originals['jobs']
        assert workflow.storage.list_ai_calls(str(state.run_id))==originals['calls']
        assert workflow.storage.research_costs(str(state.run_id))==originals['costs']
        assert workflow.storage.tokens_used(str(state.run_id))==originals['tokens']
        entry=workflow.storage.get_operation('qa-unit:'+str(state.run_id)+':'+unit['dependency'])
        assert entry['value']['result']==schema_for(unit['kind']).model_validate(payload).model_dump(mode='json')
        assert entry['value']['recovery']['call_id']==str(call.id)
    with Workflow(settings) as restarted:
        result=recover_saved_unit(restarted.storage,settings,str(state.run_id),str(call.id))
        assert not result['recovered'] and result['completed_units']==2
        requested=[]
        def stop_at_unfinished(run,schema,task,payload,**kwargs):
            requested.append(payload['unit_id'])
            raise RuntimeError('Synthetic stop before any paid call')
        monkeypatch.setattr(restarted.context.llm,'generate',stop_at_unfinished)
        with pytest.raises(RuntimeError,match='Synthetic stop'):review(restarted.context,state)
        assert len(requested)==1 and requested[0] not in {'page',unit['id']}


def test_recovered_blocking_finding_still_blocks_the_complete_review(settings,monkeypatch):
    with Workflow(settings) as workflow:
        state,call,key,unit,payload=saved_failure(workflow,settings)
        recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        requests=[]
        def passing_fake(run,schema,task,payload,**kwargs):
            requests.append(payload['unit_id'])
            return schema(unit_id=payload['unit_id'],coverage_complete=True,passed=True,findings=[],assessments=[{'criterion':c,'complete':True} for c in payload['expected_criteria']])
        monkeypatch.setattr(workflow.context.llm,'generate',passing_fake)
        result=review(workflow.context,state)
        assert not result.passed and len(result.findings)==1
        assert payload['findings'][0]['problem'] in result.errors[0]
        assert state.qa_manifest['complete'] and state.professional_review is None
        assert unit['id'] not in requests and 'page' not in requests


@pytest.mark.parametrize('invalid',['evidence','quote','duplicate','truncated','changed_draft','changed_revision','changed_settings'])
def test_recovery_cannot_bypass_grounding_or_reuse_changed_inputs(settings,invalid):
    with Workflow(settings) as workflow:
        state,call,key,unit,_=saved_failure(workflow,settings,invalid=invalid)
        if invalid=='changed_settings':
            settings.ai_task_options='{"seo_qa":{"max_output_tokens":2000}}'
        elif invalid.startswith('changed'):
            if invalid=='changed_draft':state.editorial['modules'][0]['markdown']+=' Geänderter Text.'
            else:state.revision+=1
            workflow.storage.save_run_state(state)
        with pytest.raises((ValueError,QualityReviewError,ModelRequestError)):
            recover_saved_unit(workflow.storage,settings,str(state.run_id),str(call.id))
        assert workflow.storage.get_operation('qa-unit:'+str(state.run_id)+':'+unit['dependency']) is None
