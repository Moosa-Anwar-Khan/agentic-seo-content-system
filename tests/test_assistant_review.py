"""Read-only independent observations cannot change automatic QA or human approvals."""
import hashlib,json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.services.assistant_review import load
from src.services.evaluation import digest,latest
from tests.test_solo_evaluation import fixture
from tests.conftest import authenticated_headers,own_fixture


def record(settings,storage,state):
    folder=settings.output_dir/'runs'/str(state.run_id)/'assistant-reviews'/f'revision-{state.revision:03d}'
    folder.mkdir(parents=True)
    r={'run_id':str(state.run_id),'revision':state.revision,'image_sha256':state.graphic_sha256,
        'method':'Synthetic independent assistant inspection, not an API call.','reviewed_at':'2026-10-21T19:00:00+00:00',
        'observations':[{'category':'observation','message_en':'Synthetic preparation points match.'}],
        'limitations':['Synthetic only; automatic API review remains pending.'],'private_debug':'must not be returned'}
    raw=json.dumps(r).encode();page=b'<html><body>Independent assistant review; unapproved.</body></html>'
    (folder/'image-review.json').write_bytes(raw);(folder/'REVIEW.html').write_bytes(page)
    m={'run_id':str(state.run_id),'revision':state.revision,'article_hash':digest(state.editorial),
       'brief_hash':digest(state.editorial.get('graphic',{})),'image_sha256':state.graphic_sha256,
       'evaluation_results_hash':digest((latest(storage,state,settings) or {}).get('results',{})),
       'review_sha256':hashlib.sha256(raw).hexdigest(),'html_sha256':hashlib.sha256(page).hexdigest()}
    (folder/'manifest.json').write_text(json.dumps(m))
    return folder

@pytest.fixture(autouse=True)
def combined(settings):settings.combined_mode=True;settings.seo_decisions_enabled=True


def test_projection_and_integrity_are_read_only(settings):
    from src.orchestrator.workflow import Workflow
    with Workflow(settings) as w:
        s=fixture(settings,w);before=w.storage.get_run_state(str(s.run_id)).model_dump_json()
        folder=record(settings,w.storage,s);value=load(settings,w.storage,s)
        assert 'private_debug' not in value['review'] and 'API image check remains pending' in value['review']['notice']
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert not w.storage.list_ai_calls(str(s.run_id))
        (folder/'REVIEW.html').write_text('<script>tampered</script>')
        assert load(settings,w.storage,s) is None

@pytest.mark.parametrize('field',['revision','article','image','results'])
def test_changed_inputs_hide_stale_observations(settings,field):
    from src.orchestrator.workflow import Workflow
    with Workflow(settings) as w:
        s=fixture(settings,w);record(settings,w.storage,s)
        if field=='revision':s.revision+=1
        elif field=='article':s.editorial['title']+=' changed'
        elif field=='image':s.graphic_sha256='0'*64
        else:
            from src.services.evaluation import write,current_key
            write(w.storage,'evaluation:'+str(s.run_id)+':'+current_key(s,settings),s,{'results':{'changed':True}})
        assert load(settings,w.storage,s) is None


def test_private_endpoints_do_not_grant_approval_or_allow_post(settings):
    with TestClient(create_app(settings)) as c:
        c.app.state.worker.close();headers=authenticated_headers(c,'Solo');w=c.app.state.workflow
        s=fixture(settings,w);own_fixture(c,s);w.storage.save_run_state(s);record(settings,w.storage,s)
        before=w.storage.get_run_state(str(s.run_id)).model_dump_json();base=f'/runs/{s.run_id}/evaluation'
        assert c.get(base+'/assistant-review').json()['review']['revision']==s.revision
        r=c.get(base+'/combined-review');assert r.status_code==200 and 'sandbox' in r.headers['content-security-policy']
        assert c.post(base+'/assistant-review',headers=headers,json={}).status_code==405
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        authenticated_headers(c,'Stranger')
        assert c.get(base+'/assistant-review').status_code==404 and c.get(base+'/combined-review').status_code==404
        c.cookies.clear();assert c.get(base+'/combined-review').status_code==401
