"""Quality reviews stay grounded and explicit recovery never rewrites retained drafts."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.agents.base import AgentContext
from src.agents.brand_agent import BrandAgent
from src.orchestrator.workflow import Workflow
from src.schemas.content import CheckResult
from src.schemas.decisions import SEORecommendation, PageEvidence
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.jobs import Job
from src.services.jobs import JobWorker
from src.services.quality import QualityReviewError, validate_brand_findings
from src.services.recovery import UncertainOperation
from tests.conftest import authenticated_headers, own_fixture


@pytest.mark.parametrize('result', [
    CheckResult(passed=False, errors=['Unbelegtes Versprechen: "Research-only phrase".']),
    CheckResult(passed=False, errors=['Es wurde kein konkreter Beleg genannt.']),
    CheckResult(passed=False, errors=[]),
    CheckResult(passed=True, errors=['Widersprüchlicher Befund.']),
])
def test_ungrounded_or_contradictory_findings_stop_before_rewrites(result):
    with pytest.raises(QualityReviewError):
        validate_brand_findings(result, 'Sie erhalten einen allgemeinen Überblick.')


def test_supported_brand_finding_still_blocks_and_normalizes_whitespace():
    result = CheckResult(passed=False, errors=['Unbelegte Zusage: „Sie sparen immer 30 Prozent“.'])
    validate_brand_findings(result, 'Sie sparen immer\n30 Prozent. Weitere Informationen folgen.')


def test_brand_reviews_current_draft_without_existing_page_or_briefing_context(settings):
    with Workflow(settings) as workflow:
        state = workflow.start(icp_1_buy_and_hold)
    page = PageEvidence(id='page-source',url=settings.wordpress_base_url+'/existing/',title='Vorhandene Seite',
                        content='RESEARCH_ONLY_PHRASE')
    state.analysis.page_evidence = [page]
    state.selected_recommendation = SEORecommendation(id='scope-test',topic_id=state.topic_map.seed_id,
        title=state.article.title,keyword=state.briefing.primary_keyword,search_intent='commercial',
        page_type='landing_page',action='optimize',target_url=page.url,evidence_refs=[page.id],reasoning='Scope regression')
    state.briefing.editorial_notes.append('BRIEFING_ONLY_PHRASE')
    captured = []
    class Router:
        def generate(self, run_id, schema, prompt, payload, **kwargs):
            captured.append(payload)
            return CheckResult(passed=True)
    settings.demo_mode = False
    BrandAgent(AgentContext(settings, None, Router(), None)).execute(state)
    sent = json.dumps(captured[0])
    assert 'BRIEFING_ONLY_PHRASE' not in sent
    assert 'RESEARCH_ONLY_PHRASE' not in sent
    assert 'selected_context' not in captured[0] and 'briefing' not in captured[0]
    assert state.article.introduction[0] in ' '.join(captured[0]['current_public_draft'])


def failed_draft(workflow, monkeypatch):
    """Retain an actual generated fixture draft, with a genuine quality-check failure."""
    original = workflow.agents['brand_agent'].execute
    def reject(state):
        state.brand_check = CheckResult(passed=False, errors=['Ein konkreter Prüfschritt ist offen.'])
        return state
    workflow.settings.max_auto_revisions = 0
    monkeypatch.setattr(workflow.agents['brand_agent'], 'execute', reject)
    state = workflow.start(icp_1_buy_and_hold)
    assert state.status == 'failed'
    monkeypatch.setattr(workflow.agents['brand_agent'], 'execute', original)
    workflow.settings.max_auto_revisions = 2
    return state


def test_check_only_recovery_preserves_draft_and_requires_separate_approval(settings, monkeypatch):
    with Workflow(settings) as workflow:
        old = failed_draft(workflow, monkeypatch)
        before = {k:old.model_dump(mode='json')[k] for k in ['article','briefing','analysis','revision','auto_revisions']}
        monkeypatch.setattr(workflow.agents['content_agent'], 'execute', lambda _: pytest.fail('No new draft permitted'))
        monkeypatch.setattr(workflow.context.sources, 'collect', lambda _: pytest.fail('Research must be reused'))
        result = workflow.quality_checks_queued(str(old.run_id), old.revision)
        assert result.status == 'seo_check_passed'
        assert {k:result.model_dump(mode='json')[k] for k in before} == before
        assert result.review_history == [] and 'publisher_agent' not in result.executed_agents
        assert workflow.graph.get_state(workflow._config(str(old.run_id))).next == ('human_review',)
        assert (settings.output_dir/'runs'/str(old.run_id)/f'revision-{old.revision:03d}'/'reviews/quality-checks.json').is_file()


def test_failed_recheck_never_triggers_another_article_even_when_repair_allowance_remains(settings, monkeypatch):
    with Workflow(settings) as workflow:
        old = failed_draft(workflow, monkeypatch)
        before = old.article.model_dump()
        def reject(state):
            state.brand_check = CheckResult(passed=False, errors=['Weiterhin nicht freigegeben.'])
            return state
        monkeypatch.setattr(workflow.agents['brand_agent'], 'execute', reject)
        monkeypatch.setattr(workflow.agents['content_agent'], 'execute', lambda _: pytest.fail('No paid rewrite permitted'))
        result = workflow.quality_checks_queued(str(old.run_id), old.revision)
        assert result.status == 'failed' and result.auto_revisions == old.auto_revisions
        assert result.article.model_dump() == before and result.seo_check is None


def test_recheck_job_restart_resumes_only_remaining_check(settings, monkeypatch):
    with Workflow(settings) as workflow:
        old = failed_draft(workflow, monkeypatch)
        job = Job(run_id=old.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4(),
                  payload={'source_revision':old.revision})
        workflow.storage.create_job(job)
        def interrupted(_): raise UncertainOperation('simulated response loss')
        monkeypatch.setattr(workflow.agents['seo_qa_agent'],'execute',interrupted)
        assert JobWorker(settings,workflow).process_one()
        assert workflow.storage.get_job(str(job.id)).status == 'paused'
        assert workflow.graph.get_state(workflow._config(str(old.run_id))).next == ('seo_qa_agent',)
    with Workflow(settings) as resumed:
        monkeypatch.setattr(resumed.agents['brand_agent'],'execute',lambda _:pytest.fail('Completed brand check must survive restart'))
        monkeypatch.setattr(resumed.agents['content_agent'],'execute',lambda _:pytest.fail('No new draft permitted'))
        resumed.storage.retry_job(str(job.id))
        assert JobWorker(settings,resumed).process_one()
        result = resumed.get(str(old.run_id))
        assert result.status == 'seo_check_passed' and result.article.model_dump() == old.article.model_dump()


def test_api_recheck_is_authenticated_revision_bound_and_idempotent(settings, monkeypatch):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        workflow = client.app.state.workflow
        old = failed_draft(workflow, monkeypatch)
        payload={'kind':'quality_checks','run_id':str(old.run_id),'expected_revision':old.revision,'idempotency_key':str(uuid4())}
        assert client.post('/jobs',json=payload).status_code in {401,403}
        headers=authenticated_headers(client,'Sam');own_fixture(client,old)
        stale={**payload,'expected_revision':old.revision+1}
        assert client.post('/jobs',headers=headers,json=stale).status_code==409
        response=client.post('/jobs',headers=headers,json=payload)
        assert response.status_code==202,response.text
        assert client.post('/jobs',headers=headers,json=payload).json()['id']==response.json()['id']
        assert workflow.storage.get_job(response.json()['id']).reviewer_id is not None
        assert client.app.state.worker.process_one()
        assert workflow.get(str(old.run_id)).status=='seo_check_passed'
        # Reusing an idempotency key after completion returns that job, not another paid request.
        assert client.post('/jobs',headers=headers,json=payload).json()['id']==response.json()['id']


def test_api_recheck_blocks_uncertain_requests(settings,monkeypatch):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        workflow=client.app.state.workflow
        old=failed_draft(workflow,monkeypatch)
        workflow.storage.create_job(Job(run_id=old.run_id,kind='selection',reviewer='Sam',
            idempotency_key=uuid4(),status='paused'))
        headers=authenticated_headers(client,'Sam');own_fixture(client,old)
        response=client.post('/jobs',headers=headers,json={'kind':'quality_checks','run_id':str(old.run_id),
            'expected_revision':old.revision,'idempotency_key':str(uuid4())})
        assert response.status_code==409
