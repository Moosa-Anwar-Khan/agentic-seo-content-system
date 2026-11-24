"""Invalid semantic findings cannot discard paid measurements or unlock drafts."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.schemas.decisions import AssessmentBatch, PageAssessment, PageEvidence
from src.services.dashboard import dashboard
from src.services.seo_decisions import DecisionService, topics
from src.storage.sqlite_storage import SQLiteStorage
from tests.test_verified_research import make_state


def research_case(settings):
    state = make_state(settings)
    state.research_snapshot = {'version': 2, 'dataforseo': {'status': 'usable'}}
    url = 'https://steuerkanzlei.example/property/'
    state.analysis.page_evidence = [PageEvidence(id='page-0', url=url,
        title='Immobiliensteuerberater', content='Wir begleiten private Vermieter.')]
    state.analysis.existing_pages = [url]
    planned = topics(state)
    valid = PageAssessment(topic_id=planned[0]['id'], page_url=url,
        relation='dedicated', evidence_quote='Wir begleiten private Vermieter.',
        reasoning='Vorhandene Leistungsseite mit passendem Leserbezug.')
    return state, planned, valid


def test_quarantine_preserves_valid_findings_and_raw_reply_blocks_rejected_topics(settings):
    state, planned, valid = research_case(settings)
    invalid_quote = valid.model_copy(update={'topic_id': planned[1]['id'], 'evidence_quote': 'Invented quotation'})
    invalid_none = valid.model_copy(update={'topic_id': planned[2]['id'], 'relation': 'none', 'evidence_quote': ''})
    invented_url = valid.model_copy(update={'topic_id': planned[3]['id'], 'page_url': 'https://unknown.example/'})
    invented_metrics = valid.model_copy(update={'topic_id': planned[4]['id'], 'reasoning': 'Suchvolumen 10000.'})
    batch = AssessmentBatch(assessments=[valid, invalid_quote, invalid_none, invented_url, invented_metrics])
    original = deepcopy(batch.model_dump())
    original_plan = deepcopy(state.topic_map.model_dump())
    service = DecisionService(SimpleNamespace(settings=settings))
    result = service.build(state, batch, quarantine_invalid=True)
    assert batch.model_dump() == original
    assert state.analysis.page_assessments == [valid]
    audit = state.research_snapshot['assessment_validation']
    assert audit['accepted_count'] == 1 and audit['rejected_count'] == 4
    assert audit['status'] == 'partial'
    assert len(result) == len(planned) and 6 <= len(state.topic_map.pillars) <= 8
    assert state.topic_map.model_dump() == original_plan
    for recommendation in result:
        if recommendation.topic_id in audit['blocked_topic_ids']:
            assert not recommendation.drafting_eligible
            assert recommendation.research_needed and recommendation.readiness_blockers
    assert all('Invented quotation' not in a.evidence_quote for a in state.analysis.page_assessments)
    assert any('nicht übernommen' in warning for warning in state.analysis.limitations)


def test_default_validator_stays_strict_and_duplicate_also_blocks_valid_same_topic(settings):
    state, planned, valid = research_case(settings)
    invalid = valid.model_copy(update={'evidence_quote': 'Invented quotation'})
    service = DecisionService(SimpleNamespace(settings=settings))
    with pytest.raises(ValueError, match='saved page evidence'):
        service.build(state, AssessmentBatch(assessments=[invalid]))
    result = service.build(state, AssessmentBatch(assessments=[valid, valid]), quarantine_invalid=True)
    assert state.research_snapshot['assessment_validation']['rejected_count'] == 1
    recommendation = next(r for r in result if r.topic_id == planned[0]['id'])
    assert not recommendation.drafting_eligible and recommendation.research_needed


def test_complete_good_batch_is_verified_and_rejection_cannot_be_hidden_by_scope(settings):
    state, planned, valid = research_case(settings)
    batch = AssessmentBatch(assessments=[valid, *[
        PageAssessment(topic_id=t['id'], relation='none', reasoning='Kein belegter Treffer im gelieferten Ausschnitt.')
        for t in planned[1:]]])
    service = DecisionService(SimpleNamespace(settings=settings))
    state.analysis.recommendations = service.build(state, batch, quarantine_invalid=True)
    assert state.research_snapshot['assessment_validation']['status'] == 'verified'
    assert not state.research_snapshot['assessment_validation']['rejected']
    state.research_snapshot.update(
        scope_coverage={'icp_1': {'complete': True}}, report_readiness='ready',
        performance={'icp_1_de': {p: {'summary': {'available': True, 'rows': [
            {'clicks': 0, 'impressions': 100, 'position': 44.6}]}} for p in ['current', 'previous']}})
    storage = SQLiteStorage(settings.data_dir/'report.sqlite')
    assert dashboard(state, storage)['report_readiness'] == 'ready'
    state.research_snapshot['assessment_validation']['status'] = 'partial'
    report = dashboard(state, storage)
    assert report['report_readiness'] == 'partial'
    assert report['sources']['assessment_validation']['status'] == 'partial'
    assert report['current']['clicks'] == 0 and report['current']['impressions'] == 100


def test_absence_claim_is_downgraded_without_changing_paid_model_reply(settings):
    state, _, valid = research_case(settings)
    valid.issue = 'Die Seite ist ohne eine nachvollziehbare Erklärung.'
    valid.distinct_need = 'Neue Seite wegen angeblicher Abwesenheit.'
    batch = AssessmentBatch(assessments=[valid])
    original = deepcopy(batch.model_dump())
    result = DecisionService(SimpleNamespace(settings=settings)).build(state, batch, quarantine_invalid=True)
    assert batch.model_dump() == original
    assert state.analysis.page_assessments[0].relation == 'uncertain'
    assert not state.analysis.page_assessments[0].issue
    assert not state.analysis.page_assessments[0].distinct_need
    assert all(not r.drafting_eligible for r in result)
