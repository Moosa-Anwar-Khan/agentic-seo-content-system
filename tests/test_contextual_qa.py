"""Regressions from the saved service-page review; all integrations are fake."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from src.services.qa_units import plan, review, QAUnitResult, estimate_units
from src.services.review_contract import contract
from src.services.quality import QualityReviewError
from src.orchestrator.workflow import Workflow
from src.combined.editorial import rules, structural_errors, release_errors
from src.combined.models import EditorialOutput
from src.schemas.decisions import PageEvidence
from tests.test_pipeline_stabilization import prepared


@pytest.fixture(autouse=True)
def combined_settings(settings):
    settings.combined_mode=True
    settings.seo_decisions_enabled=True


def test_review_receives_body_page_type_company_proof_and_cross_reference(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings)
        s.icp.approved_fact_sources=[settings.wordpress_base_url+'/company/']
        s.analysis.page_evidence=[PageEvidence(id='approved-company',url=s.icp.approved_fact_sources[0],title='Company',content='Approved bookkeeping service.')]
        s.editorial['modules'].append({'id':'L04a','markdown':'## Anfrage-Kompass\nSie ordnen Ihre Fragen.'})
        s.editorial['modules']=[m for m in s.editorial['modules'] if m['id']!='L14']
        s.editorial['modules'].append({'id':'L14','markdown':'## Fragen\nDer Anfrage-Kompass hilft Ihnen.'})
        _,units=plan(s,settings)
        page=units[0]['payload']
        assert ''.join(page['article_modules'][0]['ordered_text_parts'])==s.editorial['modules'][0]['markdown']
        assert page['page_type']=='landing_page'
        assert page['review_contract']['company_evidence'][0]['ordered_text_parts']==['Approved bookkeeping service.']
        faq=next(u for u in units if u['id']=='module:L14')['payload']
        assert any(m['id']=='L04a' for m in faq['referenced_modules'])
        assert 'social' in faq['review_contract']['landing_links']
        assert 'L12' in faq['review_contract']['testimonials']


@pytest.mark.parametrize('count',[0,1,2,3])
def test_optional_reviews_do_not_require_three_or_an_empty_module(settings,monkeypatch,count):
    with Workflow(settings) as w:
        s=prepared(w,settings)
        s.editorial['modules']=[m for m in s.editorial['modules'] if m['id']!='L12']
        s.graphic_file=''
        pack=rules(settings,'landing_page')
        pack['approved_reviews']['reviews']=[{'text':'Freigegebener synthetischer Wortlaut','platform':'Synthetic','approved':True}]*count
        assert all('L12' not in row['R'] for row in pack['matrix'].values())
        errors=structural_errors(EditorialOutput.model_validate(s.editorial),s.briefing.primary_keyword,pack)
        assert not any('L12' in error for error in errors)
        assert not any('Bewertungen' in error for error in release_errors(s,settings))
        assert any('infografik' in error.lower() for error in release_errors(s,settings))
        if count:
            edited=deepcopy(s.editorial)
            edited['modules'].insert(-3,{'id':'L12','markdown':'### Erfahrungen\nFreigegebener synthetischer Wortlaut'})
            assert not any('L12' in e for e in structural_errors(EditorialOutput.model_validate(edited),s.briefing.primary_keyword,pack))


@pytest.mark.parametrize('severity,category,rule',[('suggestion','suggestion','editorial'),('release','release_prerequisite','pending_input')])
def test_style_and_pending_inputs_do_not_fail_content_but_still_remain_visible(settings,monkeypatch,severity,category,rule):
    with Workflow(settings) as w:
        s=prepared(w,settings); first=s.editorial['modules'][0]
        def generate(run,schema,task,payload,**kwargs):
            findings=[]
            if payload['unit_id']=='module:'+first['id']:
                findings=[{'module_id':first['id'],'quote':'Sie prüfen den genauen Sachverhalt.','problem':'Ein synthetischer Hinweis.','correction':'Bitte prüfen Sie den Hinweis.',
                    'severity':severity,'category':category,'rule':rule,'message_en':'Review this specific passage.','action_en':'Make a free local edit.'}]
            return schema(unit_id=payload['unit_id'],assessments=[{'criterion':c,'complete':True} for c in payload['expected_criteria']],coverage_complete=True,passed=True,findings=findings)
        monkeypatch.setattr(w.context.llm,'generate',generate)
        result=review(w.context,s)
        assert result.passed and not result.errors and len(result.findings)==1
        assert result.findings[0].message_en=='Review this specific passage.'


def test_missing_business_input_cannot_be_a_blocking_prose_defect():
    with pytest.raises(ValidationError):
        QAUnitResult(unit_id='page',coverage_complete=True,passed=False,findings=[{'module_id':'L05','quote':'[PREIS FEHLT]','message_en':'Check the quoted passage.','action_en':'Correct the saved passage.','problem':'Preis fehlt noch.','correction':'Preis freigeben lassen.','rule':'pending_input'}])


def test_unknown_evidence_and_oversized_context_stop_without_retries(settings,monkeypatch):
    with Workflow(settings) as w:
        s=prepared(w,settings); first=s.editorial['modules'][0];calls=[]
        def generate(run,schema,task,payload,**kwargs):
            calls.append(payload)
            return QAUnitResult(unit_id=payload['unit_id'],coverage_complete=True,passed=False,findings=[{'module_id':first['id'],'quote':'Sie prüfen den genauen Sachverhalt.','message_en':'Check the quoted passage.','action_en':'Correct the saved passage.','problem':'Beleg fehlt hier.','correction':'Bitte prüfen Sie den Beleg.','evidence_refs':['invented-source']}])
        monkeypatch.setattr(w.context.llm,'generate',generate)
        with pytest.raises(QualityReviewError): review(w.context,s)
        assert len(calls)==1
        calls.clear()
        s.editorial['modules'][0]['markdown']='Sie prüfen. '*100000
        with pytest.raises(ValueError):review(w.context,s)
        assert not calls


def test_link_policy_and_source_change_invalidate_affected_units(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings)
        _,old=plan(s,settings)
        s.internal_link_policy='starter_library'
        _,new=plan(s,settings)
        assert old[0]['dependency']!=new[0]['dependency']
        assert all(a['dependency']!=b['dependency'] for a,b in zip(old,new))


def test_live_quality_stop_never_purchases_an_automatic_rewrite(settings,monkeypatch):
    from src.schemas.content import CheckResult
    with Workflow(settings) as w:
        state=prepared(w,settings)
        state.brand_check=CheckResult(passed=False,errors=['Ein belegter Widerspruch muss korrigiert werden.'])
        before=(state.revision,state.auto_revisions,state.editorial)
        settings.max_auto_revisions=5
        monkeypatch.setattr(w.context.llm,'generate',lambda *args,**kwargs:pytest.fail('No automatic paid rewrite'))
        result=w._repair({'run':state.model_dump(mode='json')})['run']
        assert result['status']=='failed'
        assert (result['revision'],result['auto_revisions'],result['editorial'])==before


def test_company_evidence_hash_changes_are_not_promoted_to_verified_facts(settings):
    import hashlib
    with Workflow(settings) as w:
        state=prepared(w,settings)
        state.icp.approved_fact_sources=[settings.wordpress_base_url+'/company/']
        state.analysis.page_evidence=[PageEvidence(id='company',url=state.icp.approved_fact_sources[0],title='Company',content='Changed text.',content_hash=hashlib.sha256(b'Original text.').hexdigest())]
        with pytest.raises(QualityReviewError):plan(state,settings)


def test_old_attempt_cannot_be_current_failure_for_locally_corrected_revision(settings):
    from src.services.progress import progress
    from src.schemas.jobs import Job
    from src.schemas.content import CheckResult
    from uuid import uuid4
    with Workflow(settings) as w:
        state=prepared(w,settings)
        w.storage.create_job(Job(run_id=state.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4(),status='failed',source_revision=state.revision,result_revision=state.revision,error_code='quality_failed'))
        state.revision+=1;state.status='draft_created'
        state.brand_check=CheckResult(passed=True);state.seo_check=None
        state.agent_outputs['local_correction']={'revision':state.revision}
        result=progress(w,state)
        assert result['state']=='Locally corrected — affected checks pending'
        assert result['latest_attempt']['historical']
        assert result['current_failure'] is None


def test_same_missing_price_is_presented_once_with_all_original_evidence(settings):
    from src.services.progress import progress
    from src.schemas.content import CheckResult
    with Workflow(settings) as w:
        state=prepared(w,settings)
        state.seo_check=None;state.brand_check=CheckResult(passed=True)
        state.release_blockers=['Offene Fachfrage: Preise fehlen für diese Leistungen.','Freigegebene Preise fehlen.']
        report=progress(w,state)
        requirements=[i for i in report['findings'] if i['category']=='release_prerequisite']
        assert len(requirements)==1
        assert 'Offene Fachfrage:' in requirements[0]['original_finding']
        assert 'Freigegebene Preise fehlen.' in requirements[0]['original_finding']


def test_page_has_deterministic_inventory_while_module_owns_target_evidence(settings):
    with Workflow(settings) as w:
        state=prepared(w,settings)
        state.analysis.page_evidence=[PageEvidence(id='guide-a',url=settings.wordpress_base_url+'/guide-a/',title='First guide'),PageEvidence(id='guide-b',url=settings.wordpress_base_url+'/guide-b/',title='Second guide')]
        faq=next(m for m in state.editorial['modules'] if m['id']=='L14')
        faq['markdown']='## Fragen\n[First guide]('+state.analysis.page_evidence[0].url+')\n[Second guide]('+state.analysis.page_evidence[1].url+')'
        _,units=plan(state,settings);request=units[0]['request']
        assert not {'guide-a','guide-b'}&set(request['allowed_evidence_refs'])
        assert 'verified_link_targets' not in request
        assert 'link_inventory' not in request # Page no longer repeats deterministic link counts.
        module=next(u['request'] for u in units if u['id']=='module:L14')
        assert {'guide-a','guide-b'}<=set(module['allowed_evidence_refs'])
        assert len(module['verified_link_targets'])==2


def test_policy_paths_in_saved_failure_are_rejected_with_exact_diagnostic_reason(settings,monkeypatch):
    with Workflow(settings) as w:
        state=prepared(w,settings);first=state.editorial['modules'][0];calls=[]
        def generate(run,schema,task,payload,**kwargs):
            calls.append(payload)
            return QAUnitResult(unit_id=payload['unit_id'],coverage_complete=True,passed=False,findings=[{
                'module_id':first['id'],'quote':'Sie prüfen den genauen Sachverhalt.',
                'problem':'Ein synthetischer Befund.','correction':'Prüfen Sie die Verlinkung.',
                'message_en':'Inspect the supplied links.','action_en':'Inspect the policy and source inventory.',
                'evidence_refs':['internal_link_policy.landing_guide_minimum',first['id']]}])
        monkeypatch.setattr(w.context.llm,'generate',generate)
        with pytest.raises(QualityReviewError) as error: review(w.context,state)
        assert error.value.reason=='unknown_evidence_reference'
        assert error.value.validation_fields==['findings.evidence_refs']
        assert len(calls)==1 and not state.qa_manifest and state.professional_review is None
