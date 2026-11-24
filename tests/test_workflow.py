import json
import shutil
from pathlib import Path
import pytest
from src.orchestrator.workflow import Workflow
from src.orchestrator.registry import register_agent
from src.agents.base import BaseAgent
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.review import ReviewRequest
from src.schemas.content import CheckResult

def request(state,action='needs_revision',target='content_agent'):
    return ReviewRequest(action=action,feedback_target_agent=target,feedback_notes='Bitte ergänzen Sie eine Checkliste für das Erstgespräch.',expected_revision=state.revision)

def test_restart_revision_approval_and_stale_review(settings):
    reviewer='Alex'
    with Workflow(settings) as workflow:
        state=workflow.start(icp_1_buy_and_hold)
        assert state.status=='seo_check_passed'
        run_id=str(state.run_id)
        assert not (settings.output_dir/'runs'/run_id/'approved').exists()
    # New graph and DB connections must resume the durable human interrupt.
    with Workflow(settings) as workflow:
        original=workflow.get(run_id)
        revised=workflow.submit_review(run_id,request(original),reviewer)
        assert revised.revision==1 and revised.status=='seo_check_passed'
        assert revised.executed_agents.count('seo_strategy_agent')==1
        assert revised.executed_agents.count('topic_map_agent')==1
        assert revised.executed_agents.count('briefing_agent')==1
        assert revised.executed_agents.count('content_agent')==2
        assert 'review-ergaenzung' in revised.html
        assert revised.review_history[0].reviewer_name=='Alex'
        with pytest.raises(ValueError): workflow.submit_review(run_id,request(original,'approved'),reviewer)
        approved=workflow.submit_review(run_id,request(revised,'approved'),'Sam')
        assert approved.status=='exported'
        assert approved.executed_agents[-1]=='publisher_agent'
        assert approved.review_history[-1].reviewer_name=='Sam'
        assert all(Path(p).is_file() for p in approved.exported_paths)
        with workflow.storage.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==2

def test_briefing_revision_reexecutes_downstream_only(settings):
    with Workflow(settings) as workflow:
        initial=workflow.start(icp_1_buy_and_hold)
        revised=workflow.submit_review(str(initial.run_id),request(initial,target='briefing_agent'),'Alex')
        assert revised.executed_agents.count('briefing_agent')==2
        assert revised.executed_agents.count('content_agent')==2
        assert revised.executed_agents.count('seo_strategy_agent')==1
        assert any(h.h2=='Unterlagen für das Erstgespräch' for h in revised.briefing.headings)

def test_rejection_never_exports_final(settings):
    with Workflow(settings) as workflow:
        state=workflow.start(icp_1_buy_and_hold)
        rejected=workflow.submit_review(str(state.run_id),request(state,'rejected'),'Alex')
        assert rejected.status=='rejected'
        assert 'publisher_agent' not in rejected.executed_agents
        assert not (settings.output_dir/'runs'/str(state.run_id)/'approved').exists()

def test_failed_qa_is_bounded(settings):
    settings.max_auto_revisions=1
    with Workflow(settings) as workflow:
        def fail_brand(state):
            state.brand_check=CheckResult(passed=False,errors=['Pflicht-Disclaimer prüfen'])
            return state
        workflow.agents['brand_agent'].execute=fail_brand
        state=workflow.start(icp_1_buy_and_hold)
        assert state.status=='failed' and state.auto_revisions==1
        assert 'publisher_agent' not in state.executed_agents
        assert 'seo_qa_agent' not in state.executed_agents

def test_new_agent_loads_without_orchestrator_edit(settings,tmp_path):
    @register_agent('additional_editorial_agent')
    class ExtraAgent(BaseAgent):
        def execute(self,state):
            state.analysis.opportunities.append('Zusätzliche redaktionelle Prüfung ausgeführt')
            return state
    destination=tmp_path/'inputs'
    shutil.copytree(settings.input_dir,destination)
    path=destination/'config/pipeline.json'
    data=json.loads(path.read_text())
    data['agents'].insert(2,{'name':'additional_editorial_agent','role':'standard'})
    path.write_text(json.dumps(data))
    settings.input_dir=destination
    with Workflow(settings) as workflow:
        state=workflow.start(icp_1_buy_and_hold)
        assert state.status=='seo_check_passed'
        assert 'additional_editorial_agent' in state.executed_agents
