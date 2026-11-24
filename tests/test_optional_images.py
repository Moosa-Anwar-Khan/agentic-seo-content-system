"""Offline coverage for explicit image generation and its configuration boundary."""
import base64
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.orchestrator.workflow import Workflow
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.services.images import OpenAIImageProvider, ImageModelRouter, image_operation_key
from src.services.recovery import running_job
from src.services.social import SocialService
from src.storage.sqlite_storage import BudgetExceeded
from tests.conftest import authenticated_headers, own_fixture


@pytest.mark.parametrize('quality,size,expected', [
    (None, (2048, 2048), 'high'), (None, (1024, 1024), 'medium'),
    ('medium', (2048, 2048), 'medium'), ('low', (2048, 2048), 'low'),
])
def test_openai_quality_and_master_request(settings, monkeypatch, quality, size, expected):
    settings.image_quality = quality
    requests = []
    real_client = httpx.Client
    def handle(request):
        assert str(request.url) == 'https://api.openai.com/v1/images/generations'
        assert request.headers['authorization'] == 'Bearer fake-openai'
        payload = json.loads(request.content)
        assert payload['model'] == 'gpt-image-2.5-flare'
        assert payload['quality'] == expected
        assert payload['size'] == f'{size[0]}x{size[1]}'
        assert payload['n'] == 1 and payload['output_format'] == 'png'
        requests.append(payload)
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(b'fake-image').decode()}]})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert OpenAIImageProvider(settings, key='fake-openai').generate('test', size) == (b'fake-image', None)
    assert len(requests) == 1


def test_options_auth_configuration_and_secret_redaction(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get('/image-generation/options').status_code == 401
        headers = authenticated_headers(client)
        options = client.get('/image-generation/options', headers=headers).json()
        assert options['enabled'] is False and options['configured'] is False
        settings.image_generation_enabled = True
        settings.openai_api_key = 'fake-secret-never-return'
        settings.image_generation_provider = 'openai'
        settings.image_generation_model = 'gpt-image-2.5-flare'
        settings.image_request_max_usd = .25
        response = client.get('/image-generation/options', headers=headers)
        assert response.json() == {'enabled': True, 'configured': True, 'demo_mode': True,
                                   'request_max_usd': .25, 'reason': None}
        assert 'fake-secret' not in response.text and 'gpt-image' not in response.text
        settings.combined_mode = True
        settings.demo_mode = False
        settings.image_request_max_usd = None
        assert client.get('/image-generation/options', headers=headers).json()['configured'] is False


def test_queue_rejects_missing_configuration_and_sources(settings, monkeypatch):
    settings.image_generation_enabled = True
    settings.image_api_key = 'fake'
    with TestClient(create_app(settings)) as client:
        headers = authenticated_headers(client)
        workflow = client.app.state.workflow
        state = own_fixture(client, workflow.start(icp_1_buy_and_hold))
        payload = {'kind': 'image_set', 'run_id': str(state.run_id), 'idempotency_key': str(uuid4())}
        response = client.post('/jobs', headers=headers, json=payload)
        assert response.status_code == 409 and 'Social-Media' in response.json()['detail']
        SocialService(settings, workflow.storage).prepare(state)
        settings.image_api_key = ''
        response = client.post('/jobs', headers=headers, json=payload)
        assert response.status_code == 409 and 'eingerichtet' in response.json()['detail']
        assert workflow.storage.list_jobs() == []
        # In live combined mode, even prepared social assets cannot bypass export.
        settings.image_api_key = 'fake'
        settings.image_request_max_usd = .25
        settings.combined_mode = True
        settings.demo_mode = False
        response = client.post('/jobs', headers=headers, json=payload)
        assert response.status_code == 409 and 'exportieren' in response.json()['detail']
        assert workflow.storage.list_jobs() == []


def test_quality_changes_durable_request_identity(settings):
    with Workflow(settings) as workflow:
        storage = workflow.storage
        job = Job(run_id=uuid4(), kind='image_set', idempotency_key=uuid4(), reviewer='Alex')
        with running_job(job, storage):
            args = ('openai', 'gpt-image-2.5-flare', False, 'same prompt', (2048, 2048))
            legacy = image_operation_key(settings, *args)
            settings.image_quality = 'medium'
            medium = image_operation_key(settings, *args)
            settings.image_quality = 'high'
            high = image_operation_key(settings, *args)
            assert len({legacy, medium, high}) == 3


def test_insufficient_image_budget_never_calls_provider(settings):
    settings.combined_mode = True
    settings.demo_mode = False
    settings.image_request_max_usd = .25
    settings.max_run_usd = .1
    class NeverCalled:
        def generate(self, prompt, size):
            pytest.fail('An over-budget image request reached the provider')
    with Workflow(settings) as workflow:
        storage = workflow.storage
        with pytest.raises(BudgetExceeded):
            ImageModelRouter(settings, storage, NeverCalled()).generate(str(uuid4()), 'test', (2048, 2048))
