"""Retained drafts can be corrected explicitly without restarting paid research."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.combined.editorial import structural_errors, rules
from src.combined.pipeline import demo_draft, brand
from src.combined.models import SupportedClaim
from src.orchestrator.workflow import Workflow
from src.schemas.content import CheckResult
from src.schemas.jobs import Job
from src.schemas.review import ReviewEvent
from src.services.jobs import JobWorker
from src.services.recovery import UncertainOperation
from tests.conftest import authenticated_headers, own_fixture
from tests.test_quality_scope import failed_draft
from tests.test_combined import source


@pytest.mark.parametrize('count,valid', [(4, True), (6, True), (8, False)])
def test_contact_tracking_urls_count_without_relaxing_cta_limit(settings, count, valid):
    draft = demo_draft(SimpleNamespace(briefing=SimpleNamespace(
        page_type='landing_page', primary_keyword='Immobiliensteuerberater')), settings)
    draft.modules[0].markdown += '\n' + '\n'.join(
        '[Gespräch](https://steuerkanzlei.example/mandant-werden/?quelle=lp-test#formular)' for _ in range(count))
    errors = structural_errors(draft, 'Immobiliensteuerberater', rules(settings, 'landing_page'))
    assert ('Vier bis sechs Kontakt-CTAs erforderlich.' not in errors) == valid


@pytest.mark.parametrize('style', ['bold', 'heading'])
def test_eight_real_faq_questions_are_recognized_in_supported_markdown(settings, style):
    draft = demo_draft(SimpleNamespace(briefing=SimpleNamespace(
        page_type='landing_page', primary_keyword='Immobiliensteuerberater')), settings)
    faq = next(module for module in draft.modules if module.id == 'L14')
    faq.markdown = '## Häufige Fragen\n\n' + '\n\n'.join(
        (f'**Welche Unterlagen prüfen Sie im Schritt {i}?**' if style == 'bold'
         else f'### Welche Unterlagen prüfen Sie im Schritt {i}?') + '\nSie bereiten Ihre Fragen vor.'
        for i in range(8))
    errors = structural_errors(draft, 'Immobiliensteuerberater', rules(settings, 'landing_page'))
    assert 'L14 benötigt sechs bis acht FAQ-Fragen.' not in errors


def test_evidence_and_link_defects_reach_repair_before_a_paid_qa_request(settings):
    draft = demo_draft(SimpleNamespace(briefing=SimpleNamespace(
        page_type='landing_page', primary_keyword='Immobiliensteuerberater')), settings)
    draft.claims = [SupportedClaim(id='test-claim', text='Dieser Satz steht nicht im Entwurf.',
        source_id='source-1', quote=source().text, tax_year=settings.tax_year,
        conditions='Synthetische Bedingungen im Offline-Test.')]
    draft.modules[0].markdown += '\n[Erfundener Link](https://steuerkanzlei.example/nonexistent-test/)'
    state = SimpleNamespace(editorial=draft.model_dump(mode='json'),
        briefing=SimpleNamespace(primary_keyword='Immobiliensteuerberater'), analysis=None,
        research_packet={'official_sources': [source().model_dump(mode='json')]})
    settings.demo_mode = False
    result = brand(SimpleNamespace(settings=settings), state)
    assert 'Behauptung gehört nicht zum Entwurf: test-claim' in result.brand_check.errors
    assert any('nonexistent-test' in error for error in result.brand_check.errors)


def test_identical_failed_output_stops_another_automatic_paid_rewrite(settings, monkeypatch):
    with Workflow(settings) as workflow:
        state = failed_draft(workflow, monkeypatch)
        state.auto_revisions = 0
        envelope = {'run': state.model_dump(mode='json')}
        first = workflow._repair(envelope)
        assert first['run']['auto_revisions'] == 1
        second = workflow._repair(first)
        assert second['run']['status'] == 'failed'
        assert second['run']['auto_revisions'] == 1
        assert 'denselben Entwurf' in second['run']['error']


def test_explicit_failed_draft_correction_is_owned_revision_bound_and_idempotent(settings, monkeypatch):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        workflow = client.app.state.workflow
        old = failed_draft(workflow, monkeypatch)
        old.auto_revisions = settings.max_auto_revisions
        workflow.graph.update_state(workflow._config(str(old.run_id)), {'run': old.model_dump(mode='json')})
        workflow.storage.save_run_state(old)
        headers = authenticated_headers(client, 'Sam'); own_fixture(client, old)
        payload = {'kind': 'review', 'run_id': str(old.run_id), 'idempotency_key': str(uuid4()),
            'review': {'action': 'needs_revision', 'expected_revision': old.revision,
                       'feedback_target_agent': 'content_agent',
                       'feedback_notes': 'Bitte die Struktur des gespeicherten Entwurfs gezielt korrigieren.'}}
        # Another member cannot inspect or correct this run.
        outsider_headers = authenticated_headers(client, 'Alex')
        assert client.post('/jobs', headers=outsider_headers, json=payload).status_code in {403, 404}
        headers = authenticated_headers(client, 'Sam')
        stale = {**payload, 'review': {**payload['review'], 'expected_revision': old.revision + 1}}
        assert client.post('/jobs', headers=headers, json=stale).status_code == 409
        queued = client.post('/jobs', headers=headers, json=payload)
        assert queued.status_code == 202, queued.text
        assert client.post('/jobs', headers=headers, json=payload).json()['id'] == queued.json()['id']
        monkeypatch.setattr(workflow.agents['seo_strategy_agent'], 'execute', lambda _: pytest.fail('Research must not restart'))
        monkeypatch.setattr(workflow.agents['briefing_agent'], 'execute', lambda _: pytest.fail('Briefing must be retained'))
        assert JobWorker(settings, workflow).process_one()
        result = workflow.get(str(old.run_id))
        assert result.status == 'seo_check_passed'
        assert result.revision == old.revision + 1 and result.human_revisions == 1
        assert result.auto_revisions == old.auto_revisions
        assert result.analysis == old.analysis and result.briefing == old.briefing
        assert not result.professional_review and not result.publication
        assert not (settings.output_dir/'runs'/str(old.run_id)/'approved').exists()
        assert client.post('/jobs', headers=headers, json=payload).json()['id'] == queued.json()['id']


def test_correction_retry_resumes_checkpoint_once_and_preserves_budget_limits(settings, monkeypatch):
    with Workflow(settings) as workflow:
        old = failed_draft(workflow, monkeypatch)
        event = ReviewEvent(action='needs_revision', expected_revision=old.revision,
            feedback_notes='Bitte korrigieren Sie den gespeicherten Entwurf gezielt.',
            reviewer_name='Sam', event_id=str(uuid4()))
        job = Job(run_id=old.run_id, kind='review', reviewer='Sam', idempotency_key=uuid4(),
                  payload={'event': event.model_dump(mode='json')})
        workflow.storage.create_job(job)
        original = workflow.agents['content_agent'].execute
        monkeypatch.setattr(workflow.agents['content_agent'], 'execute',
                            lambda _: (_ for _ in ()).throw(UncertainOperation('Synthetic interruption')))
        worker = JobWorker(settings, workflow)
        assert worker.process_one()
        assert workflow.storage.get_job(str(job.id)).status == 'paused'
        interim = workflow.get(str(old.run_id))
        assert interim.revision == old.revision + 1 and interim.human_revisions == 1
        assert len(interim.review_history) == 1
        monkeypatch.setattr(workflow.agents['content_agent'], 'execute', original)
        workflow.storage.retry_job(str(job.id))
        assert worker.process_one()
        result = workflow.get(str(old.run_id))
        assert result.status == 'seo_check_passed'
        assert result.revision == interim.revision and result.human_revisions == 1
        assert len(result.review_history) == 1 and result.auto_revisions == old.auto_revisions


@pytest.mark.parametrize('blocker', ['paused', 'revision_limit', 'approval'])
def test_failed_draft_recovery_never_bypasses_uncertain_work_limits_or_approval(settings, monkeypatch, blocker):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        workflow = client.app.state.workflow
        old = failed_draft(workflow, monkeypatch)
        headers = authenticated_headers(client, 'Sam'); own_fixture(client, old)
        if blocker == 'paused':
            workflow.storage.create_job(Job(run_id=old.run_id, kind='social', reviewer='Sam',
                reviewer_id=old.owner_user_id, idempotency_key=uuid4(), status='paused'))
        if blocker == 'revision_limit':
            old.human_revisions = settings.max_human_revisions
            workflow.graph.update_state(workflow._config(str(old.run_id)), {'run': old.model_dump(mode='json')})
            workflow.storage.save_run_state(old)
        response = client.post('/jobs', headers=headers, json={
            'kind': 'review', 'run_id': str(old.run_id), 'idempotency_key': str(uuid4()),
            'review': {'action': 'approved' if blocker == 'approval' else 'needs_revision',
                       'expected_revision': old.revision, 'feedback_notes': 'Bitte korrigieren Sie den Entwurf gezielt.'}})
        assert response.status_code == 409
        assert not any(job.status == 'queued' for job in workflow.storage.list_jobs(str(old.run_id)))
