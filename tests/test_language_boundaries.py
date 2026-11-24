import json
import logging
from types import SimpleNamespace
from uuid import uuid4

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from src.api.app import create_app
from tests.conftest import authenticated_headers
from src.orchestrator.workflow import Workflow
from src.schemas.content import CheckResult
from src.schemas.icp import icp_1_buy_and_hold
from src.services.llm import ModelRouter
from src.storage.sqlite_storage import SQLiteStorage


def test_api_errors_are_german_and_validation_contract_stays_stable(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get('/runs').status_code == 401
        headers = authenticated_headers(client)
        assert client.get('/missing-route').json()['detail'] == 'Route nicht gefunden.'
        assert client.put('/runs', headers=headers).json()['detail'] == 'Methode nicht erlaubt.'
        response = client.post('/runs', headers=headers, json={'icp_id': '../invalid'})
        assert response.status_code == 422
        assert response.json()['detail'] == 'Ungültige Zielgruppen-ID'
        response = client.post('/runs/not-a-uuid/reviews', headers=headers, json={})
        assert response.status_code == 422
        errors = response.json()['detail']
        assert any(error['loc'] == ['path', 'run_id'] for error in errors)
        assert all(error['msg'].startswith(('Ungültige Eingabe', 'Pflichtfeld fehlt')) for error in errors)
        # Custom English validator exceptions must also be localized.
        run = client.post('/runs', headers=headers, json={}).json()
        response = client.post(f'/runs/{run["run_id"]}/reviews', headers=headers, json={
            'action': 'needs_revision', 'feedback_notes': 'kurz', 'expected_revision': 0,
        })
        assert response.status_code == 422
        assert 'Provide specific revision notes' not in response.text


def test_unexpected_api_failure_has_german_response_and_english_log(settings, caplog):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = authenticated_headers(client)
        def fail(_, **kwargs):
            raise RuntimeError('Private provider diagnostic must not reach the response')
        app.state.workflow.start = fail
        with caplog.at_level(logging.ERROR):
            response = client.post('/runs', headers=headers, json={'icp_id': 'icp_1_buy_and_hold'})
        assert response.status_code == 500
        assert response.json()['detail'].startswith('Interner Serverfehler.')
        assert 'Private provider diagnostic' not in response.text
        assert 'API request failed (RuntimeError)' in caplog.text


def test_workflow_failure_keeps_german_review_error_and_english_log(settings, caplog):
    with Workflow(settings) as workflow:
        def fail(_):
            raise RuntimeError('Private provider diagnostic')
        workflow.agents['topic_map_agent'].execute = fail
        with caplog.at_level(logging.ERROR):
            state = workflow.start(icp_1_buy_and_hold)
        assert state.status == 'failed'
        assert state.error.startswith('Fehler im Verarbeitungsschritt topic_map_agent.')
        assert 'topic_map_agent failed (RuntimeError)' in caplog.text
        assert 'Private provider diagnostic' not in caplog.text


def test_english_model_instructions_request_german_output_without_changing_json(settings, tmp_path, monkeypatch):
    import litellm
    settings.demo_mode = False
    settings.llm_api_key = 'temporary-test-secret'
    calls = []
    monkeypatch.setattr(litellm, 'token_counter', lambda **kwargs: 50)
    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(total_tokens=100),
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
                content=json.dumps({'passed': False, 'errors': ['Formelle Ansprache fehlt']}),
            ))],
        )
    monkeypatch.setattr(litellm, 'completion', completion)
    store = SQLiteStorage(tmp_path / 'language.sqlite')
    result = ModelRouter(settings, store).generate(str(uuid4()), CheckResult, 'Check the draft.', {'public': 'Öffentlicher Entwurf'})
    prompt = calls[0]['messages'][0]['content']
    assert prompt.startswith('Create public SEO editorial drafts')
    assert 'natural-language values in German' in prompt
    assert 'Keep JSON keys, IDs, enum' in prompt
    assert result.errors == ['Formelle Ansprache fehlt']
    assert result.target == 'content_agent'


def test_german_topic_labels_preserve_export_data_and_review_status(settings):
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
        assert state.status == 'seo_check_passed'
        directory = settings.output_dir / 'runs' / str(state.run_id) / 'revision-000'
        soup = BeautifulSoup((directory / 'topic-maps/topic-map.html').read_text(), 'html.parser')
        assert soup.h1.get_text() == 'Themenplan'
        assert 'Blogbeitrag' in soup.get_text()
        assert 'Unterseite' in soup.get_text()
        exported = json.loads((directory / 'topic-maps/topic-map.json').read_text())
        assert exported == state.topic_map.model_dump(mode='json')
        assert {cluster['kind'] for pillar in exported['pillars'] for cluster in pillar['clusters']} == {'cluster', 'blog'}
