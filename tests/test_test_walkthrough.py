"""A rehearsal cannot create real approvals, buy work or bypass genuine QA."""
import io
import json
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.orchestrator.workflow import Workflow
from src.schemas.jobs import Job
from src.services.test_walkthrough import snapshot, export, LABEL
from tests.test_solo_evaluation import fixture
from tests.conftest import authenticated_headers, own_fixture

@pytest.fixture(autouse=True)
def combined(settings):
    settings.combined_mode=True; settings.seo_decisions_enabled=True


def test_rehearsal_export_is_read_only_unapproved_and_preserves_real_gates(settings, monkeypatch):
    with Workflow(settings) as w:
        s=fixture(settings,w)
        monkeypatch.setattr(w.context.llm,'generate',lambda *a,**k:pytest.fail('No provider call'))
        before=w.storage.get_run_state(str(s.run_id)).model_dump_json()
        calls=w.storage.list_ai_calls(str(s.run_id));jobs=w.storage.list_jobs(str(s.run_id));costs=w.storage.research_costs(str(s.run_id))
        p=snapshot(settings,w.storage,s);assert p['ready'] and len(p['steps'])==8
        for _ in range(2):
            raw=export(settings,w.storage,s,s.revision,p['context'])
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                receipt=json.loads(z.read('test-walkthrough.json'))
                assert receipt['professional_attestation'].startswith('SIMULATED ONLY')
                assert receipt['final_approval'].startswith('SIMULATED ONLY')
                assert receipt['publication'] is False
                assert LABEL.encode() in z.read('README.txt') and LABEL.encode() in z.read('START_HERE.html')
                assert z.read('infographic-unapproved.webp')
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert w.storage.list_ai_calls(str(s.run_id))==calls and w.storage.list_jobs(str(s.run_id))==jobs
        assert w.storage.research_costs(str(s.run_id))==costs
        assert s.professional_review is None and s.status!='exported'


def test_missing_work_active_jobs_and_stale_context_block_export(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);baseline=s.model_copy(deep=True);p=snapshot(settings,w.storage,s)
        with pytest.raises(ValueError):export(settings,w.storage,s,s.revision+1,p['context'])
        s.editorial['title']+=' changed'
        with pytest.raises(ValueError):export(settings,w.storage,s,s.revision,p['context'])
        s.seo_check=None
        p=snapshot(settings,w.storage,s);assert not p['ready'] and any('QA' in b for b in p['blockers'])
        with pytest.raises(ValueError):export(settings,w.storage,s,s.revision,p['context'])
        s=baseline.model_copy(deep=True);s.graphic_sha256='changed'
        assert not snapshot(settings,w.storage,s)['ready']
        s=baseline.model_copy(deep=True)
        w.storage.create_job(Job(run_id=s.run_id,kind='quality_checks',idempotency_key=uuid4(),reviewer='Sam',payload={'source_revision':s.revision}))
        assert not snapshot(settings,w.storage,s)['ready']


def test_authenticated_owner_can_rehearse_without_professional_role(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();w=client.app.state.workflow;s=fixture(settings,w)
        authenticated_headers(client,'Solo');own_fixture(client,s);w.storage.save_run_state(s)
        path=f'/runs/{s.run_id}/evaluation'
        p=client.get(path+'/test-walkthrough');assert p.status_code==200
        ctx=p.json()['context']
        assert client.get(path+'/test-export',params={'revision':s.revision,'context':ctx}).status_code==200
        assert client.get(path+'/test-export',params={'revision':s.revision+1,'context':ctx}).status_code==409
        assert client.get(path+'/test-export',params={'revision':s.revision,'context':'bad'}).status_code==422
        assert w.get(str(s.run_id)).professional_review is None
        assert w.get(str(s.run_id)).status!='exported'
        authenticated_headers(client,'Other')
        assert client.get(path+'/test-walkthrough').status_code==404
        assert client.get(path+'/test-export',params={'revision':s.revision,'context':ctx}).status_code==404
        client.cookies.clear()
        assert client.get(path+'/test-walkthrough').status_code==401


def test_approved_run_rehearsal_still_produces_unapproved_copy(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);s.status='exported'
        p=snapshot(settings,w.storage,s)
        with zipfile.ZipFile(io.BytesIO(export(settings,w.storage,s,s.revision,p['context']))) as z:
            assert b'nicht freigegeben' in z.read('article.html').lower()
            assert b'UNAPPROVED TEST OUTPUT' in z.read('website/UPLOAD_INSTRUCTIONS.txt')
        assert s.status=='exported'
