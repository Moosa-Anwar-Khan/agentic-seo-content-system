"""Read-only diagnostics from saved evidence. Never retrieves or generates."""
from src.services.link_policy import contract, guide_candidates


class PreflightBlocked(ValueError):
    code = 'preflight_blocked'


def inspect(workflow, state, recommendation_id=None, *, phase=None):
    from src.services.model_routes import resolve_route, credential, validate_text_route, text_request_options, ModelConfigurationError
    from src.services.research_budget import text_upper_price, micros
    from src.combined.evidence import source_issue
    selected=state.selected_recommendation
    if recommendation_id:
        selected=next((r for r in (state.analysis.recommendations if state.analysis else []) if r.id==recommendation_id),None)
        if selected is None:raise KeyError('Recommendation not found')
    issues=[];routes={};missing=[]
    def issue(code,message,action,category='missing_input',blocking=True):
        issues.append({'code':code,'category':category,'message':message,'action':action,'blocking':blocking,'retry_may_charge':False})
    costs=workflow.storage.research_costs(str(state.run_id))
    if selected and selected.audience_profile_id and selected.audience_profile_id!=state.icp.id:
        issue('audience_mismatch','This recommendation belongs to a different audience/service profile.','Create work using the matching approved profile; do not reuse an unrelated audience for writing.')
    limit=round(min(workflow.settings.max_run_usd,state.run_budget_usd or min(1,workflow.settings.max_run_usd))*1000000)
    remaining=max(0,limit-costs['recorded_microusd']-costs['reserved_microusd'])
    campaign=workflow.storage.campaign_costs(str(state.run_id))
    if campaign:
        remaining=min(remaining,max(0,campaign['limit_microusd']-campaign['recorded_microusd']-campaign['reserved_microusd']))
    if state.icp.id!='icp_1_buy_and_hold' and (not state.icp.drafting_approved or not state.icp.approved_fact_sources):
        issue('audience_approval','This audience/service profile is available for research but has no approved drafting facts.','Approve the configured profile and its genuine fact sources before paid writing.')
    month=workflow.storage.monthly_costs()
    monthly_remaining=None if month['limit_microusd'] is None else max(0,month['limit_microusd']-month['recorded_microusd']-month['reserved_microusd'])
    if not remaining or monthly_remaining==0:
        issue('budget','The run or shared monthly allowance is fully recorded or reserved.','Inspect the ledger. No further paid request can start.','budget')
    for task,schema in [('briefing','SelectedBriefing'),('content','EditorialOutput'),('seo_qa','QAUnitResult')]:
        try:
            route=resolve_route(workflow.settings,task)
            options=text_request_options(workflow.settings,route,schema)
            routes[task]={'provider':route.provider,'model':route.model,'reasoning_effort':options.get('reasoning_effort'),'max_output_tokens':options['max_tokens']}
            if not workflow.settings.demo_mode:
                credential(workflow.settings,route);validate_text_route(workflow.settings,route,1024,options['max_tokens'])
                minimum=micros(text_upper_price(workflow.settings,route,1024,options['max_tokens']))
                routes[task]['minimum_reservation_microusd']=minimum
                if task==phase and minimum>min(remaining,monthly_remaining if monthly_remaining is not None else remaining):
                    issue('next_request_budget','The remaining allowance cannot cover even the minimum reservation for this step.','Keep the saved results. This run allowance cannot be increased.','budget')
        except (ValueError,ModelConfigurationError):
            issue('model_configuration_'+task,f'The {task.replace("_"," ")} route, credentials, context capability or dated pricing needs attention.','Check System status and verified model configuration; no credential is exposed.','configuration')
    page_type=state.briefing.page_type if state.briefing else selected.page_type if selected else None
    guides=guide_candidates(state,selected.keyword if selected else '')
    if page_type=='blog' and len(guides)<contract(state)['related_minimum']:
        issue('guide_inventory','The saved inventory does not contain enough candidate guide destinations for this run’s linking policy.','Retrieve or supply suitable existing pages before paid writing; do not invent destinations.')
    if not selected and state.decision_workflow_version and not state.briefing:
        missing.append('Select an eligible recommendation before drafting.')
        recommendations=state.analysis.recommendations if state.analysis else []
        informational=[r for r in recommendations if r.page_type=='blog']
        clear=[r for r in informational if r.drafting_eligible and not r.research_needed and not r.uncertainties and r.action in {'create_new','create_cluster','optimize'}]
        if informational and not clear:
            issue('topic_selection_decision','Informational recommendations still contain open selection assumptions. No topic was automatically approved for writing.',
                  'Review website-inventory completeness, measured demand and existing-page coverage in Research & Topic Selection. Correct the evidence or explicitly choose a recommendation with documented reasoning; the supervised pilot will not acknowledge assumptions for you.',
                  category='missing_input',blocking=False)
    records=state.research_packet.get('official_sources',[])
    if not records:
        saved=workflow.storage.get_operation(f'preflight-inputs:{state.run_id}:{state.revision}')
        records=((saved or {}).get('value') or {}).get('research_packet',{}).get('official_sources',[])
    eligible=[]
    for record in records:
        reason=source_issue(record,workflow.settings.tax_year)
        if reason:
            issue('source_ineligible',f'Saved source {record.get("id","unknown")} is excluded: {reason}.','Retrieve a complete eligible official source; never relabel its year or treat a forum as legal authority.',blocking=False)
        else:eligible.append(record['id'])
    if not eligible:
        missing.append('Complete eligible official research is required before briefing or writing.')
        if phase in {'briefing','content'} or records:
            issue('official_evidence','No complete eligible official source is available for writing.','Resolve source retrieval and applicable-year issues before generating an article.')
    blockers=[i for i in issues if i['blocking']]
    from src.services.qa_audit import audit
    from src.services.guided_qa import readiness
    qa_readiness=readiness(workflow.storage,state,workflow.settings) if state.editorial and state.briefing else {'ready':False,'reason':'Create a saved draft first.'}
    return {'qa_readiness':qa_readiness,'deterministic_audit':audit(state,workflow.settings),'run_id':str(state.run_id),'revision':state.revision,'ready':not blockers and not missing,
            'status':'blocked' if blockers else 'needs_research' if missing else 'ready',
            'internal_link_policy':contract(state),'guide_candidates':guides,'eligible_source_ids':eligible,
            'missing_research':missing,'findings':issues,'model_routes':routes,
            'costs':{**costs,'limit_microusd':limit,'remaining_microusd':remaining,'monthly_remaining_microusd':monthly_remaining},
            'cost_notice':'Reservation checks are conservative, not an exact invoice. The actual complete request must also pass atomic USD, token and context limits.'}


def ensure_ready(context,state,phase):
    if context.settings.demo_mode:return
    report=inspect(context,state,phase=phase)
    state.agent_outputs['preflight']=report
    # A failed LangGraph node retains its previous checkpoint. Preserve researched
    # inputs independently so the worker cannot erase them while reconciling state.
    if state.research_packet.get('official_sources'):
        key=f'preflight-inputs:{state.run_id}:{state.revision}'
        context.storage.begin_operation(key,str(state.run_id))
        context.storage.finish_operation(key,{'research_packet':state.research_packet,'report':report})
    context.storage.save_run_state(state)
    if report['status']=='blocked' or (phase in {'briefing','content'} and report['missing_research']):
        raise PreflightBlocked('Saved inputs do not satisfy the preflight requirements')
