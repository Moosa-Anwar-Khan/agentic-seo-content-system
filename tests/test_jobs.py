from uuid import uuid4
from io import BytesIO
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import os
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from src.api.app import create_app
from src.orchestrator.workflow import Workflow
from src.schemas.jobs import Job
from src.orchestrator.state import RunState
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.review import ReviewEvent
from src.schemas.content import CheckResult
from src.schemas.content import Keyword
from src.schemas.assets import ImageCreative
from src.services.jobs import JobWorker
from src.services.image_processing import master_image_path
from src.services.llm import ModelRouter
from src.services.recovery import running_job, UncertainOperation
from src.security.auth import AuthService, digest, totp_code
from src.integrations.sources import Sources


def test_worker_finishes_a_saved_run_without_a_browser(settings):
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(job, state)
        worker = JobWorker(settings, workflow)
        assert worker.process_one()
        assert workflow.storage.get_job(str(job.id)).status == 'completed'
        result = workflow.get(str(state.run_id))
        assert result.status == 'seo_check_passed'
        assert len(result.executed_agents) == 6
    with Workflow(settings) as reopened:
        assert reopened.get(str(state.run_id)).status == 'seo_check_passed'


def test_session_cookie_and_idempotent_job_submission(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.auth.store.create_user('alex@example.org','Alex','reviewer','temporary-password-123')
        login = client.post('/auth/login', json={'email':'alex@example.org','password':'temporary-password-123'})
        assert login.status_code == 200
        challenge=login.json()['challenge']
        assert login.json()['stage']=='setup_password'
        assert client.post('/auth/setup/password',json={'challenge':challenge,'password':'new-long-password-456'}).status_code==200
        setup=client.post('/auth/setup/mfa',json={'challenge':challenge}).json()
        signed=client.post('/auth/setup/confirm',json={'challenge':challenge,'code':totp_code(setup['secret'])})
        assert signed.status_code==200
        assert signed.json()['reviewer']=='Alex'
        assert 'httponly' in signed.headers['set-cookie'].lower()
        cookie={'X-CSRF-Token':signed.json()['csrf_token']}
        assert client.get('/session').json()['reviewer']=='Alex'
        request = {'kind': 'start', 'icp_id': 'icp_1_buy_and_hold', 'idempotency_key': str(uuid4())}
        first = client.post('/jobs', json=request, headers=cookie)
        second = client.post('/jobs', json=request, headers=cookie)
        assert first.status_code == second.status_code == 202
        assert first.json()['id'] == second.json()['id']
        assert first.json()['run_id'] == second.json()['run_id']
        assert client.get('/jobs', headers=cookie).status_code == 200
        assert client.post('/session/logout', headers=cookie).status_code == 200
        assert client.get('/jobs').status_code == 401
        assert client.app.state.workflow.storage.get_job(first.json()['id']) is not None


def test_uncertain_paid_step_pauses_until_explicit_retry(settings):
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(job, state)
        key = f'{job.id}:paid:test'
        workflow.storage.begin_operation(key, str(state.run_id))
        assert workflow.storage.get_operation(key)['status'] == 'pending'
        workflow.storage.recover_jobs()
        assert workflow.storage.get_job(str(job.id)).status == 'queued'
        workflow.storage.reset_pending_operations(str(job.id))
        assert workflow.storage.begin_operation(key, str(state.run_id))['status'] == 'new'


def test_failed_node_restarts_from_last_checkpoint(settings):
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(job, state)
        worker = JobWorker(settings, workflow)
        original = workflow.agents['briefing_agent'].execute
        workflow.agents['briefing_agent'].execute = lambda _: (_ for _ in ()).throw(RuntimeError('test failure'))
        worker.process_one()
        failed = workflow.get(str(state.run_id))
        assert workflow.storage.get_job(str(job.id)).status == 'failed'
        assert failed.executed_agents == ['seo_strategy_agent', 'topic_map_agent']
        workflow.agents['briefing_agent'].execute = original
        job = workflow.storage.get_job(str(job.id))
        job.status = 'queued'
        workflow.storage.save_job(job)
        worker.process_one()
        recovered = workflow.get(str(state.run_id))
        assert recovered.status == 'seo_check_passed'
        assert recovered.executed_agents.count('seo_strategy_agent') == 1


def test_image_job_retries_local_variant_without_another_provider_call(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake-image-key'
    calls = []
    def fake_generate(_self, _prompt, size):
        calls.append(size)
        data = BytesIO()
        Image.new('RGB', size, '#dce8f5').save(data, format='PNG')
        return data.getvalue(), None
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate', fake_generate)
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        social = Job(run_id=state.run_id, kind='social', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(social)
        worker = JobWorker(settings, workflow)
        worker.process_one()
        assert len(workflow.storage.list_assets(str(state.run_id), 'post')) == 2
        image_job = Job(run_id=state.run_id, kind='image_set', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(image_job)
        from src.services.images import process_image as original
        def fail_linkedin(raw, platform, png_requested, image_settings, allow_upscale=True):
            if platform == 'linkedin':
                raise ValueError('temporary local processing failure')
            return original(raw, platform, png_requested, image_settings, allow_upscale)
        monkeypatch.setattr('src.services.images.process_image', fail_linkedin)
        worker.process_one()
        assert workflow.storage.get_job(str(image_job.id)).status == 'failed'
        masters = workflow.storage.list_assets(str(state.run_id), 'master')
        assert len(masters) == 1 and master_image_path(settings, masters[0]).is_file()
        monkeypatch.setattr('src.services.images.process_image', original)
        image_job = workflow.storage.get_job(str(image_job.id))
        image_job.status = 'queued'
        workflow.storage.save_job(image_job)
        worker.process_one()
        assert workflow.storage.get_job(str(image_job.id)).status == 'completed'
        assert len(calls) == 1
        assert len(workflow.storage.list_assets(str(state.run_id), 'image')) == 3
        assert workflow.get(str(state.run_id)).executed_agents.count('content_agent') == 1


def test_queued_review_preserves_human_approval(settings):
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        event = ReviewEvent(action='approved', expected_revision=state.revision,
                            reviewer_name='Sam', event_id=str(uuid4()))
        job = Job(run_id=state.run_id, kind='review', reviewer='Sam', idempotency_key=uuid4(),
                  payload={'event': event.model_dump(mode='json')})
        workflow.storage.create_job(job)
        JobWorker(settings, workflow).process_one()
        assert workflow.storage.get_job(str(job.id)).status == 'completed'
        result = workflow.get(str(state.run_id))
        assert result.status == 'exported'
        assert result.review_history[-1].reviewer_name == 'Sam'


def test_completed_paid_text_call_is_reused(settings, monkeypatch):
    import litellm
    settings.demo_mode = False
    settings.llm_api_key = 'fake-test-key'
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        calls = []
        monkeypatch.setattr(litellm, 'token_counter', lambda **kwargs: 20)
        def completion(**kwargs):
            calls.append(kwargs['model'])
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(content='{"passed": true}'))], _hidden_params={})
        monkeypatch.setattr(litellm, 'completion', completion)
        with running_job(job, workflow.storage):
            router = ModelRouter(settings, workflow.storage)
            first = router.generate(str(state.run_id), CheckResult, 'Check', {'input': 'same'}, model_role='brand')
            second = router.generate(str(state.run_id), CheckResult, 'Check', {'input': 'same'}, model_role='brand')
        assert first == second and len(calls) == 1
        assert len(workflow.storage.list_ai_calls(str(state.run_id))) == 1


def test_pending_paid_call_requires_explicit_recovery(settings, monkeypatch):
    import litellm
    settings.demo_mode = False
    settings.llm_api_key = 'fake-test-key'
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        monkeypatch.setattr(litellm, 'token_counter', lambda **kwargs: 20)
        calls = []
        def interrupted(**kwargs):
            calls.append(1)
            raise litellm.Timeout(message='timeout', model='openai/gpt-4.1-mini', llm_provider='openai')
        monkeypatch.setattr(litellm, 'completion', interrupted)
        with running_job(job, workflow.storage):
            with pytest.raises(UncertainOperation):
                ModelRouter(settings, workflow.storage).generate(str(state.run_id), CheckResult,
                    'Check', {'input': 'same'}, model_role='brand')
            with pytest.raises(UncertainOperation):
                ModelRouter(settings, workflow.storage).generate(str(state.run_id), CheckResult,
                    'Check', {'input': 'same'}, model_role='brand')
        assert calls == [1]


def test_restart_resumes_after_a_saved_graph_step(settings):
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(job, state)
        workflow.graph.invoke({'run': state.model_dump(mode='json')}, workflow._config(str(state.run_id)),
                              interrupt_after=['topic_map_agent'], durability='sync')
        snapshot = workflow.graph.get_state(workflow._config(str(state.run_id)))
        assert snapshot.next == ('briefing_agent',)
        job.status = 'running'
        workflow.storage.save_job(job)
    with Workflow(settings) as reopened:
        reopened.storage.recover_jobs()
        assert reopened.storage.get_job(str(job.id)).status == 'queued'
        JobWorker(settings, reopened).process_one()
        result = reopened.get(str(state.run_id))
        assert result.status == 'seo_check_passed'
        assert result.executed_agents.count('seo_strategy_agent') == 1
        assert result.executed_agents.count('topic_map_agent') == 1


def test_session_expires_and_can_be_revoked(settings):
    with Workflow(settings) as workflow:
        auth = AuthService(workflow.storage,settings.auth_encryption_key.get_secret_value())
        user=auth.store.create_user('sam@example.org','Sam','admin','temporary-password-123')
        secret,_=auth.session(user['id'])
        assert auth.identify(secret)['name']=='Sam'
        auth.logout(secret)
        assert auth.identify(secret) is None
        expired,_=auth.session(user['id'])
        with workflow.storage.connect() as db:
            db.execute('UPDATE auth_sessions SET expires_at=? WHERE digest=?',
                       ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), digest(expired)))
        assert auth.identify(expired) is None


