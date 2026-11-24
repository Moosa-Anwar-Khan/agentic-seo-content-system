"""Offline diagnostics and English guidance must not leak or replace German records."""
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.schemas.jobs import Job, view
from src.services.readiness import readiness
from src.services.user_errors import guidance, http_guidance
from tests.conftest import authenticated_headers


def test_demo_readiness_is_offline_and_missing_live_keys_are_not_validated(settings):
    result=readiness(settings)
    assert result['status']=='ready'
    assert result['mode']=='demo'
    assert 'Not performed' in result['live_validation']
    models=[x for x in result['checks'] if x['id'].startswith('model-')]
    assert len(models)==6 and all(x['status']=='warning' for x in models)


def test_live_missing_credentials_produce_actionable_failures_without_secrets(settings):
    settings.demo_mode=False
    settings.deepseek_api_key='secret-must-never-appear'
    result=readiness(settings)
    assert result['status']=='needs_attention'
    assert {'gsc','dataforseo','tavily'} <= {c['id'] for c in result['checks'] if c['status']=='fail'}
    assert 'secret-must-never-appear' not in json.dumps(result)
    assert all(c['message'] for c in result['checks'])


def test_readiness_checks_dates_and_writable_paths_without_sending_requests(settings, tmp_path):
    settings.demo_mode=False
    settings.openai_api_key='fake'
    settings.research_price_rates=json.dumps({'openai/gpt-5.4-mini':{'input':.75,'output':4.5,'verified_at':'2000-01-01'}})
    blocker=tmp_path/'file';blocker.write_text('not a directory')
    settings.output_dir=blocker
    result=readiness(settings)
    assert all(c['status']=='fail' for c in result['checks'] if c['id'].startswith('model-'))
    assert next(c for c in result['checks'] if c['id']=='exports')['status']=='fail'


def test_diagnostic_endpoint_requires_authentication_and_does_not_change_budget(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get('/system/readiness').status_code==401
        headers=authenticated_headers(client)
        before=client.get('/budget',headers=headers).json()
        response=client.get('/system/readiness',headers=headers)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['label']=='Ready for local use'
        assert client.get('/budget',headers=headers).json()==before
        assert client.get('/jobs',headers=headers).json()==[]


def test_english_guidance_keeps_legacy_api_contract_and_validation(settings):
    with TestClient(create_app(settings)) as client:
        response=client.get('/runs')
        assert response.status_code==401
        assert response.json()['detail']=='Bitte anmelden oder Seite neu laden.'
        assert response.json()['user_error']['title']=='Please sign in again'
        headers=authenticated_headers(client)
        response=client.post('/runs',headers=headers,json={'icp_id':'../invalid'})
        assert response.status_code==422
        assert response.json()['user_error']['code']=='validation'
        assert response.json()['user_error']['next_steps']


def test_error_catalog_and_job_view_do_not_echo_private_provider_text():
    job=Job(run_id=uuid4(),kind='image_set',idempotency_key=uuid4(),reviewer='Alex',
            error='private provider response and credentials',error_code='authentication',status='failed')
    result=view(job)
    assert result.error==job.error  # compatibility; the portal uses error_info instead
    assert 'private provider' not in json.dumps(result.error_info)
    assert result.error_info['code']=='authentication'
    assert result.error_info['reference']==str(job.id)
    assert guidance('unknown-private-error')['code']=='processing'
    assert guidance('uncertain_outcome')['retry']=='inspect_first'
    assert guidance('rate_limit')['retry']=='may_charge'
    assert http_guidance(502,'Bildgenerierung fehlgeschlagen')['code']=='image_generation'


def test_check_command_sanitizes_configuration_errors(monkeypatch,capsys):
    import run
    from src.config.settings import Settings
    def invalid(*args,**kwargs):raise ValueError('private-key-do-not-print')
    monkeypatch.setattr('src.config.settings.Settings',invalid)
    monkeypatch.setattr('sys.argv',['run.py','check'])
    with pytest.raises(SystemExit) as result:run.main()
    assert result.value.code==1
    output=capsys.readouterr().out
    assert 'private-key-do-not-print' not in output
    assert json.loads(output)['status']=='needs_attention'


def test_every_worker_error_has_english_guidance_and_unknown_paid_jobs_warn():
    from src.services.jobs import ERROR_MESSAGES
    from src.services.user_errors import CATALOG
    assert set(ERROR_MESSAGES)<=set(CATALOG)
    job=Job(run_id=uuid4(),kind='image_set',idempotency_key=uuid4(),reviewer='Alex',error='saved diagnostic',error_code='processing',status='failed')
    assert view(job).error_info['retry']=='may_charge'
    assert job.requires_paid_retry is False


def test_demo_console_is_english_and_saved_german_findings_remain_unchanged(monkeypatch, tmp_path, capsys):
    import run
    from src.config.settings import Settings
    def demo_settings(**kwargs):
        kwargs.update(data_dir=tmp_path/'var/demo', output_dir=tmp_path/'outputs/demo')
        return Settings(**kwargs)
    monkeypatch.setattr('src.config.settings.Settings', demo_settings)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['run.py', 'demo'])
    run.main()
    output=capsys.readouterr().out
    summary=json.loads(output)
    assert summary['api_charges']==0 and summary['release_blockers']
    assert 'Demo content cannot receive live approval' in ' '.join(summary['release_blockers'])
    assert 'Hauptkeyword' not in output and '\\u00' not in output
    reports=sorted((tmp_path/'outputs/demo/runs'/summary['run_id']).glob('revision-*/reviews/state.json'))
    assert reports
    saved=json.loads(reports[-1].read_text())
    assert 'Amtliche Quellen fehlen.' in saved['release_blockers']
    assert 'Sie' in saved['html']
    assert saved['release_blockers'] != summary['release_blockers']


def test_unknown_console_findings_use_safe_english_guidance():
    from src.services.user_errors import console_release_blockers
    original=['Unbekannter Befund mit vertraulichem Inhalt']
    result=console_release_blockers(original)
    assert result==['A content requirement needs review. Inspect the original finding in the saved German review report.']
    assert original==['Unbekannter Befund mit vertraulichem Inhalt']
