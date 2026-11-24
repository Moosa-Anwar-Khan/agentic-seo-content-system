"""Replay saved public revision-four responses; all provider work is fake/offline."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.orchestrator.workflow import Workflow
from src.schemas.decisions import PageEvidence
from src.services.qa_audit import audit
from src.services.qa_units import QAUnitResult, plan, validate_v1 as validate_unit, review
from src.services.quality import QualityReviewError
from tests.test_pipeline_stabilization import prepared, passing
from scripts.correct_revision_five import corrected, SEVDESK

FIXTURE=json.loads((Path(__file__).parent/'fixtures/revision_four_qa.json').read_text())


@pytest.fixture(autouse=True)
def combined(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True


def saved_state(w,settings):
    state=prepared(w,settings)
    state.editorial=deepcopy(FIXTURE['editorial'])
    state.analysis.page_evidence=[PageEvidence.model_validate(p) for p in FIXTURE['pages']]
    state.icp.approved_fact_sources=[p.url for p in state.analysis.page_evidence if p.id=='page-9']
    state.briefing.primary_keyword=FIXTURE['primary_keyword']
    state.briefing.search_intent=FIXTURE['search_intent']
    state.seo_check=None
    return state


def finding(module,quote,rule='unsupported_business',**extra):
    return dict(module_id=module,quote=quote,rule=rule,problem='Belegter Prüfbefund im Entwurf.',
        correction='Bitte prüfen Sie diese konkrete Aussage.',message_en='A specific assertion needs correction.',
        action_en='Correct the quoted assertion using approved evidence.',**extra)


def result(unit,item):
    return QAUnitResult(unit_id=unit['id'],coverage_complete=True,passed=False,findings=[item])


def test_every_saved_revision_four_unit_is_replayed_without_false_pass(settings):
    with Workflow(settings) as w:
        state=saved_state(w,settings);units={u['id']:u for u in plan(state,settings)[1]}
        rejected={};valid=[]
        for raw in FIXTURE['responses']:
            r=QAUnitResult.model_validate(raw)
            try:validate_unit(state,units[r.unit_id],r)
            except QualityReviewError as e:rejected[r.unit_id]=e.reason
            else:valid.append(r.unit_id)
            assert r.passed==raw['passed'] # Invalid responses are never rewritten as passes.
        assert len(FIXTURE['responses'])==15
        assert rejected=={'page':'review_scope_conflict','module:L14':'missing_link_source_quote'}
        assert len(valid)==13
        assert state.seo_check is None


@pytest.mark.parametrize('quote',['- **21 Tage Wechsel**','- **48 h Rückmeldung**','- **1 fester Ansprechpartner**'])
def test_approved_short_form_rejection_is_invalid_review(settings,quote):
    with Workflow(settings) as w:
        state=saved_state(w,settings);u=next(u for u in plan(state,settings)[1] if u['id']=='module:L10')
        with pytest.raises(QualityReviewError,match='grounded') as e:
            validate_unit(state,u,result(u,finding('L10',quote)))
        assert e.value.reason=='approved_wording_rejected'


def test_ambiguous_experience_and_conflicting_context_are_still_defects(settings):
    with Workflow(settings) as w:
        state=saved_state(w,settings)
        u=next(u for u in plan(state,settings)[1] if u['id']=='module:L10')
        ambiguous='Wir verfügen über 12 Jahre Erfahrung in der digitalen Steuerberatung.'
        assert not validate_unit(state,u,result(u,finding('L10',ambiguous))).passed
        m=next(m for m in state.editorial['modules'] if m['id']=='L10')
        m['markdown']+='\nEine Rückmeldung innerhalb von 48 Stunden wird nicht zugesagt.'
        u=next(u for u in plan(state,settings)[1] if u['id']=='module:L10')
        assert not validate_unit(state,u,result(u,finding('L10','Eine Rückmeldung innerhalb von 48 Stunden wird nicht zugesagt.','contradiction'))).passed


def test_two_links_are_counted_separately_duplicates_cannot_fill_quota(settings):
    from src.combined.editorial import link_errors
    with Workflow(settings) as w:
        state=saved_state(w,settings);a=audit(state,settings,['L14'])
        assert a['link_counts'][0]['unique_verified_destinations']==2
        assert not a['link_errors']
        module=next(m for m in state.editorial['modules'] if m['id']=='L14')
        module['markdown']=module['markdown'].replace('https://steuerkanzlei.example/buchhaltungskosten/','https://steuerkanzlei.example/lexware-office-zusammenarbeit/')
        assert audit(state,settings,['L14'])['link_counts'][0]['unique_verified_destinations']==1
        assert any('Zwei bis vier' in error for error in link_errors(state,settings))
        u=next(u for u in plan(state,settings)[1] if u['id']=='module:L14')
        with pytest.raises(QualityReviewError) as e:
            validate_unit(state,u,result(u,finding('L14','Häufig gestellte Fragen','link_count')))
        assert e.value.reason=='deterministic_review_conflict'


def test_relevance_requires_literal_original_source_and_preserves_real_defect(settings):
    with Workflow(settings) as w:
        state=saved_state(w,settings);u=next(u for u in plan(state,settings)[1] if u['id']=='module:L14')
        item=finding('L14','Überblick zu Buchhaltungskosten','links',evidence_refs=['page-51'])
        with pytest.raises(QualityReviewError) as e:validate_unit(state,u,result(u,item))
        assert e.value.reason=='missing_link_source_quote'
        item['source_quotes']=[{'evidence_ref':'page-51','quote':'Invented target text that was never saved.'}]
        with pytest.raises(QualityReviewError) as e:validate_unit(state,u,result(u,item))
        assert e.value.reason=='invalid_link_source_quote'
        source=next(p for p in u['request']['verified_link_targets'] if p['id']=='page-51')
        text=''.join(source['ordered_text_parts']);at=text.index('500')
        item['source_quotes'][0]['quote']=text[max(0,at-80):at+120]
        accepted=validate_unit(state,u,result(u,item))
        assert not accepted.passed # A source-supported relevance defect still blocks.
        # Another source or a title cannot substantiate this target quotation.
        item['source_quotes'][0]['evidence_ref']='page-62'
        with pytest.raises(QualityReviewError):validate_unit(state,u,result(u,item))


def test_page_keeps_cross_section_consistency_and_rejects_module_judgments(settings):
    with Workflow(settings) as w:
        state=saved_state(w,settings);page=plan(state,settings)[1][0]
        item=finding('L14','Eine feste Wechseldauer oder eine bestimmte Form der Datenübertragung wird hier nicht zugesagt.','contradiction')
        assert not validate_unit(state,page,result(page,item)).passed
        assert len(page['request']['article_modules'])==14
        assert 'cross-section' in page['request']['semantic_responsibilities']
        item['rule']='unsupported_business'
        with pytest.raises(QualityReviewError) as e:validate_unit(state,page,result(page,item))
        assert e.value.reason=='review_scope_conflict'


def test_local_correction_preserves_pending_inputs_and_scoped_cache(settings,monkeypatch):
    from src.services.progress import progress
    from src.services.export import LocalExporter
    with Workflow(settings) as w:
        state=saved_state(w,settings);before=deepcopy(state.editorial);calls=[]
        monkeypatch.setattr(w.context.llm,'generate',passing(calls));assert review(w.context,state).passed
        old={u['id']:u['dependency'] for u in plan(state,settings)[1]}
        state.editorial=corrected(state.editorial);state.revision+=1;state.seo_check=None
        new={u['id']:u['dependency'] for u in plan(state,settings)[1]}
        assert [key for key in old if old[key]!=new[key]]==['page','module:L07','module:L10','module:L14']
        by_id={m['id']:m['markdown'] for m in state.editorial['modules']}
        assert SEVDESK in by_id['L14'] and '/buchhaltungskosten/' not in by_id['L14']
        assert 'innerhalb von 48 Stunden' in by_id['L10'] and 'vollständig digital' in by_id['L10']
        matches=audit(state,settings,['L10'])['approved_matches']
        assert {m['fact_id'] for m in matches} >= {'brand-fact:digital_experience','brand-fact:response_time','brand-fact:switching'}
        assert '[FREIGEGEBENES STATEMENT FEHLT]' in by_id['L08'] and '[PREIS FEHLT]' in by_id['L14']
        assert state.editorial['claims']==before['claims']
        assert state.editorial['unresolved_questions']==before['unresolved_questions']
        assert progress(w,state)['review_plan']['remaining']==4
        assert state.professional_review is None
        LocalExporter(settings.output_dir,settings).write_bundle(state,settings.output_dir/'audit')
        assert json.loads((settings.output_dir/'audit/reviews/deterministic-audit.json').read_text())['version']=='qa-responsibilities-v1'
        assert state.seo_check is None


@pytest.mark.parametrize('reason,phrase',[
    ('approved_wording_rejected','exact configured approved'),
    ('review_scope_conflict','outside its assigned scope'),
    ('deterministic_review_conflict','reserved for deterministic checks'),
    ('missing_link_source_quote','no quotation'),
    ('invalid_link_source_quote','does not match')])
def test_specific_invalid_review_diagnostic_is_saved_without_charges(settings,reason,phrase):
    from src.services.failures import capture
    with Workflow(settings) as w:
        state=saved_state(w,settings);before=w.storage.research_costs(str(state.run_id))
        event=capture(w.storage,code='quality_evidence',exc=QualityReviewError(reason),state=state,
            component='seo_qa',operation='Review article unit')
        assert phrase in event['summary']['reason']
        assert event['summary']['category']=='invalid_review'
        assert event['technical']['review_reason']==reason
        assert event['technical']['validation_fields']
        assert w.storage.research_costs(str(state.run_id))==before
        assert w.storage.list_run_failures(str(state.run_id))[0]['id']==event['id']


@pytest.mark.parametrize('blank_field',['quote','source_quote'])
def test_whitespace_cannot_substitute_for_draft_or_source_proof(settings,blank_field):
    with Workflow(settings) as w:
        state=saved_state(w,settings);u=next(u for u in plan(state,settings)[1] if u['id']=='module:L14')
        item=finding('L14','Überblick zu Buchhaltungskosten','links',evidence_refs=['page-51'],
            source_quotes=[{'evidence_ref':'page-51','quote':'   '}])
        if blank_field=='quote':item['quote']='   '
        with pytest.raises(QualityReviewError):validate_unit(state,u,result(u,item))
