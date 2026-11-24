"""Actual internal approvals are durable and never professional/publication approvals."""
import io,json,zipfile
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.services.internal_workflow import InternalAction,act,view,download,context,flags
from src.security.auth import AccountStore
from tests.test_solo_evaluation import fixture
from tests.conftest import authenticated_headers,own_fixture

@pytest.fixture(autouse=True)
def combined(settings):settings.combined_mode=True;settings.seo_decisions_enabled=True


def command(s,settings,action,**kw):
    return InternalAction(action=action,request_id=uuid4(),expected_revision=s.revision,context=context(s,settings),**kw)


def test_real_internal_journey_is_durable_idempotent_and_isolated(settings,monkeypatch):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();w=client.app.state.workflow;s=fixture(settings,w)
        headers=authenticated_headers(client,'Owner');own_fixture(client,s);w.storage.save_run_state(s)
        actor=client.app.state.auth.identify(client.cookies.get('review_session'));w.storage.account_store=client.app.state.auth.store
        monkeypatch.setattr(w.context.llm,'generate',lambda *a,**kw:pytest.fail('No provider calls allowed'))
        before=s.model_copy(deep=True);calls=w.storage.list_ai_calls(str(s.run_id));costs=w.storage.research_costs(str(s.run_id));jobs=w.storage.list_jobs(str(s.run_id))
        req=command(s,settings,'enable',limitations_acknowledged=True);act(w,str(s.run_id),req,actor)
        s=w.get(str(s.run_id));assert flags(s,settings)['active']
        with pytest.raises(ValueError):act(w,str(s.run_id),command(s,settings,'approve',approve_internal_only=True,limitations_acknowledged=True),actor)
        with pytest.raises(ValueError):act(w,str(s.run_id),command(s,settings,'review',notes='Actually reviewed internally.'),actor)
        req=command(s,settings,'review',notes='Inspected the actual outputs for internal use; release inputs remain unresolved.',content_reviewed=True,sources_reviewed=True,graphic_reviewed=True,limitations_acknowledged=True)
        act(w,str(s.run_id),req,actor);act(w,str(s.run_id),req,actor)
        s=w.get(str(s.run_id));assert len(s.internal_workflow['events'])==2 and flags(s,settings)['reviewed']
        req=command(s,settings,'approve',approve_internal_only=True,limitations_acknowledged=True);act(w,str(s.run_id),req,actor)
        s=w.get(str(s.run_id));assert flags(s,settings)['approved']
        req=command(s,settings,'export');act(w,str(s.run_id),req,actor);act(w,str(s.run_id),req,actor)
        s=w.get(str(s.run_id));assert flags(s,settings)['exported'] and len(s.internal_workflow['events'])==4
        with zipfile.ZipFile(io.BytesIO(download(settings,w.storage,s))) as z:
            rec=json.loads(z.read('internal-approval.json'));assert rec['professional_approval'] is False and rec['publication_approved'] is False
            assert rec['records']['review']['actor_id']==actor['id']
            assert b'simulated' not in z.read('README.txt').lower()
            assert 'test-walkthrough.json' not in z.namelist()
            assert 'professionally unapproved' in z.read('START_HERE.html').decode()
            assert z.read('article.html')==z.read('website/preview.html')
        from pathlib import Path
        artifact=Path(s.internal_workflow['export']['path']);original=artifact.read_bytes();artifact.write_bytes(b'tampered')
        with pytest.raises(ValueError):download(settings,w.storage,s)
        artifact.write_bytes(original)
        assert s.professional_review is None and s.status==before.status and not s.publication
        assert s.editorial==before.editorial and s.revision==before.revision and s.seo_check==before.seo_check
        assert calls==w.storage.list_ai_calls(str(s.run_id)) and costs==w.storage.research_costs(str(s.run_id)) and jobs==w.storage.list_jobs(str(s.run_id))
        from src.services.progress import progress
        p=progress(w,s,actor);assert all(step['status']=='complete' for step in p['workflow']['steps'])
        assert p['workflow']['steps'][5]['label']=='Internal review'
        assert client.post(f'/runs/{s.run_id}/professional-reviews',headers=headers,json={'expected_revision':s.revision,'factual_review_confirmed':True,'graphic_review_confirmed':True,'notes':'Internal review is not professional.'}).status_code==403
        # Restart reads persisted, actual approvals rather than browser simulation.
        reloaded=w.storage.get_run_state(str(s.run_id));assert flags(reloaded,settings)['exported']
        moved=s.model_copy(deep=True);moved.owner_user_id='new-owner';assert not flags(moved,settings)['reviewed']
        s.editorial['title']+=' changed';assert not flags(s,settings)['reviewed']
        with pytest.raises(ValueError):download(settings,w.storage,s)
        s=w.get(str(s.run_id))
        act(w,str(s.run_id),command(s,settings,'disable',limitations_acknowledged=True),actor)
        s=w.get(str(s.run_id));assert not flags(s,settings)['active']
        act(w,str(s.run_id),command(s,settings,'enable',limitations_acknowledged=True),actor)
        assert not flags(w.get(str(s.run_id)),settings)['reviewed']


def test_api_permissions_stale_context_and_genuine_qa_required(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();w=client.app.state.workflow;s=fixture(settings,w)
        headers=authenticated_headers(client,'Owner');own_fixture(client,s);w.storage.save_run_state(s)
        path=f'/runs/{s.run_id}/internal-workflow';p=client.get(path).json()
        body={'action':'enable','request_id':str(uuid4()),'expected_revision':s.revision,'context':p['context'],'limitations_acknowledged':True}
        assert client.post(path,json=body,headers=headers).status_code==200
        assert client.post(path,json=body,headers=headers).status_code==200
        bad={**body,'action':'disable'};assert client.post(path,json=bad,headers=headers).status_code==409
        assert client.get(path+'/download').status_code==409
        s=w.get(str(s.run_id));s.seo_check=None;w.graph.update_state(w._config(str(s.run_id)),{'run':s.model_dump(mode='json')});w.storage.save_run_state(s)
        p=client.get(path).json();body.update(action='review',request_id=str(uuid4()),context=p['context'],notes='Review is incomplete without automated QA.',content_reviewed=True,sources_reviewed=True,graphic_reviewed=True)
        assert client.post(path,json=body,headers=headers).status_code==409
        headers=authenticated_headers(client,'Other');assert client.get(path).status_code==404
        assert client.post(path,json=body,headers=headers).status_code==404
        client.cookies.clear();assert client.get(path).status_code==401