def test_paid_seo_data_and_other_sources_are_reused(settings, monkeypatch):
    settings.demo_mode = False
    calls = {'gsc': 0, 'dataforseo': 0, 'wordpress': 0}
    def gsc(_self, _seed):
        calls['gsc'] += 1
        return [Keyword(keyword='Immobiliensteuer', source='gsc')]
    def paid(_self, _seed):
        calls['dataforseo'] += 1
        return [Keyword(keyword='Immobilienberater', source='dataforseo')]
    def pages(_self):
        calls['wordpress'] += 1
        return [{'url': 'https://example.org/article', 'title': 'Article'}]
    monkeypatch.setattr('src.integrations.google_search_console.GoogleSearchConsole.keywords', gsc)
    monkeypatch.setattr('src.integrations.dataforseo.DataForSEO.keywords', paid)
    monkeypatch.setattr('src.integrations.wordpress.WordPressReadOnly.pages', pages)
    with Workflow(settings) as workflow:
        state = RunState(icp=icp_1_buy_and_hold)
        job = Job(run_id=state.run_id, kind='start', reviewer='Alex', idempotency_key=uuid4())
        with running_job(job, workflow.storage):
            first = Sources(settings).collect('Immobiliensteuer')
            second = Sources(settings).collect('Immobiliensteuer')
        assert first == second
        assert calls == {'gsc': 1, 'dataforseo': 1, 'wordpress': 1}


