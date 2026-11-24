"""The trusted-local pilot command must not create accidental paid work."""
import json
import sys

import pytest
from scripts import supervised_pilot


def test_paid_pilot_requires_explicit_flag_before_opening_workflow(monkeypatch):
    monkeypatch.setattr(sys,'argv',['supervised_pilot.py','start'])
    monkeypatch.setattr(supervised_pilot,'Workflow',lambda *a:pytest.fail('No workflow should be opened without the flag'))
    with pytest.raises(SystemExit) as error:supervised_pilot.main()
    assert error.value.code==2


def test_default_status_never_submits_work(settings,tmp_path,monkeypatch,capsys):
    marker=tmp_path/'pilot.json'
    monkeypatch.setattr(supervised_pilot,'MARKER',marker)
    monkeypatch.setattr(supervised_pilot,'Settings',lambda:settings)
    monkeypatch.setattr(sys,'argv',['supervised_pilot.py'])
    supervised_pilot.main()
    assert not marker.exists()
    assert 'No work was submitted' in capsys.readouterr().out


def test_existing_pilot_refuses_replacement_run(settings,tmp_path,monkeypatch):
    marker=tmp_path/'pilot.json';marker.write_text(json.dumps({'run_id':'existing'}))
    monkeypatch.setattr(supervised_pilot,'MARKER',marker)
    monkeypatch.setattr(supervised_pilot,'Settings',lambda:settings)
    monkeypatch.setattr(sys,'argv',['supervised_pilot.py','start','--allow-paid'])
    with pytest.raises(ValueError,match='already exists'):supervised_pilot.main()


def test_upgraded_campaign_never_acknowledges_uncertain_topics_or_queues_writing(settings,tmp_path,monkeypatch,capsys):
    from src.orchestrator.workflow import Workflow
    from src.orchestrator.state import RunState
    from src.schemas.icp import icp_1_buy_and_hold
    from src.schemas.content import SEOAnalysis
    from src.schemas.decisions import SEORecommendation
    from src.security.auth import AccountStore
    with Workflow(settings) as workflow:
        store=AccountStore(workflow.storage)
        user=store.create_user('synthetic-operator@example.org','Synthetic operator','member','synthetic-password-123')
        state=RunState(icp=icp_1_buy_and_hold,owner_user_id=user['id'],status='awaiting_selection',run_budget_usd=1,internal_link_policy='starter_library')
        state.analysis=SEOAnalysis(data_mode='live',keywords=[],existing_pages=[],opportunities=[],gaps=[],limitations=[],recommendations=[
            SEORecommendation(id='synthetic-topic',topic_id='synthetic-topic',title='Unterlagen für Ihre Vermietung',keyword='Vermietung Unterlagen',search_intent='informational',page_type='blog',action='manual_review',reasoning='Synthetische Prüfung.',uncertainties=['Die vollständige Abdeckung ist ungeklärt.'])])
        workflow.storage.save_run_state(state)
    marker=tmp_path/'var/assessment/writer-upgrade-pilot.json';marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'run_id':str(state.run_id),'owner_user_id':user['id'],'selection_job_id':None}))
    monkeypatch.setattr(supervised_pilot,'ROOT',tmp_path)
    monkeypatch.setattr(supervised_pilot,'Settings',lambda:settings)
    monkeypatch.setattr(sys,'argv',['supervised_pilot.py','select','--campaign','writer-upgrade','--allow-paid'])
    supervised_pilot.main()
    assert json.loads(marker.read_text())['selection_stop_reason']=='open_topic_selection_assumptions'
    assert 'no writing job was submitted' in capsys.readouterr().out.lower()
    with Workflow(settings) as workflow:
        assert not workflow.storage.list_jobs() and not workflow.storage.list_ai_calls(str(state.run_id))
