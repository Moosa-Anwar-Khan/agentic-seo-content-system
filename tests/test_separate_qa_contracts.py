"""Actual failed page response, assembled model messages and compatible reuse."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.orchestrator.workflow import Workflow
from src.services.qa_units import (QAUnitResult, plan, schema_for, prompt_for, validate_v1 as validate_unit,
    persist, cached_unit, retain_compatible_units, manifest_key, review)
from src.services.llm import ModelRouter, request_messages, text_operation_identity, ModelRequestError
from src.services.model_routes import resolve_route, text_request_options
from src.services.quality import QualityReviewError
from tests.test_qa_responsibilities import saved_state
from scripts.correct_revision_five import corrected

SAVED=json.loads((Path(__file__).parent/'fixtures/revision_five_page_review.json').read_text())


@pytest.fixture(autouse=True)
def combined(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True


def revision_five(w,settings):
    state=saved_state(w,settings);state.editorial=corrected(state.editorial);state.revision=5
    return state


def test_actual_page_response_is_rejected_without_changing_its_findings(settings):
    original=deepcopy(SAVED)
    with Workflow(settings) as w:
        state=revision_five(w,settings);page=plan(state,settings)[1][0]
        with pytest.raises(QualityReviewError) as e:validate_unit(state,page,QAUnitResult.model_validate(SAVED))
        assert e.value.reason=='review_scope_conflict'
        with pytest.raises(ValidationError):schema_for('page').model_validate(SAVED)
        assert SAVED==original and not SAVED['passed'] and state.seo_check is None
        # The original source quotation is exactly a saved H1, not body proof.
        u=next(u for u in plan(state,settings)[1] if u['id']=='module:L14')
        single={**SAVED,'assessments':[{'criterion':c,'complete':True} for c in u['request']['expected_criteria']],'unit_id':u['id'],'findings':[deepcopy(SAVED['findings'][0])]}
        with pytest.raises(QualityReviewError) as e:validate_unit(state,u,__import__('src.services.qa_units',fromlist=['schema_v1']).schema_v1('module').model_validate({k:v for k,v in single.items() if k!='assessments'}))
        assert e.value.reason=='heading_only_link_source_quote'


def test_real_page_messages_have_one_scope_and_no_link_target_bodies(settings):
    with Workflow(settings) as w:
        state=revision_five(w,settings);page=plan(state,settings)[1][0]
        messages=request_messages(settings,schema_for('page'),page['prompt'],page['request'])
        system=messages[0]['content'];request=json.loads(messages[1]['content'])
        assert 'consistency WITHIN THE CURRENT DRAFT' in system
        assert 'never a source/evidence ID as module_id' in system
        assert 'do not write, rewrite or approve' in system
        assert 'For page link review' not in system and 'For module units' not in system
        assert 'verified_link_targets' not in request
        assert len(request['article_modules'])==len(state.editorial['modules'])
        assert 'company_evidence' not in request['review_contract']
        assert request['approved_brand_facts']
        assert request['review_contract']['company_services']==state.icp.services
        assert request['meta_title']==state.editorial['meta_title']
        schema=schema_for('page').model_json_schema()
        assert schema['$defs']['PageMetadataSuggestion']['properties']['severity']['const']=='suggestion'
        assert 'conflicting_passages' in schema['$defs']['PageContradiction']['required']
        module=next(u for u in plan(state,settings)[1] if u['id']=='module:L14')
        assert len(module['request']['verified_link_targets'])==2
        assert 'conditional software-guide link' in module['prompt']
        assert all(p['ordered_text_parts'] for p in module['request']['verified_link_targets'])


@pytest.mark.parametrize('kind',['page','module','claim'])
def test_each_schema_keeps_output_bounds_completion_and_repetition_safeguards(settings,kind):
    schema=schema_for(kind);route=resolve_route(settings,'seo_qa')
    assert text_request_options(settings,route,schema.__name__)['max_tokens']==2400
    valid={'unit_id':'synthetic','coverage_complete':True,'passed':True,'findings':[],'assessments':[]} 
    assert ModelRouter._validate(route,schema,json.dumps(valid),'stop').passed
    for content,finish in [(json.dumps(valid),'length'),(json.dumps(valid)[:-1],'stop')]:
        with pytest.raises(ModelRequestError):ModelRouter._validate(route,schema,content,finish)
    raw=deepcopy(SAVED['findings'][0]);raw['rule']='contradiction'
    invalid={**valid,'passed':False,'findings':[raw,raw]}
    with pytest.raises(ModelRequestError) as e:ModelRouter._validate(route,schema,json.dumps(invalid),'stop')
    assert e.value.code=='quality_repetition'
    invalid['coverage_complete']=False;invalid['findings']=[]
    with pytest.raises(ValidationError):schema.model_validate(invalid)


def test_paid_identity_changes_with_prompt_schema_context_and_model_options(settings):
    route=resolve_route(settings,'seo_qa');schema=schema_for('page')
    payload={'_qa_contract':{'version':'test'},'unit_id':'page'}
    options=text_request_options(settings,route,schema.__name__)
    messages=request_messages(settings,schema,prompt_for('page'),payload)
    base=text_operation_identity('seo_qa',route,schema,payload,messages,options)
    different=request_messages(settings,schema,prompt_for('page')+' Extra test instruction.',payload)
    assert base!=text_operation_identity('seo_qa',route,schema,payload,different,options)
    assert base!=text_operation_identity('seo_qa',route,schema,payload,messages,{**options,'max_tokens':2000})
    assert base!=text_operation_identity('seo_qa',route,schema,{**payload,'unit_id':'other'},messages,options)
    assert base!=text_operation_identity('seo_qa',route,schema_for('module'),payload,messages,options)
    assert base!=text_operation_identity('seo_qa',replace(route,model='different-model'),schema,payload,messages,options)


def test_eleven_compatible_units_survive_restart_and_do_not_certify_page(settings,monkeypatch):
    with Workflow(settings) as w:
        state=revision_five(w,settings);legacy=plan(state,settings,legacy=True)[1]
        preserved={}
        for u in legacy:
            if u['id'] in {'page','module:L07','module:L10','module:L14'}:continue
            key='qa-unit:'+str(state.run_id)+':'+u['dependency']
            value={'result':QAUnitResult(unit_id=u['id'],coverage_complete=True,passed=True,findings=[]).model_dump(mode='json'),
                'dependency':u['dependency'],'reviewed_revision':4}
            persist(w.storage,key,state.run_id,value);preserved[key]=deepcopy(w.storage.get_operation(key))
        costs=w.storage.research_costs(str(state.run_id))
        manifest=retain_compatible_units(w.storage,state,settings)
        assert manifest['completed']==11 and not manifest['complete']
        assert all(r['compatibility']['source_operation'] in preserved for r in manifest['unit_records'])
        assert w.storage.research_costs(str(state.run_id))==costs
        assert all(w.storage.get_operation(k)==v for k,v in preserved.items())
        first=deepcopy(w.storage.get_operation(manifest_key(state,manifest['fingerprint'])))
        retain_compatible_units(w.storage,state,settings)
        assert w.storage.get_operation(manifest_key(state,manifest['fingerprint']))==first
    with Workflow(settings) as w:
        units=plan(state,settings)[1]
        assert sum(bool(cached_unit(w.storage,state,u)) for u in units)==11
        assert not cached_unit(w.storage,state,units[0])
        requests=[]
        def stop(run,schema,task,payload,**kwargs):
            requests.append(payload['unit_id']);raise RuntimeError('Synthetic provider stop')
        monkeypatch.setattr(w.context.llm,'generate',stop)
        with pytest.raises(RuntimeError,match='Synthetic provider stop'):review(w.context,state)
        assert requests==['page'] and state.seo_check is None
        # Changing a prompt invalidates current cache; compatibility cannot undo it.
        monkeypatch.setattr('src.services.qa_units.prompt_for',lambda kind:prompt_for(kind)+' Changed instructions.')
        assert not any(cached_unit(w.storage,state,u) for u in plan(state,settings)[1])


def test_status_projection_distinguishes_brand_pass_and_stopped_qa_without_mutating_state(settings):
    from uuid import uuid4
    from src.schemas.jobs import Job
    from src.services.failures import capture
    from src.services.progress import progress
    with Workflow(settings) as w:
        state=revision_five(w,settings);state.status='brand_check_passed'
        state.brand_check.passed=True;w.storage.save_run_state(state)
        job=Job(run_id=state.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4(),
            status='failed',source_revision=5,result_revision=5,attempt_id=str(uuid4()),error_code='quality_evidence')
        w.storage.create_job(job)
        event=capture(w.storage,code='quality_evidence',job=job,state=state,component='seo_qa',
            exc=QualityReviewError('review_scope_conflict'),operation='Review page')
        job.failure_summary=event['summary'];w.storage.save_job(job)
        before=w.storage.get_run_state(str(state.run_id)).model_dump(mode='json')
        costs=w.storage.research_costs(str(state.run_id))
        for _ in range(2):
            report=progress(w,state)
            assert report['check_status']['summary']=='Brand Check passed · SEO QA stopped · Draft saved, unapproved'
            assert report['current_failure']['reference']==event['id']
        assert w.storage.get_run_state(str(state.run_id)).model_dump(mode='json')==before
        assert w.storage.research_costs(str(state.run_id))==costs


@pytest.mark.parametrize('kind',['page','module','claim'])
def test_scoped_schemas_preserve_long_explanations(kind):
    item=deepcopy(SAVED['findings'][0]);item['rule']='contradiction'
    item['problem']='Ein vollständiger synthetischer Hinweis. '*50
    item['correction']='Bitte prüfen Sie die belegte Aussage. '*50
    raw={**SAVED,'findings':[item]}
    from src.services.qa_units import schema_v1
    parsed=schema_v1(kind).model_validate(raw)
    assert parsed.findings[0].problem==item['problem']
    assert parsed.findings[0].correction==item['correction']


@pytest.mark.parametrize('kind',['page','module','claim'])
def test_saved_encrypted_response_recovers_for_each_scoped_schema_without_provider(settings,monkeypatch,kind):
    from uuid import uuid4
    from src.schemas.ai import AICall
    from src.schemas.jobs import Job
    from src.services.qa_recovery import recover_saved_unit
    from src.services.qa_units import digest
    from src.services.recovery import encrypt_response
    from tests.test_pipeline_stabilization import prepared
    with Workflow(settings) as w:
        state=prepared(w,settings);state.seo_check=None;w.storage.save_run_state(state)
        fingerprint,units=plan(state,settings);unit=next(u for u in units if u['kind']==kind)
        job=Job(run_id=state.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4(),
            status='failed',source_revision=state.revision,attempt_id=str(uuid4()))
        w.storage.create_job(job);route=resolve_route(settings,'seo_qa');schema=schema_for(kind)
        call=AICall(run_id=str(state.run_id),task='seo_qa',job_id=str(job.id),attempt_id=job.attempt_id,
            provider=route.provider,model=route.model,status='failure',error_code='schema_validation',
            finish_reason='stop',latency_ms=1,cost=.0001)
        w.storage.log_ai_call(call)
        raw={'unit_id':unit['id'],'coverage_complete':True,'passed':True,'findings':[],'assessments':[{'criterion':c,'complete':True} for c in unit['request']['expected_criteria']]} 
        identity=text_operation_identity('seo_qa',route,schema,unit['request'],
            request_messages(settings,schema,unit['prompt'],unit['request']),text_request_options(settings,route,schema.__name__))
        key=str(job.id)+':text:'+digest(identity);w.storage.begin_operation(key,str(state.run_id))
        w.storage.save_operation_response(key,{'call':call.model_dump(mode='json'),'finish_reason':'stop',
            'encrypted_content':encrypt_response(settings,json.dumps(raw))})
        w.storage.fail_operation(key)
        persist(w.storage,manifest_key(state,fingerprint),state.run_id,{'fingerprint':fingerprint,'revision':state.revision,'complete':False})
        before=w.storage.get_operation(key);costs=w.storage.research_costs(str(state.run_id))
        monkeypatch.setattr(ModelRouter,'generate',lambda *a,**kw:pytest.fail('No provider call permitted'))
        recovered=recover_saved_unit(w.storage,settings,str(state.run_id),str(call.id))
        assert recovered['recovered'] and recovered['completed_units']==1
        assert recovered['semantic_review']=='incomplete' and recovered['paid_calls_added']==0
        assert w.storage.get_operation(key)==before and w.storage.research_costs(str(state.run_id))==costs
        assert w.storage.get_run_state(str(state.run_id)).seo_check is None


def test_legacy_schema_failure_remains_recoverable_with_current_module_validation(settings,monkeypatch):
    from uuid import uuid4
    from tests.test_qa_explanation_recovery import saved_failure
    from src.services.qa_recovery import recover_saved_unit
    from src.services.qa_units import digest
    from src.services.recovery import encrypt_response
    with Workflow(settings) as w:
        state,call,new_key,unit,payload=saved_failure(w,settings)
        old_fingerprint,old_units=plan(state,settings,legacy=True)
        old_unit=next(u for u in old_units if u['id']==unit['id'])
        call=call.model_copy(update={'id':uuid4()});w.storage.log_ai_call(call)
        route=resolve_route(settings,'seo_qa')
        key=str(call.job_id)+':text:'+digest(text_operation_identity('seo_qa',route,QAUnitResult,old_unit['request'],[],{}))
        w.storage.begin_operation(key,str(state.run_id))
        w.storage.save_operation_response(key,{'call':call.model_dump(mode='json'),'finish_reason':'stop',
            'encrypted_content':encrypt_response(settings,json.dumps(payload))})
        w.storage.fail_operation(key)
        persist(w.storage,manifest_key(state,old_fingerprint),state.run_id,{'fingerprint':old_fingerprint,'revision':state.revision,'complete':False})
        before=w.storage.get_operation(key)
        monkeypatch.setattr(ModelRouter,'generate',lambda *a,**kw:pytest.fail('No provider call permitted'))
        with pytest.raises((ValueError,ModelRequestError,QualityReviewError)):
            recover_saved_unit(w.storage,settings,str(state.run_id),str(call.id))
        assert w.storage.get_operation(key)==before and state.seo_check is None


def test_recovery_rejects_a_mismatched_manifest_fingerprint(settings):
    from tests.test_qa_explanation_recovery import saved_failure
    from src.services.qa_recovery import recover_saved_unit
    with Workflow(settings) as w:
        state,call,key,unit,payload=saved_failure(w,settings)
        fingerprint,_=plan(state,settings)
        persist(w.storage,manifest_key(state,fingerprint),state.run_id,
            {'fingerprint':'different','revision':state.revision,'complete':False})
        with pytest.raises(ValueError,match='No unique saved response'):
            recover_saved_unit(w.storage,settings,str(state.run_id),str(call.id))
        assert w.storage.get_operation('qa-unit:'+str(state.run_id)+':'+unit['dependency']) is None


def test_paid_identity_is_stable_when_database_json_key_order_changes(settings):
    schema=schema_for('page');route=resolve_route(settings,'seo_qa')
    a={'_qa_contract':{'version':'test'},'data':{'first':1,'second':2}}
    b={'data':{'second':2,'first':1},'_qa_contract':{'version':'test'}}
    options=text_request_options(settings,route,schema.__name__)
    one=text_operation_identity('seo_qa',route,schema,a,request_messages(settings,schema,prompt_for('page'),a),options)
    two=text_operation_identity('seo_qa',route,schema,b,request_messages(settings,schema,prompt_for('page'),b),options)
    assert one==two
