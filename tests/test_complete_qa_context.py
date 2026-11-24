"""Complete evidence, scoped caches and pending inputs; fake providers only."""
from copy import deepcopy
import hashlib
import json

import pytest

from src.combined.editorial import rules
from src.orchestrator.workflow import Workflow
from src.schemas.decisions import PageEvidence
from src.services.export import LocalExporter
from src.services.progress import progress
from src.services.qa_units import QAUnitResult, plan, review, validate_v1 as validate_unit
from src.services.quality import QualityReviewError
from tests.test_pipeline_stabilization import prepared, passing


@pytest.fixture(autouse=True)
def combined_settings(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True


def target_page(state,settings,text):
    page=PageEvidence(id='lexware-proof',url=settings.wordpress_base_url+'/lexware-proof/',
        title='Find a synthetic tax adviser',content='Shortened preview only.',content_truncated=True,
        sections=[{'text':text}],content_hash=hashlib.sha256(text.encode()).hexdigest())
    state.analysis.page_evidence.append(page)
    next(m for m in state.editorial['modules'] if m['id']=='L10')['markdown']+='\n[Digitale Zusammenarbeit]('+page.url+')'
    return page


def test_link_review_receives_the_entire_original_beyond_the_preview(settings):
    with Workflow(settings) as w:
        state=prepared(w,settings)
        text='Synthetischer Quelltext. '*600+' Lexware Office, früher Lexoffice: Wir arbeiten digital zusammen.'
        target_page(state,settings,text)
        _,units=plan(state,settings)
        assert 'verified_link_targets' not in units[0]['request']
        assert units[0]['request']['article_modules']
        for unit in [next(u for u in units if u['id']=='module:L10')]:
            evidence=next(p for p in unit['request']['verified_link_targets'] if p['id']=='lexware-proof')
            assert ''.join(evidence['ordered_text_parts'])==text
            assert not evidence['partial'] and len(evidence['ordered_text_parts'])>1
            assert 'Lexware Office' in evidence['ordered_text_parts'][-1]


def test_changed_link_source_is_rejected_before_any_request(settings,monkeypatch):
    with Workflow(settings) as w:
        state=prepared(w,settings);page=target_page(state,settings,'Originale synthetische Zusammenarbeit.')
        page.sections[0]['text']='Geänderter Text ohne passenden Hash.'
        monkeypatch.setattr(w.context.llm,'generate',lambda *a,**kw:pytest.fail('No paid request'))
        with pytest.raises(QualityReviewError) as error:review(w.context,state)
        assert error.value.reason=='changed_link_evidence'


def test_configured_facts_are_relevant_citable_and_scoped_to_affected_units(settings,monkeypatch):
    with Workflow(settings) as w:
        state=prepared(w,settings)
        old_root,old=plan(state,settings)
        digital=next(u for u in old if u['id']=='module:L10')['request']
        assert len(digital['approved_brand_facts'])==6
        assert all(f['id'] in digital['allowed_evidence_refs'] for f in digital['approved_brand_facts'])
        assert not any(f['fact_id']=='google_rating' for f in digital['approved_brand_facts'])
        assert 'approved_brand_facts' not in next(u for u in old if u['id']=='module:L01')['request']
        original=rules(settings,'landing_page')
        next(f for f in original['approved_facts'] if f['id']=='digital_experience')['wording']='Synthetisch geänderte freigegebene Erfahrung.'
        monkeypatch.setattr('src.combined.editorial.rules',lambda *a,**kw:deepcopy(original))
        new_root,new=plan(state,settings)
        assert old_root!=new_root
        changed=[a['id'] for a,b in zip(old,new) if a['dependency']!=b['dependency']]
        assert changed==['page','module:L10'] # Page retains company facts for cross-section consistency.


def placeholder_state(w,settings):
    state=prepared(w,settings)
    placeholder=next(m['draft_placeholder'] for m in rules(settings,'landing_page')['modules'] if m['id']=='L08')
    next(m for m in state.editorial['modules'] if m['id']=='L08')['markdown']=placeholder
    return state,placeholder


def test_declared_statement_placeholder_is_release_only_without_fabricating_a_pass(settings):
    with Workflow(settings) as w:
        state,text=placeholder_state(w,settings)
        unit=next(u for u in plan(state,settings)[1] if u['id']=='module:L08')
        finding={'module_id':'L08','quote':'[FREIGEGEBENES STATEMENT FEHLT]',
            'problem':'Freigegebenes Statement fehlt.','correction':'Bitte holen Sie die Freigabe ein.',
            'message_en':'An approved statement is pending.','action_en':'Obtain the genuine statement before release.',
            'rule':'intent','severity':'blocking','category':'content_defect'}
        result=QAUnitResult(unit_id=unit['id'],coverage_complete=True,passed=False,findings=[finding])
        with pytest.raises(QualityReviewError) as error:validate_unit(state,unit,result)
        assert error.value.reason=='release_input_misclassified'
        assert result.passed is False and result.findings[0].severity=='blocking'
        finding.update(rule='pending_input',severity='release',category='release_prerequisite')
        valid=QAUnitResult(unit_id=unit['id'],coverage_complete=True,passed=True,findings=[finding])
        assert validate_unit(state,unit,valid).findings[0].severity=='release'
        assert state.professional_review is None


def test_placeholder_marker_cannot_hide_an_added_unsupported_assertion(settings):
    with Workflow(settings) as w:
        state,text=placeholder_state(w,settings)
        next(m for m in state.editorial['modules'] if m['id']=='L08')['markdown']+='\nEine erfundene Garantie.'
        unit=next(u for u in plan(state,settings)[1] if u['id']=='module:L08')
        assert not unit['request'].get('draft_placeholders')
        result=QAUnitResult(unit_id=unit['id'],coverage_complete=True,passed=False,findings=[{
            'module_id':'L08','quote':'Eine erfundene Garantie.','problem':'Unbelegte Behauptung im Entwurf.',
            'correction':'Bitte entfernen Sie die unbelegte Behauptung.','message_en':'This assertion lacks evidence.',
            'action_en':'Remove or substantiate the assertion.','rule':'unsupported_business'}])
        assert not validate_unit(state,unit,result).passed


def test_progress_reuses_unaffected_units_even_without_a_new_revision_manifest(settings,monkeypatch):
    with Workflow(settings) as w:
        state=prepared(w,settings);calls=[]
        monkeypatch.setattr(w.context.llm,'generate',passing(calls))
        assert review(w.context,state).passed
        total=state.qa_manifest['total']
        state.revision+=1;state.seo_check=None
        next(m for m in state.editorial['modules'] if m['id']=='L10')['markdown']+='\nEine lokale redaktionelle Präzisierung.'
        result=progress(w,state)
        assert result['qa']['completed']==total-2
        assert result['review_plan']['remaining']==2 and not result['qa']['complete']


def test_oversized_link_source_blocks_the_entire_plan_before_provider_work(settings,monkeypatch):
    with Workflow(settings) as w:
        state=prepared(w,settings);target_page(state,settings,'Synthetischer Quelltext. '*100000)
        monkeypatch.setattr(w.context.llm,'generate',lambda *a,**kw:pytest.fail('No paid request'))
        with pytest.raises(ValueError):review(w.context,state)


def test_export_records_business_context_and_pending_statement_without_approval(settings):
    with Workflow(settings) as w:
        state,text=placeholder_state(w,settings)
        target=settings.output_dir/'audit'
        LocalExporter(settings.output_dir,settings).write_bundle(state,target)
        context=json.loads((target/'reviews/business-review-context.json').read_text())
        assert len(context['approved_brand_facts'])==6
        assert context['draft_placeholders'][0]['module_id']=='L08'
        assert state.professional_review is None
