"""Trusted, audited pre-draft profile assignment using existing research only."""
import hashlib
import json
from datetime import datetime,timezone
from src.schemas.icp import load_icp
from src.schemas.decisions import AssessmentBatch
from src.security.run_access import RunAccess


def assign_approved_profile(workflow,run_id,profile_id,recommendation_id,actor,reason,expected_revision):
    from src.services.seo_decisions import DecisionService
    from src.services.verified_recommendations import recommend
    from src.services.visibility import annotate
    from src.services.preflight import inspect
    import re
    if not re.fullmatch(r'[a-z0-9_]+',profile_id):raise ValueError('Invalid configured profile ID')
    if not reason.strip():raise ValueError('Record the source and reason for this company-fact approval')
    profile=load_icp(workflow.settings.input_dir/'icps'/(profile_id+'.json'))
    if not profile.drafting_approved or not profile.approved_fact_sources:raise ValueError('The configured profile has no approved facts')
    store=workflow.storage;accounts=getattr(store,'account_store',None)
    if accounts is None:raise PermissionError('Authenticated account context required')
    actor=accounts.user('id',actor['id'])
    from src.security.sanitize import sanitize_text
    reason=sanitize_text(reason,2000)
    with store.run_lock(str(run_id)):
        state=workflow.get(str(run_id));access=RunAccess(accounts);access.require(state,actor)
        if actor['role']!='admin':raise PermissionError('A trusted administrator must assign approved drafting facts')
        if state.revision!=expected_revision or state.status!='awaiting_selection' or state.article or state.briefing or state.selected_recommendation or not state.decision_workflow_version:
            raise ValueError('Audience assignment is available only before drafting and topic selection')
        if any(j.status in {'queued','running','paused'} for j in store.list_jobs(str(run_id))):raise ValueError('Inspect active or uncertain work before changing the drafting audience')
        if any(o['status']=='pending' for o in store.list_operations(str(run_id)) if not o['key'].startswith('audience-assignment:')):
            raise ValueError('Resolve pending operations before assigning a profile')
        annotate(state)
        chosen=next((r for r in state.analysis.recommendations if r.id==recommendation_id),None) if state.analysis else None
        if chosen is None or chosen.audience_profile_id!=profile.id:raise ValueError('The configured profile does not match this recommendation')
        known={p.url for p in state.analysis.page_evidence}
        if not set(profile.approved_fact_sources)<=known or chosen.target_url not in profile.approved_fact_sources:
            raise ValueError('Approved company facts must reference the saved existing target page')
        topic={'id':chosen.topic_id,'title':chosen.title,'keyword':chosen.keyword,'intent':chosen.search_intent,'page_type':chosen.page_type}
        matches=[a for a in state.analysis.page_assessments if a.topic_id==chosen.topic_id]
        DecisionService(workflow.context).validate_assessments([topic],state.analysis.page_evidence,AssessmentBatch(assessments=matches))
        verified=recommend(workflow.context,state,topic,matches)
        if not verified.drafting_eligible or verified.research_needed or verified.uncertainties:
            raise ValueError('Company-fact approval cannot override unresolved research or content prerequisites')
        if verified.id!=chosen.id or verified.action!=chosen.action or verified.target_url!=chosen.target_url:
            raise ValueError('Saved recommendation and current evidence no longer agree')
        fingerprint=hashlib.sha256(profile.model_dump_json().encode()).hexdigest()
        key=f'audience-assignment:{run_id}:{state.revision}:{chosen.id}:{fingerprint}'
        prior=store.get_operation(key)
        if prior and prior['status']=='completed' and state.icp==profile:return state
        store.begin_operation(key,str(run_id))
        event={'previous_profile':state.icp.model_dump(mode='json'),'approved_profile':profile.model_dump(mode='json'),
            'recommendation_id':chosen.id,'revision':state.revision,'actor_user_id':actor['id'],
            'reason':reason,'recorded_at':datetime.now(timezone.utc).isoformat(),'paid_requests':0}
        state.research_snapshot.setdefault('research_audience',state.icp.model_dump(mode='json'))
        state.icp=profile
        state.analysis.recommendations=[verified if r.id==chosen.id else r for r in state.analysis.recommendations]
        annotate(state)
        state.agent_outputs['audience_assignment']=event
        envelope=workflow._save(state)
        workflow.graph.update_state(workflow._config(str(run_id)),envelope,as_node='topic_map_agent')
        # Re-establish only the free selection interrupt, never resume a paid node.
        workflow.graph.invoke(None,workflow._config(str(run_id)),durability='sync')
        store.finish_operation(key,event)
        # This is configuration, not topic selection, generation or editorial approval.
        inspect(workflow,state,chosen.id)
        return state