def test_saved_provider_bytes_survive_master_record_failure(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake-image-key'
    calls = []
    def fake_generate(_self, _prompt, size):
        calls.append(size)
        data = BytesIO()
        Image.new('RGB', size, '#dce8f5').save(data, format='PNG')
        return data.getvalue(), None
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate', fake_generate)
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        from src.services.social import SocialService
        SocialService(settings, workflow.storage).prepare(state)
        job = Job(run_id=state.run_id, kind='image_set', reviewer='Alex', idempotency_key=uuid4())
        workflow.storage.create_job(job)
        worker = JobWorker(settings, workflow)
        save = workflow.storage.save_asset
        failed = [False]
        def fail_master(asset):
            if isinstance(asset, ImageCreative) and not failed[0]:
                failed[0] = True
                raise RuntimeError('database write interrupted')
            return save(asset)
        monkeypatch.setattr(workflow.storage, 'save_asset', fail_master)
        worker.process_one()
        assert workflow.storage.get_job(str(job.id)).status == 'failed'
        monkeypatch.setattr(workflow.storage, 'save_asset', save)
        job = workflow.storage.get_job(str(job.id))
        job.status = 'queued'
        workflow.storage.save_job(job)
        worker.process_one()
        assert workflow.storage.get_job(str(job.id)).status == 'completed'
        assert len(calls) == 1
        with workflow.storage.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM image_attempts').fetchone()[0] == 1


def test_sqlite_job_claims_are_atomic(settings):
    with Workflow(settings) as workflow:
        for _ in range(2):
            state = RunState(icp=icp_1_buy_and_hold)
            workflow.storage.create_job(Job(run_id=state.run_id, kind='start', reviewer='Alex',
                                            idempotency_key=uuid4()), state)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: workflow.storage.claim_job(), range(2)))
        assert all(claimed)
        assert len({job.id for job in claimed}) == 2
        assert workflow.storage.claim_job() is None


@pytest.mark.skipif(not os.environ.get('TEST_POSTGRES_DSN'), reason='Dedicated Postgres test database not configured')
def test_postgres_job_claims_are_atomic():
    pytest.importorskip('psycopg')
    from src.storage.postgres_storage import PostgresStorage
    store = PostgresStorage(os.environ['TEST_POSTGRES_DSN'])
    jobs = [Job(run_id=uuid4(), kind='start', reviewer='Alex', idempotency_key=uuid4()) for _ in range(2)]
    try:
        for job in jobs:
            store.create_job(job)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: store.claim_job(), range(2)))
        assert {job.id for job in claimed} == {job.id for job in jobs}
    finally:
        with store.connect() as db:
            for job in jobs:
                db.execute('DELETE FROM seo_jobs WHERE id=%s', (str(job.id),))
