"""Approved pre-draft reassignment is free, evidence-bound and restart-safe."""
import copy
import pytest
from src.orchestrator.workflow import Workflow
from src.orchestrator.state import RunState
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.content import SEOAnalysis,Keyword,TopicMap
from src.schemas.decisions import PageEvidence,PageAssessment,ResearchCoverage,SelectionRequest
from src.security.auth import AccountStore
from src.services.audience_profiles import assign_approved_profile
from src.services.seo_decisions import validate_selection
from src.services.verified_recommendations import recommend
from src.services.visibility import annotate

URL='https://steuerkanzlei.example/zielgruppen/einzelunternehmen/'


def setup(workflow):
    store=workflow.storage;store.account_store=AccountStore(store)
    admin=store.account_store.create_user('handoff@example.invalid','Sam','admin','synthetic-password-123')
    state=RunState(icp=copy.deepcopy(icp_1_buy_and_hold),owner_user_id=admin['id'],status='awaiting_selection',
        research_scope='site',run_budget_usd=5,campaign_budget_usd=5,decision_workflow_version=1)
    state.campaign_id=state.run_id
    state.analysis=SEOAnalysis(data_mode='live',opportunities=[],gaps=[],limitations=[],existing_pages=[URL],
        coverage=ResearchCoverage(inventory_complete=True,content_complete=True),
        keywords=[Keyword(keyword='steuerberater für einzelunternehmen',source='GSC',page=URL,impressions=1000,position=12,measurement_context={'country':'de'}),
                  Keyword(keyword='steuerberater für einzelunternehmen',source='DataForSEO',volume=260,measurement_context={'location_code':2276,'language_code':'de'})],
        page_evidence=[PageEvidence(id='page',url=URL,title='Steuerberater für Einzelunternehmen',content='Steuerberater für Einzelunternehmen. Digitale Buchhaltung.')],
        page_assessments=[PageAssessment(topic_id='topic',page_url=URL,relation='dedicated',evidence_quote='Steuerberater für Einzelunternehmen',
            issue='Prüfen Sie die Überschrift "Steuerberater für Einzelunternehmen".',reasoning='Synthetischer wörtlicher Prüfbeleg.')])
    state.research_snapshot={'dataforseo':{'status':'usable'}}
    topic={'id':'topic','title':'Einzelunternehmen','keyword':'steuerberater für einzelunternehmen','intent':'commercial','page_type':'landing_page'}
    state.topic_map=TopicMap.model_validate_json((workflow.settings.input_dir/'fixtures/topic-map.json').read_text())
    state.research_snapshot['observed_topics']=[topic]
    rec=recommend(workflow.context,state,topic,state.analysis.page_assessments)
    assert rec.drafting_eligible
    state.analysis.recommendations=[rec];annotate(state)
    workflow.graph.update_state(workflow._config(str(state.run_id)),workflow._save(state),as_node='topic_map_agent')
    return state,admin,rec.id


def test_handoff_preserves_research_budget_and_approval_gates_across_restart(settings):
    settings.demo_mode=False;settings.max_run_usd=5
    with Workflow(settings) as workflow:
        before,actor,rid=setup(workflow);run_id=str(before.run_id)
        evidence=before.analysis.model_dump(exclude={'recommendations'});costs=workflow.storage.campaign_costs(run_id)
        pending=workflow.graph.get_state(workflow._config(run_id)).next
        result=assign_approved_profile(workflow,run_id,'sole_traders',rid,actor,'Operator approved existing sole-trader service facts.',0)
        assert result.icp.id=='sole_traders' and result.analysis.recommendations[0].profile_ready
        assert result.analysis.model_dump(exclude={'recommendations'})==evidence
        assert workflow.storage.campaign_costs(run_id)==costs
        assert workflow.graph.get_state(workflow._config(run_id)).next==pending
        chosen=validate_selection(result,SelectionRequest(recommendation_id=rid,expected_revision=0))
        assert chosen.drafting_eligible and not result.article and not result.professional_review and not result.review_history
        assert workflow.storage.list_jobs(run_id)==[]
        assert len([o for o in workflow.storage.list_operations(run_id) if o['key'].startswith('audience-assignment:')])==1
        assign_approved_profile(workflow,run_id,'sole_traders',rid,actor,'Same approved details.',0)
    with Workflow(settings) as restarted:
        assert restarted.get(run_id).icp.id=='sole_traders'


def test_handoff_rejects_unsupported_evidence_and_untrusted_actor(settings):
    settings.demo_mode=False;settings.max_run_usd=5
    with Workflow(settings) as workflow:
        state,actor,rid=setup(workflow)
        member=workflow.storage.account_store.create_user('member@example.invalid','Other','member','synthetic-password-123')
        with pytest.raises(PermissionError):assign_approved_profile(workflow,str(state.run_id),'sole_traders',rid,{**member,'role':'admin'},'Synthetic approval.',0)
        with pytest.raises(ValueError,match='no approved facts'):assign_approved_profile(workflow,str(state.run_id),'site_tax_services',rid,actor,'Synthetic approval.',0)
        state.analysis.page_assessments[0].issue=''
        workflow.graph.update_state(workflow._config(str(state.run_id)),workflow._save(state),as_node='topic_map_agent')
        with pytest.raises(ValueError,match='unresolved research'):assign_approved_profile(workflow,str(state.run_id),'sole_traders',rid,actor,'Synthetic approval.',0)
        assert workflow.get(str(state.run_id)).icp.id=='icp_1_buy_and_hold'
