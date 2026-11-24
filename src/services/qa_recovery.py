"""Explicit local recovery of a saved QA response; never submits provider work."""
from datetime import datetime, timezone

from src.services.llm import ModelRouter, request_messages, text_operation_identity
from src.services.model_routes import resolve_route, text_request_options
from src.services.qa_units import (
    QAUnitResult, RESPONSE_PARSER, digest, manifest_key, persist, plan, validate_unit, schema_for, cached_unit,
)
from src.services.recovery import decrypt_response


def recover_saved_unit(storage, settings, run_id, call_id):
    """Revalidate exact current inputs without changing calls, failures or ledgers.

    The terminal caller needs private database/key access. No job is queued and no
    state/approval is advanced. The original paid operation remains immutable.
    """
    with storage.run_lock(run_id):
        if any(job.status in {'queued', 'running', 'paused'} for job in storage.list_jobs(run_id)):
            raise ValueError('Inspect active or uncertain work before local recovery')
        state=storage.get_run_state(run_id)
        if state is None:
            raise ValueError('Saved run not found')
        call=next((item for item in storage.list_ai_calls(run_id) if str(item.id)==call_id),None)
        if call is None or call.task!='seo_qa' or call.error_code!='schema_validation':
            raise ValueError('A recorded QA schema failure is required')
        job=storage.get_job(call.job_id)
        if not job or job.source_revision!=state.revision or job.attempt_id!=call.attempt_id:
            raise ValueError('The response does not belong to this revision and attempt')
        route=resolve_route(settings,'seo_qa')
        if (route.provider,route.model,route.fallback)!=(call.provider,call.model,call.fallback_used):
            raise ValueError('The configured review route differs from the recorded request')
        fingerprint,units=plan(state,settings)
        root=manifest_key(state,fingerprint)
        candidates=[]
        current_units={u['id']:u for u in units}
        for legacy in (False,True):
            old_fingerprint,source_units=plan(state,settings,legacy=True) if legacy else (fingerprint,units)
            source_manifest=(storage.get_operation(manifest_key(state,old_fingerprint)) or {}).get('value')
            if (not source_manifest or source_manifest.get('revision')!=state.revision or
                    source_manifest.get('fingerprint')!=old_fingerprint):continue
            for source_unit in source_units:
                unit=current_units[source_unit['id']]
                if legacy and (unit['kind']=='page' or
                        {k:v for k,v in unit['request'].items() if k!='_qa_contract'}!=source_unit['request']):continue
                schema=QAUnitResult if legacy else schema_for(unit['kind'])
                messages=[] if legacy else request_messages(settings,schema,unit['prompt'],unit['request'])
                identity=text_operation_identity('seo_qa',route,schema,source_unit['request'],messages,
                    text_request_options(settings,route,schema.__name__))
                key=str(job.id)+':text:'+digest(identity)
                entry=storage.get_operation(key)
                saved=(entry or {}).get('value') or {}
                if (entry and entry['status']=='failed' and saved.get('call',{}).get('id')==call_id
                        and saved.get('encrypted_content')):
                    candidates.append((unit,key,saved,schema,source_manifest.get('policy')))
        if len(candidates)!=1:
            raise ValueError('No unique saved response matches the exact current review inputs')
        unit,source_key,saved,schema,source_policy=candidates[0]
        result=ModelRouter._validate(route,schema,
            decrypt_response(settings,saved['encrypted_content']),saved.get('finish_reason'))
        validate_unit(state,unit,result)
        manifest=(storage.get_operation(root) or {}).get('value') or {
            'policy':source_policy,'fingerprint':fingerprint,'revision':state.revision,
            'total':len(units),'complete':False,'unit_ids':[u['id'] for u in units]}
        key='qa-unit:'+run_id+':'+unit['dependency']
        prior=storage.get_operation(key)
        recovered=False
        if prior and prior['status']=='completed':
            validate_unit(state,unit,schema_for(unit['kind']).model_validate(prior['value']['result']))
            if prior['value']['result']!=result.model_dump(mode='json'):
                raise ValueError('A different accepted result already exists; inspect it first')
        else:
            persist(storage,key,run_id,{
                'result':result.model_dump(mode='json'),'reviewed_revision':state.revision,
                'dependency':unit['dependency'],'recovery':{
                    'parser':RESPONSE_PARSER,'source_operation':source_key,'call_id':call_id,
                    'job_id':str(job.id),'attempt_id':job.attempt_id,
                    'recovered_at':datetime.now(timezone.utc).isoformat()}})
            recovered=True
        # Update only the manifest projection, never the draft, job or approvals.
        accepted=[];remaining=[]
        for item in units:
            cached=cached_unit(storage,state,item)
            if cached and cached['status']=='completed':
                validate_unit(state,item,schema_for(item['kind']).model_validate(cached['value']['result']))
                accepted.append({'unit_id':item['id'],'dependency':item['dependency'],
                    'reviewed_revision':cached['value']['reviewed_revision'],'reused':True})
            else:
                remaining.append(item['id'])
        manifest.update(completed=len(accepted),unit_records=accepted,
            current_unit=remaining[0] if remaining else None,
            updated_at=datetime.now(timezone.utc).isoformat())
        # Only the normal complete aggregation can certify the whole review.
        persist(storage,root,run_id,manifest)
        return {'run_id':run_id,'revision':state.revision,'unit_id':unit['id'],
            'recovered':recovered,'unit_passed':result.passed,'finding_count':len(result.findings),
            'completed_units':len(units)-len(remaining),'remaining_units':remaining,
            'semantic_review':'incomplete','paid_calls_added':0,'response_parser':RESPONSE_PARSER}
