"""Read-only current execution and ledger information, separate from history."""
from datetime import datetime, timezone
from pydantic import ValidationError
from src.services.failures import category


def progress(workflow, state, actor=None):
    store=workflow.storage;run_id=str(state.run_id)
    all_jobs=store.list_jobs(run_id)
    # Evaluation is deliberately outside article QA/release state. A failed
    # preview review must never turn an accepted article into "SEO QA stopped".
    evaluation_jobs=[job for job in all_jobs if job.kind=='evaluation']
    jobs=[job for job in all_jobs if job.kind!='evaluation']
    latest=max(jobs,key=lambda item:item.updated_at) if jobs else None
    active=next((job for job in jobs if job.status in {'queued','running'}),None)
    costs=store.research_costs(run_id)
    qa={'total':0,'completed':0,'complete':False,'current_unit':None}
    partial_findings=[];recovered=None;valid_cached=set();review_details={}
    if state.editorial and state.briefing:
        from src.services.qa_units import plan,manifest_key,QAUnitResult,validate_unit,cached_unit,schema_for
        from src.services.quality import QualityReviewError
        try:
            fingerprint,units=plan(state,workflow.settings)
            qa['total']=len(units)
            saved=store.get_operation(manifest_key(state,fingerprint))
            if saved and saved.get('value'):qa.update(saved['value'])
            for unit in units:
                try:
                    entry=cached_unit(store,state,unit)
                    if not entry:continue
                    value=entry.get('value') or {}
                    result=validate_unit(state,unit,schema_for(unit['kind']).model_validate(value['result']))
                except (ValidationError,QualityReviewError,KeyError):
                    continue
                valid_cached.add(unit['dependency'])
                for item in result.findings:
                    review_details[(item.module_id,item.rule,item.quote,item.severity)]={k:v for k,v in item.model_dump(mode='json').items() if k in {'metadata_field','material_impact','requirement_id','conflicting_passages'}}
                if state.seo_check is None:
                    # Release prerequisites already have a separate authoritative
                    # projection below. These partial findings never certify QA.
                    partial_findings.extend(item for item in result.findings if item.severity!='release')
                recovery=value.get('recovery') or {}
                if (latest and latest.error_code=='schema_validation'
                    and recovery.get('job_id')==str(latest.id)
                    and recovery.get('attempt_id')==latest.attempt_id
                    and recovery.get('call_id')==latest.execution_context.get('call_id')
                    and value.get('reviewed_revision')==state.revision):
                    recovered={'unit_id':unit['id'],'call_id':recovery['call_id'],
                        'job_id':str(latest.id),'attempt_id':latest.attempt_id,
                        'recovered_at':recovery['recovered_at'],'parser':recovery['parser']}
            qa['completed']=len(valid_cached)
            # A fresh manifest may still reuse checks from unchanged modules.
            # Coverage becomes complete only through the normal aggregation path.
            if not saved or qa['completed']!=qa['total']:qa['complete']=False
        except QualityReviewError:
            qa['evidence_blocked']=True
    review_plan=None
    if state.editorial and state.briefing:
        try:
            from src.services.qa_units import estimate_units
            items=estimate_units(workflow.settings,units)
            for item in items:
                item['cached']=item['dependency'] in valid_cached
            review_plan={'units':items,'remaining':sum(not item['cached'] for item in items),
                'maximum_reservation_microusd':sum(item['maximum_reservation_microusd'] for item in items if not item['cached'])}
        except (ValueError,UnboundLocalError):
            review_plan={'blocked':True,'message':'Review context or model configuration needs attention before any paid check.'}
    latest_revision=(latest.result_revision if latest.result_revision is not None else latest.source_revision) if latest else None
    failed=latest and not recovered and latest.status in {'failed','paused'} and latest.updated_at>=state.updated_at and (latest_revision is None or latest_revision==state.revision)
    failed_check=any(check and not check.passed for check in [state.brand_check,state.seo_check])
    if active: label='Processing';action='Follow the saved task. Closing the page does not cancel server work.'
    elif recovered and state.seo_check is None:
        label='Draft saved — QA incomplete'
        action=(f"The saved {recovered['unit_id']} response was recovered locally without another paid request. "
            f"{qa['completed']} of {qa['total']} review units are saved. Review the content findings below before any further paid check. "
            'The original response-format failure remains in task history; it is not a new failure. Overall QA has not passed.')
    elif failed or failed_check or state.status=='failed': label='Checks need attention';action='Review the saved findings. Corrections can be saved locally without AI charges. Supply missing verified inputs before authorizing another paid check.'
    elif state.status=='exported': label='Approved for local export';action='Review the local export, then prepare and approve social drafts. Publishing is manual.'
    elif state.status=='seo_check_passed': label='Awaiting professional review';action='Resolve release blockers, upload the required infographic and obtain qualified professional attestation before final approval.'
    elif state.status=='awaiting_selection': label='Research saved';action='Choose an actual recommendation in Research & Topic Selection.'
    elif state.agent_outputs.get('local_correction') and state.seo_check is None: label='Locally corrected — affected checks pending';action='Open the review package. Earlier outputs remain labelled by revision. No paid check has started; review the reusable and pending checks before a separately confirmed QA request.'
    else: label='Draft saved' if state.article else 'Research saved';action='Continue the current saved stage. Generating or reviewing content can incur charges.'
    from src.services.preflight import inspect
    from src.services.link_policy import contract,followups
    from src.services.user_errors import console_release_blockers
    preflight=inspect(workflow,state)
    findings=list(preflight['findings'])
    partial_seen=set()
    for number,item in enumerate(partial_findings):
        identity=(item.module_id,item.rule,item.quote,item.severity)
        if identity in partial_seen:continue
        partial_seen.add(identity)
        findings.append({'code':'semantic_qa_partial_'+str(number),**{
            k:v for k,v in item.model_dump(mode='json').items() if k not in {'problem','correction'}},
            'blocking':item.severity=='blocking','provisional':True,
            'message':item.message_en,'action':item.action_en,
            'original_finding':f'{item.module_id}: „{item.quote}“ — {item.problem} {item.correction}',
            'retry_may_charge':False})
    for code,check in [('brand',state.brand_check),('semantic_qa',state.seo_check)]:
        if check and check.findings:
            for number,item in enumerate(check.findings):
                findings.append({'code':code+'_'+str(number),**item.model_dump(mode='json'),**review_details.get((item.module_id,item.rule,item.quote,item.severity),{}),
                    'blocking':item.severity=='blocking','message':item.message_en or 'Review the quoted passage in section '+item.module_id+'.',
                    'action':item.action_en or 'Inspect this specific finding before requesting a correction.',
                    'original_finding':next((text for text in check.errors+check.warnings if item.quote in text and text.startswith(item.module_id+':')),item.quote),'retry_may_charge':False})
            continue
        for number,text in enumerate(check.errors if check and not check.passed else []):
            findings.append({'code':code+'_'+str(number),'category':'content_defect','blocking':True,
                'message':console_release_blockers([text])[0],
                'action':'Inspect the saved German finding. Local text corrections are free; an explicitly requested AI rewrite or semantic recheck can incur charges.',
                'original_finding':text,'retry_may_charge':True})
    for number,text in enumerate(dict.fromkeys(state.release_blockers)):
        findings.append({'code':'release_'+str(number),'category':'release_prerequisite','blocking':False,
            'message':console_release_blockers([text])[0], 'action':'Supply the verified input or complete the required review before release. This does not start a paid request.',
            'original_finding':text,'retry_may_charge':False})
    if failed and latest.error_code not in {'quality_failed','preflight_blocked'}:
        from src.services.user_errors import guidance
        info=guidance(latest.error_code)
        findings.append({'code':latest.error_code,'category':category(latest.error_code),'blocking':True,
            'message':latest.failure_summary['reason'] if latest.failure_summary else info['message'],'action':'Inspect the saved request before explicitly authorizing another paid attempt.','retry_may_charge':True})
    # Multiple checks may identify the same release input. Keep all evidence in
    # details, but present the action only once. Content quotations stay distinct.
    unique=[];seen={}
    for item in findings:
        key=(item['category'],item.get('module_id'),item['message'],
             '' if item['category']=='release_prerequisite' else item.get('quote',item.get('original_finding','')))
        if key in seen:
            original=item.get('original_finding')
            prior=seen[key]
            if original and original not in prior.get('original_finding',''):
                prior['original_finding']=prior.get('original_finding','')+'\n'+original
        else:
            seen[key]=item;unique.append(item)
    findings=unique
    calls=store.list_ai_calls(run_id)
    dated=[state.updated_at]+([latest.updated_at] if latest else [])
    if recovered:dated.append(datetime.fromisoformat(recovered['recovered_at']))
    current_failure = latest.failure_summary if failed and latest.failure_summary and latest.failure_summary.get('revision') == state.revision else None
    if not current_failure and not active and state.status=='failed':
        current_failure=next((event['summary'] for event in store.list_run_failures(run_id)
            if event['job_id'] is None and event['summary'].get('revision')==state.revision),None)
    brand_state='passed' if state.brand_check and state.brand_check.passed else 'needs correction' if state.brand_check else 'pending'
    seo_state=('stopped' if current_failure and current_failure['component']=='SEO QA' and current_failure['category']!='content_corrections'
        else 'needs correction' if state.seo_check and not state.seo_check.passed
        else 'passed' if state.seo_check and state.seo_check.passed else 'pending')
    if active and active.current_step=='seo_qa_agent':seo_state='processing'
    draft_state='Approved for local export' if state.status=='exported' else 'Draft saved, unapproved' if state.article else 'No draft saved'
    from src.services.internal_workflow import flags as internal_flags
    internal = internal_flags(state, workflow.settings)
    if internal['active']:
        draft_state = ('Internally reviewed — professionally unapproved' if internal['approved'] else 'Internal review pending — professionally unapproved')
        if brand_state == 'passed' and seo_state == 'passed':
            label = ('Internal export complete — professionally unapproved' if internal['exported'] else
                     'Final internal approval pending' if internal['reviewed'] else 'Awaiting internal review')
            action = 'Continue the real internal workflow. Professional tax approval and publication approval remain separate.'
    check_status={'brand':brand_state,'seo':seo_state,'draft':draft_state,
        'summary':f'Brand Check {brand_state} · SEO QA {seo_state} · {draft_state}'}
    if seo_state=='stopped':
        action='The AI review was rejected; your draft is unchanged and unapproved. Inspect the specific diagnostic. Another paid review requires separate confirmation.'
    from src.services.guided_qa import readiness
    from src.services.guided_portal import classify,workflow_summary
    findings=classify(state,findings)
    qa_readiness=(readiness(store,state,workflow.settings,units=units,estimates=review_plan['units'])
        if review_plan and 'units' in review_plan else
        readiness(store,state,workflow.settings) if state.editorial and state.briefing else
        {'ready':False,'reason':'Create a saved draft first.'})
    if current_failure and current_failure['component']=='SEO QA' and current_failure['category']!='content_corrections':
        findings=[f for f in findings if f['code']!=getattr(latest,'error_code',None)]
        findings.append({'finding_id':current_failure['reference'],'display_kind':'review_error','category':'invalid_review','blocking':False,'message':current_failure['reason'],'action':current_failure['next_action'],'reference':current_failure['reference'],'module_id':current_failure.get('unit_id'),'reviewed_revision':state.revision,'location':{'status':'section_only','module_id':(current_failure.get('unit_id') or '').removeprefix('module:')}})
    from src.services.presentation import design_status
    workflow_view=workflow_summary(state,{'findings':findings,'qa_readiness':qa_readiness,'latest_attempt':None if not latest else {'status':latest.status}},actor)
    return {'evaluation_attempt':None if not evaluation_jobs else {'id':str(evaluation_jobs[0].id),'status':evaluation_jobs[0].status},'workflow':workflow_view,'qa_readiness':qa_readiness,'current_failure':current_failure,'local_recovery':recovered,'run_id':run_id,'revision':state.revision,'state':label,'next_action':action,
      'check_status':check_status,
      'design_status':design_status(state),
      'status':state.status,'updated_at':max(dated),'checked_at':datetime.now(timezone.utc),
      'tokens_used':store.tokens_used(run_id),'qa':qa,'blockers':state.release_blockers,
      'findings':findings,'review_plan':review_plan,'deterministic_audit':preflight.get('deterministic_audit'),'editorial_followups':followups(state),'internal_link_policy':contract(state),
      'preflight':preflight,'research_scope':state.research_scope,'campaign':store.campaign_costs(run_id),
      'latest_attempt':None if not latest else {'id':str(latest.id),'status':latest.status,
        'source_revision':latest.source_revision,'result_revision':latest.result_revision,'recovered':bool(recovered),'historical':bool(recovered) or (latest.result_revision is not None and latest.result_revision<state.revision) or latest.updated_at<state.updated_at,
        'kind':latest.kind,'step':latest.current_step,'error_code':latest.error_code,'updated_at':latest.updated_at},
      'costs':{**costs,'limit_microusd':round(min(workflow.settings.max_run_usd,state.run_budget_usd or min(1,workflow.settings.max_run_usd))*1000000),
        'provider_reported_usd':sum(call.cost for call in calls if call.cost is not None),
        'provider_costs_incomplete':any(call.cost is None for call in calls)}}
