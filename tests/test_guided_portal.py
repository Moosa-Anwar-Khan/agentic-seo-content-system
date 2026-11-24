"""Real failed responses, typed judgments, annotation safety and free workflow."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from src.orchestrator.workflow import Workflow
from src.services.qa_units import plan,schema_for,validate_unit,review,persist,cached_unit
from src.services.guided_qa import CRITERIA,guard_key,readiness,QA_SYSTEM
from src.services.guided_portal import classify,preview,workflow_summary,location
from src.services.progress import progress
from src.services.quality import QualityReviewError
from tests.test_pipeline_stabilization import prepared,passing
from tests.test_qa_responsibilities import saved_state
from scripts.correct_revision_five import corrected

@pytest.fixture(autouse=True)
def combined(settings):settings.combined_mode=True;settings.seo_decisions_enabled=True

def answer(unit,findings=None):
    findings=findings or []
    return schema_for(unit['kind'])(unit_id=unit['id'],coverage_complete=True,
      passed=not any(f.get('severity')=='blocking' for f in findings),findings=findings,
      assessments=[{'criterion':c,'complete':True} for c in unit['request']['expected_criteria']])

def suggestion(module,quote):
    return dict(module_id=module,quote=quote,rule='editorial',severity='suggestion',category='suggestion',
       problem='Eine optionale Formulierung.',correction='Sie können den Satz kürzen.',
       message_en='Optional wording improvement.',action_en='Keep it or make a free local edit.')

def test_latest_paid_response_has_multiple_independent_errors_and_never_passes(settings):
    raw=json.loads((Path(__file__).parent/'fixtures/revision_five_latest_invalid_review.json').read_text())
    before=deepcopy(raw)
    with Workflow(settings) as w:
        s=saved_state(w,settings);s.editorial=corrected(s.editorial);s.revision=5
        page=plan(s,settings)[1][0]
        with pytest.raises(ValidationError):schema_for('page').model_validate(raw)
        for f in raw['findings']:
            one={**raw,'passed':f['severity']!='blocking','findings':[f],
                 'assessments':[{'criterion':c,'complete':True} for c in CRITERIA['page']]}
            with pytest.raises((ValidationError,QualityReviewError)):
                validate_unit(s,page,schema_for('page').model_validate(one))
        assert raw==before and s.seo_check is None
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_page_context_separates_saved_website_from_current_article(settings):
    with Workflow(settings) as w:
        s=saved_state(w,settings);s.editorial=corrected(s.editorial);s.revision=5
        units=plan(s,settings)[1];page=units[0];p=page['request']
        assert 'company_evidence' not in p['review_contract']
        assert p['review_contract']['company_services']==s.icp.services
        assert p['approved_brand_facts']
        assert p['allowed_evidence_refs']==[f['id'] for f in p['approved_brand_facts']]
        assert 'page-9' not in p['allowed_evidence_refs']
        assert {m['id']:''.join(m['ordered_text_parts']) for m in p['article_modules']}=={
            m['id']:m['markdown'] for m in s.editorial['modules']}
        assert 'two different CURRENT article_modules' in page['prompt']
        assert 'older website omits them' in page['prompt']
        module=next(u for u in units if u['id']=='module:L10')
        assert module['request']['review_contract']['company_evidence']
        assert 'Do not require website corroboration' in module['prompt']
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_saved_source_comparisons_remain_invalid_without_discarding_findings(settings):
    raw=json.loads((Path(__file__).parent/'fixtures/revision_five_evidence_scope_review.json').read_text())
    original=deepcopy(raw)
    with Workflow(settings) as w:
        s=saved_state(w,settings);s.editorial=corrected(s.editorial);s.revision=5
        page=plan(s,settings)[1][0]
        # Replay the complete actual response, then every unsupported contradiction.
        with pytest.raises(QualityReviewError):validate_unit(s,page,schema_for('page').model_validate(raw))
        for finding in raw['findings']:
            if finding['rule']!='contradiction':continue
            single={**raw,'findings':[finding],'passed':False}
            with pytest.raises(QualityReviewError):validate_unit(s,page,schema_for('page').model_validate(single))
        assert raw==original and s.seo_check is None
        assert not w.storage.list_ai_calls(str(s.run_id))

@pytest.mark.parametrize('kind',['page','module','claim'])
def test_coverage_cannot_be_asserted_by_a_standalone_pass(settings,kind):
    with Workflow(settings) as w:
        s=prepared(w,settings);u=next(u for u in plan(s,settings)[1] if u['kind']==kind)
        a=answer(u);a.assessments=[]
        with pytest.raises(QualityReviewError) as e:validate_unit(s,u,a)
        assert e.value.reason=='incomplete_review_coverage'
        a=answer(u);a.assessments.append(a.assessments[0])
        with pytest.raises(QualityReviewError):validate_unit(s,u,a)

@pytest.mark.parametrize('severity',['suggestion','blocking'])
def test_minor_style_can_only_be_a_suggestion_in_wire_schema(settings,severity):
    with Workflow(settings) as w:
        s=prepared(w,settings);u=next(u for u in plan(s,settings)[1] if u['id']=='module:L01')
        f=suggestion('L01','Sie prüfen den genauen Sachverhalt.')
        f.update(severity=severity,category='suggestion' if severity=='suggestion' else 'content_defect')
        if severity=='blocking':
            with pytest.raises(ValidationError):answer(u,[f])
        else:assert validate_unit(s,u,answer(u,[f])).passed


def test_material_contradiction_requires_two_real_different_passages(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings)
        s.editorial['modules'][0]['markdown']+='\nEine Antwort erfolgt in 48 Stunden.'
        s.editorial['modules'][1]['markdown']+='\nEine Antwort erfolgt erst nach 30 Tagen.'
        u=plan(s,settings)[1][0];a,b=s.editorial['modules'][:2]
        f=dict(module_id=a['id'],quote='Eine Antwort erfolgt in 48 Stunden.',rule='contradiction',
            severity='blocking',category='content_defect',requirement_id='contradiction',
            material_impact='The response commitments conflict.',message_en='The response times conflict.',
            action_en='Resolve the contradictory service commitment.',problem='Die Zusagen widersprechen sich.',
            correction='Bestätigen Sie die tatsächliche Zusage.',conflicting_passages=[
              {'module_id':a['id'],'quote':'Eine Antwort erfolgt in 48 Stunden.'},
              {'module_id':b['id'],'quote':'Eine Antwort erfolgt erst nach 30 Tagen.'}])
        assert not validate_unit(s,u,answer(u,[f])).passed
        f['conflicting_passages'][1]['quote']='Invented passage.'
        with pytest.raises(QualityReviewError):validate_unit(s,u,answer(u,[f]))
        f['conflicting_passages']=f['conflicting_passages'][:1]
        with pytest.raises(ValidationError):answer(u,[f])


def test_minor_only_full_review_passes_but_does_not_approve_or_release(settings,monkeypatch):
    with Workflow(settings) as w:
        s=prepared(w,settings);s.seo_check=None;units=plan(s,settings)[1]
        def fake(run,schema,task,payload,**kwargs):
            u=next(u for u in units if u['id']==payload['unit_id'])
            return answer(u,[suggestion('L01','Sie prüfen den genauen Sachverhalt.')] if u['id']=='module:L01' else [])
        monkeypatch.setattr(w.context.llm,'generate',fake)
        result=review(w.context,s)
        assert result.passed and result.findings[0].category=='suggestion'
        assert s.professional_review is None and s.status!='exported'


def test_same_invalid_unit_is_blocked_across_new_attempts_and_restart(settings,monkeypatch):
    with Workflow(settings) as w:
        s=prepared(w,settings);w.storage.save_run_state(s);calls=[]
        def invalid(*a,**kw):calls.append(1);raise QualityReviewError('missing_contradiction_pair')
        monkeypatch.setattr(w.context.llm,'generate',invalid)
        with pytest.raises(QualityReviewError):review(w.context,s)
        assert not readiness(w.storage,s,settings)['ready']
        with pytest.raises(QualityReviewError,match='grounded'):review(w.context,s)
        assert len(calls)==1
    with Workflow(settings) as w:
        assert not readiness(w.storage,s,settings)['ready']
        s.editorial['modules'][0]['markdown']+='\nA material context correction.'
        assert readiness(w.storage,s,settings)['ready']


def test_known_token_shortfall_stops_before_any_paid_unit(settings,monkeypatch):
    from src.storage.sqlite_storage import BudgetExceeded
    from src.services.qa_units import estimate_units
    with Workflow(settings) as w:
        s=prepared(w,settings);units=plan(s,settings)[1]
        largest=max(e['maximum_token_reservation'] for e in estimate_units(settings,units))
        settings.max_run_tokens=largest-1
        status=readiness(w.storage,s,settings)
        assert not status['ready'] and status['token_budget']['largest_pending_reservation']==largest
        assert 'token reservation' in status['reason'] and not status['blocked_units']
        monkeypatch.setattr(w.context.llm,'generate',lambda *a,**kw:pytest.fail('Known token shortfall must stop before any paid unit'))
        with pytest.raises(BudgetExceeded):review(w.context,s)
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_token_preflight_excludes_accepted_units(settings):
    from src.services.qa_units import estimate_units
    with Workflow(settings) as w:
        s=prepared(w,settings);units=plan(s,settings)[1]
        estimates=estimate_units(settings,units)
        last=min(estimates,key=lambda e:e['maximum_token_reservation'])
        for u in units:
            if u['id']!=last['unit_id']:
                persist(w.storage,'qa-unit:'+str(s.run_id)+':'+u['dependency'],s.run_id,
                    {'result':answer(u).model_dump(mode='json')})
        settings.max_run_tokens=last['maximum_token_reservation']
        status=readiness(w.storage,s,settings)
        assert status['ready']
        assert status['token_budget']['total_pending_reservation_bound']==settings.max_run_tokens
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_preview_highlights_inline_markdown_without_changing_saved_html(settings):
    from src.combined.editorial import render
    from src.combined.models import EditorialOutput
    with Workflow(settings) as w:
        s=prepared(w,settings);s.editorial['modules'][0]['markdown']='# Titel\n\nSie **prüfen** den Sachverhalt.'
        s.html=render(EditorialOutput.model_validate(s.editorial));original=s.html
        items=classify(s,[dict(code='x',category='suggestion',blocking=False,module_id='L01',quote='Sie **prüfen** den Sachverhalt.',message='Optional wording.',action='Review it.')])
        assert items[0]['location']['status']=='exact'
        html=preview(s,items,items[0]['finding_id'])
        from bs4 import BeautifulSoup
        mark=BeautifulSoup(html,'html.parser').find('mark')
        assert mark and 'qa-suggestion' in mark.get('class',[]) and mark['id']=='finding-'+items[0]['finding_id']
        assert s.html==original and '<mark' not in original
        assert '<script' not in html


def test_ambiguous_missing_and_blue_findings_never_mark_arbitrary_text(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings);s.editorial['modules'][0]['markdown']='Repeated text. Repeated text.'
        assert location(s,{'module_id':'L01','quote':'Repeated text.'})['status']=='ambiguous'
        assert location(s,{'module_id':'L01','quote':'Missing text.'})['status']=='not_found'
        html=preview(s,[{'display_kind':'review_error','finding_id':'blue','module_id':'L01','location':{'status':'section_only'},'quote':'Sie'}],'blue')
        assert '<mark' not in html


def test_workflow_permissions_and_mandatory_gates(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings);s.owner_user_id='owner';s.seo_check=None
        owner=workflow_summary(s,{}, {'id':'owner','role':'member','name':'Owner'})
        assert owner['current_step']=='qa' and owner['permissions']['draft']
        assert len(owner['steps'])==8 and not owner['permissions']['final']
        visitor=workflow_summary(s,{}, {'id':'other','role':'tax_adviser','name':'Adviser'})
        assert not visitor['permissions']['draft'] and visitor['permissions']['professional']
        assert s.professional_review is None


def test_preview_api_is_private_revision_bound_read_only_and_sandboxed(settings):
    from src.api.app import create_app
    from tests.conftest import authenticated_headers,own_fixture
    settings.demo_mode=True;app=create_app(settings)
    with TestClient(app) as client:
        authenticated_headers(client)
        w=app.state.workflow;s=prepared(w,settings);s=own_fixture(client,s);w.storage.save_run_state(s)
        before=w.storage.get_run_state(str(s.run_id)).model_dump_json();cost=w.storage.research_costs(str(s.run_id))
        url='/runs/'+str(s.run_id)+'/review-preview'
        response=client.get(url,params={'revision':s.revision})
        assert response.status_code==200 and "script-src" not in response.headers['content-security-policy']
        assert "default-src 'none'" in response.headers['content-security-policy']
        assert client.get(url,params={'revision':s.revision+1}).status_code==409
        assert client.get(url,params={'revision':s.revision,'finding_id':'../x'}).status_code==422
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert w.storage.research_costs(str(s.run_id))==cost
        client.cookies.clear();assert client.get(url,params={'revision':s.revision}).status_code==401


def test_claim_contradiction_uses_official_source_not_an_invented_second_draft_passage(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings);u=next(u for u in plan(s,settings)[1] if u['kind']=='claim')
        source=u['request']['official_source'];claim=u['request']['claim']
        f=dict(module_id=u['module_ids'][0],claim_id=claim['id'],quote=claim['text'],rule='contradiction',
          severity='blocking',category='content_defect',requirement_id='tax_claim',
          material_impact='A synthetic tax statement conflicts with the supplied original.',
          message_en='The claim conflicts with the original source.',action_en='Correct the supported tax assertion.',
          problem='Die Aussage widerspricht der Originalquelle.',correction='Prüfen Sie die belegte Aussage.',
          evidence_refs=[source['id']],source_quotes=[{'evidence_ref':source['id'],'quote':claim['quote']}])
        assert not validate_unit(s,u,answer(u,[f])).passed
        f['source_quotes'][0]['quote']='Invented official quotation.'
        with pytest.raises(QualityReviewError):validate_unit(s,u,answer(u,[f]))
        f['source_quotes'][0]['quote']='   '
        with pytest.raises((ValidationError,QualityReviewError)):validate_unit(s,u,answer(u,[f]))


def test_relabelled_formatting_objection_cannot_override_deterministic_approved_fact(settings):
    with Workflow(settings) as w:
        s=saved_state(w,settings);s.editorial=corrected(s.editorial)
        u=next(u for u in plan(s,settings)[1] if u['id']=='module:L10')
        f=dict(module_id='L10',quote='**Über 12 Jahre Erfahrung in der digitalen Steuerberatung.**',
          rule='required_module',severity='blocking',category='content_defect',requirement_id='required_module',
          material_impact='Capitalisation is alleged to change a required statement.',
          message_en='The reviewer objects to emphasis.',action_en='Inspect the review contract.',
          problem='Die Hervorhebung wurde beanstandet.',correction='Prüfen Sie den Befund.')
        with pytest.raises(QualityReviewError) as error:validate_unit(s,u,answer(u,[f]))
        assert error.value.reason=='approved_wording_rejected'


def test_invalid_review_guard_is_enforced_by_api_and_worker(settings,monkeypatch):
    from src.api.app import create_app
    from src.schemas.jobs import Job
    from src.services.jobs import JobWorker
    from tests.conftest import authenticated_headers,own_fixture
    settings.demo_mode=True;app=create_app(settings)
    with TestClient(app) as client:
        headers=authenticated_headers(client);w=app.state.workflow;s=own_fixture(client,prepared(w,settings))
        s.status='brand_check_passed';s.seo_check=None
        w.graph.update_state(w._config(str(s.run_id)),{'run':s.model_dump(mode='json')},as_node='content_agent')
        w.storage.save_run_state(s);u=plan(s,settings)[1][0]
        persist(w.storage,guard_key(s,u),s.run_id,{'reason':'review_scope_conflict'})
        before=len(w.storage.list_jobs(str(s.run_id)))
        response=client.post('/jobs',headers=headers,json={'kind':'quality_checks','run_id':str(s.run_id),'expected_revision':s.revision,'idempotency_key':str(uuid4())})
        assert response.status_code==409 and 'unchanged invalid review' in response.json()['detail'] and len(w.storage.list_jobs(str(s.run_id)))==before
        actor=app.state.auth.identify(client.cookies.get('review_session'))
        job=Job(run_id=s.run_id,kind='quality_checks',reviewer=actor['name'],reviewer_id=actor['id'],idempotency_key=uuid4(),payload={'source_revision':s.revision})
        monkeypatch.setattr(w,'quality_checks_queued',lambda *a:pytest.fail('A blocked worker must not execute paid QA'))
        with pytest.raises(QualityReviewError):JobWorker(settings,w)._execute(job)
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_metadata_highlight_is_bound_to_named_field(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings);u=plan(s,settings)[1][0]
        f=suggestion(u['module_ids'][0],s.editorial['meta_title']);f['metadata_field']='meta_title'
        result=validate_unit(s,u,answer(u,[f]));item=classify(s,[{'code':'meta',**result.findings[0].model_dump(),'message':f['message_en'],'action':f['action_en'],'blocking':False}])[0]
        assert item['location']=={'status':'exact','field':'meta_title'}
        f['metadata_field']='meta_description'
        with pytest.raises(QualityReviewError):validate_unit(s,u,answer(u,[f]))


def test_complete_l10_references_cannot_fail_for_paragraph_grouping(settings):
    with Workflow(settings) as w:
        s=saved_state(w,settings);s.editorial=corrected(s.editorial)
        u=next(u for u in plan(s,settings)[1] if u['id']=='module:L10')
        assert u['request']['established_module_facts']['approved_references_present']
        f=dict(module_id='L10',quote='Ein Digital-Spezialist im Team hält die Kanzlei technisch auf dem neuesten Stand. Ein IT-Partnerunternehmen sorgt für moderne, durchdachte Workflows.',rule='required_module',severity='blocking',category='content_defect',requirement_id='L10.combined_statement',material_impact='The reviewer objects only to separate paragraphs.',message_en='The reviewer objects to separate statements.',action_en='Inspect the minor presentation finding.',problem='Die Aussagen stehen in verschiedenen Absätzen.',correction='Sie können die Aussagen in einem Absatz verbinden.')
        with pytest.raises(QualityReviewError) as e:validate_unit(s,u,answer(u,[f]))
        assert e.value.reason=='minor_detail_misclassified'
        # Actually removing an approved reference still permits an essential finding.
        module=next(m for m in s.editorial['modules'] if m['id']=='L10')
        module['markdown']=module['markdown'].replace('**Über 12 Jahre Erfahrung in der digitalen Steuerberatung.**','')
        u=next(u for u in plan(s,settings)[1] if u['id']=='module:L10')
        assert not u['request']['established_module_facts']['approved_references_present']
        assert not validate_unit(s,u,answer(u,[f])).passed


def test_completed_review_retains_metadata_location_in_progress(settings,monkeypatch):
    with Workflow(settings) as w:
        s=prepared(w,settings);units=plan(s,settings)[1]
        def fake(run,schema,task,payload,**kwargs):
            u=next(u for u in units if u['id']==payload['unit_id']);findings=[]
            if u['kind']=='page':
                f=suggestion(u['module_ids'][0],s.editorial['meta_title']);f['metadata_field']='meta_title';findings=[f]
            return answer(u,findings)
        monkeypatch.setattr(w.context.llm,'generate',fake)
        s.seo_check=review(w.context,s)
        assert s.seo_check.passed
        report=progress(w,s)
        item=next(f for f in report['findings'] if f['display_kind']=='suggestion')
        assert item['metadata_field']=='meta_title' and item['location']=={'status':'exact','field':'meta_title'}


def test_exact_operation_identity_includes_validator_fingerprint(settings):
    from src.services.guided_qa import validator_fingerprint
    with Workflow(settings) as w:
        s=prepared(w,settings);_,units=plan(s,settings)
        expected=validator_fingerprint()
        assert len(expected)==64
        assert all(u['request']['_qa_contract']['validator_hash']==expected for u in units)


def test_module_can_report_an_unregistered_tax_assertion_without_impossible_source_proof(settings):
    with Workflow(settings) as w:
        s=prepared(w,settings);s.editorial['modules'][0]['markdown']+='\nEine synthetische steuerliche Aussage benötigt einen Quellenbeleg.'
        u=next(u for u in plan(s,settings)[1] if u['id']=='module:L01')
        f=dict(module_id='L01',quote='Eine synthetische steuerliche Aussage benötigt einen Quellenbeleg.',rule='tax_claim',claim_status='unregistered',severity='blocking',category='content_defect',requirement_id='tax_claim',material_impact='The substantive assertion has no registered original source claim.',message_en='This assertion has no registered source claim.',action_en='Supply an applicable original source and claim registration.',problem='Ein registrierter Quellenbeleg fehlt.',correction='Belegen Sie die Aussage mit einer Originalquelle.')
        assert not validate_unit(s,u,answer(u,[f])).passed
        # A registered claim cannot be rejected merely for lacking registration.
        registered=s.editorial['claims'][0]
        f['quote']=registered['text']
        with pytest.raises(QualityReviewError):validate_unit(s,u,answer(u,[f]))
