"""Acceptance and negative tests for the new combined workflow."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from src.combined.evidence import allowed_official, claim_errors, retrieve
from src.combined.models import SourceEvidence, SupportedClaim, EditorialOutput
from src.combined.pipeline import demo_draft, require_release
from src.combined.editorial import structural_errors, rules, render
from src.combined.graphics import attach
from src.combined.fetch import Fetcher, FetchPolicy
from src.orchestrator.workflow import Workflow
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.decisions import SelectionRequest
from src.schemas.review import ReviewRequest, ReviewEvent
from src.services.social import SocialService
from src.services.research_budget import micros
from src.api.app import create_app
from tests.conftest import authenticated_headers, own_fixture


@pytest.fixture
def combined(settings):
    settings.combined_mode=True
    settings.seo_decisions_enabled=True
    return settings


def draft_state(workflow):
    state=workflow.start(icp_1_buy_and_hold)
    assert state.status=='awaiting_selection'
    chosen=next(r for r in state.analysis.recommendations if r.action!='protect')
    return workflow.submit_selection(str(state.run_id),SelectionRequest(
        recommendation_id=chosen.id,expected_revision=state.revision,
        acknowledge_uncertainty=True,notes='Synthetischer Test zur Funktionsprüfung, keine echte Freigabe.'),'Sam')


def test_complete_offline_flow_has_three_independent_platforms_and_never_publishes(combined):
    with Workflow(combined) as workflow:
        state=draft_state(workflow)
        assert state.status=='seo_check_passed'
        assert 6<=len(state.topic_map.pillars)<=8
        assert state.editorial and state.html.startswith('<!doctype html>')
        assert 'application/ld+json' in state.html
        assert state.release_blockers
        assert any('Demo' in b for b in state.release_blockers)
        posts=SocialService(combined,workflow.storage).prepare(state)
        assert {p.platform for p in posts}=={'linkedin','google_business_profile'}
        assert all(p.status=='pending' for p in posts)
        with pytest.raises(ValueError,match='source website'):
            SocialService(combined,workflow.storage).review(state,str(posts[0].id),'approved','Alex')
        with pytest.raises(ValueError,match='Release is blocked'):
            workflow.submit_review(str(state.run_id),ReviewRequest(action='approved',
                expected_revision=state.revision,factual_review_confirmed=True,
                graphic_review_confirmed=True,manual_upload_confirmed=True),'Sam')
        assert not (combined.output_dir/'runs'/str(state.run_id)/'approved').exists()


def test_website_revisions_invalidate_social_drafts_and_leave_research_unchanged(combined):
    with Workflow(combined) as workflow:
        state=draft_state(workflow)
        posts=SocialService(combined,workflow.storage).prepare(state)
        revised=workflow.submit_review(str(state.run_id),ReviewRequest(action='needs_revision',
            expected_revision=state.revision,feedback_notes='Bitte die offenen Fragen klarer darstellen.'),'Alex')
        assert revised.revision>state.revision
        assert revised.executed_agents.count('seo_strategy_agent')==1
        with pytest.raises(ValueError):
            SocialService(combined,workflow.storage).review(revised,str(posts[0].id),'approved','Alex')


@pytest.mark.parametrize('url,expected',[
    ('https://www.gesetze-im-internet.de/estg/__7.html',True),
    ('https://www.bundesfinanzhof.de/de/entscheidungen/',True),
    ('https://gesetze-im-internet.de.evil.test/fake',False),
    ('https://evil.test/?source=gesetze-im-internet.de',False),
    ('http://www.gesetze-im-internet.de/estg/',False),
    ('https://user:password@www.gesetze-im-internet.de/estg/',False),
    ('https://127.0.0.1/internal',False),
    ('https://www.gesetze-im-internet.de:444/estg/',False),
])
def test_official_source_allowlist(url,expected):
    assert bool(allowed_official(url)) is expected


def source():
    text='Ein gespeicherter Gesetzestext mit einer genauen einschlägigen Passage.'
    return SourceEvidence(id='source-1',url='https://www.gesetze-im-internet.de/estg/__7.html',
        kind='statute',text=text,content_hash=hashlib.sha256(text.encode()).hexdigest())


def test_claims_need_literal_official_support_correct_period_and_draft_membership(combined):
    state=SimpleNamespace(briefing=SimpleNamespace(page_type='landing_page',primary_keyword='Immobiliensteuerberater'))
    draft=demo_draft(state,combined)
    claim=SupportedClaim(id='claim-1',text='Sie prüfen den Sachverhalt.',source_id='source-1',
        quote='einer genauen einschlägigen Passage',tax_year=2026,conditions='Nur im zutreffenden Anwendungsbereich.')
    draft.modules[0].markdown+='\n\n'+claim.text
    draft.claims=[claim]
    assert not claim_errors(draft,[source().model_dump(mode='json')],2026)
    claim.quote='Diese erfundene Passage steht nicht in der Quelle.'
    assert claim_errors(draft,[source().model_dump(mode='json')],2026)
    claim.quote='einer genauen einschlägigen Passage';claim.tax_year=2025
    assert claim_errors(draft,[source().model_dump(mode='json')],2026)
    claim.tax_year=2026
    competitor=source().model_copy(update={'kind':'competitor'})
    assert claim_errors(draft,[competitor.model_dump(mode='json')],2026)


def test_source_cache_requires_freshness_and_integrity(combined):
    from src.combined.enums import FetchStatus
    calls=[]
    class FakeFetcher:
        def fetch(self,url,policy):
            calls.append(url)
            return SimpleNamespace(text=source().text,truncated=False,body=None,status=FetchStatus.READ)
    urls=[{'url':source().url,'kind':'statute'}]
    first,errors=retrieve(combined,urls,FakeFetcher())
    second,_=retrieve(combined,urls,FakeFetcher())
    assert first==second and not errors and len(calls)==1
    cached=next((combined.data_dir/'evidence').glob('*.json'))
    data=json.loads(cached.read_text());data['text']='Modified source without updating its hash'
    cached.write_text(json.dumps(data))
    retrieve(combined,urls,FakeFetcher())
    assert len(calls)==2
    data=json.loads(cached.read_text());data['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    cached.write_text(json.dumps(data));retrieve(combined,urls,FakeFetcher())
    assert len(calls)==3


def test_broken_anchors_and_raw_html_are_detected(combined):
    state=SimpleNamespace(briefing=SimpleNamespace(page_type='blog',primary_keyword='Immobiliensteuerberater'))
    draft=demo_draft(state,combined)
    draft.modules[0].markdown+='\n[Situation](#missing-heading)\n<script>alert(1)</script>'
    found=structural_errors(draft,state.briefing.primary_keyword,rules(combined,'blog'))
    assert any('Sprungziel' in x for x in found)
    assert any('HTML' in x for x in found)
    assert '<script>alert' not in render(draft)
    draft.signature_element='<img src=x onerror=alert(1)>'
    assert '<img src=x' not in render(draft)


def test_metadata_and_infographic_schema_have_binding_limits(combined):
    state=SimpleNamespace(briefing=SimpleNamespace(page_type='blog',primary_keyword='Immobiliensteuerberater'))
    value=demo_draft(state,combined).model_dump()
    value['meta_title']='X'*61
    with pytest.raises(ValueError):EditorialOutput.model_validate(value)
    value['meta_title']='X'*30;value['meta_description']='X'*139
    with pytest.raises(ValueError):EditorialOutput.model_validate(value)
    value['meta_description']='X'*140;value.pop('graphic')
    with pytest.raises(ValueError):EditorialOutput.model_validate(value)


def test_paid_model_budget_reserves_before_call_and_unknown_prices_block(combined,monkeypatch):
    from src.services.llm import ModelRouter
    from src.schemas.content import CheckResult
    from src.services.research_budget import text_upper_price
    from src.services.model_routes import resolve_route
    import litellm
    from tests.test_paid_recovery import model_response
    combined.demo_mode=False;combined.openai_api_key='test-key'
    combined.ai_model_capabilities=json.dumps({'openai/gpt-5.4-mini':{'json_output':True,'context_tokens':400000}})
    calls=[]
    with Workflow(combined) as workflow:
        run_id=str(uuid4())
        monkeypatch.setattr(litellm,'token_counter',lambda **kw:20)
        def completion(**kw):
            assert workflow.storage.research_costs(run_id)['reserved_microusd']>0
            calls.append(1);return model_response()
        monkeypatch.setattr(litellm,'completion',completion)
        ModelRouter(combined,workflow.storage).generate(run_id,CheckResult,'Check',{},model_role='seo_strategy')
        combined.max_run_usd=.000001
        with pytest.raises(Exception):
            ModelRouter(combined,workflow.storage).generate(run_id,CheckResult,'Check again',{},model_role='seo_strategy')
        assert len(calls)==1
    combined.research_price_rates='{}'
    route=resolve_route(combined,'seo_strategy')
    assert text_upper_price(combined,route,1000,1000)>0
    combined.research_price_rates=json.dumps({route.litellm_model:{'input':1,'output':1,'verified_at':'2000-01-01'}})
    with pytest.raises(ValueError,match='expired'):text_upper_price(combined,route,1,1)


def test_manual_graphic_is_revision_bound_and_validated(combined):
    with Workflow(combined) as workflow:
        state=draft_state(workflow);run_id=str(state.run_id)
        stream=BytesIO();Image.new('RGB',(1200,600),'white').save(stream,format='WEBP')
        attached=attach(workflow,run_id,stream.getvalue(),state.revision)
        assert attached.graphic_revision==state.revision
        assert 'data:image/webp;base64' in attached.html
        with pytest.raises(ValueError):attach(workflow,run_id,b'not an image',state.revision)
        with pytest.raises(ValueError):attach(workflow,run_id,stream.getvalue(),state.revision+1)


def test_final_approval_requires_named_reviewer_even_for_python_callers(combined):
    with Workflow(combined) as workflow:
        state=draft_state(workflow)
        event=ReviewEvent(action='approved',expected_revision=state.revision,
                          reviewer_name='Other person',event_id=str(uuid4()))
        with pytest.raises(PermissionError):require_release(combined,state,event)


def install_reviewable_fixture(workflow, combined, monkeypatch):
    """Synthetic complete document; source text and reviews are test fixtures only."""
    import src.combined.editorial as editorial
    from src.schemas.decisions import PageEvidence
    state=draft_state(workflow)
    keyword=state.briefing.primary_keyword
    state.briefing.page_type='landing_page'
    draft=demo_draft(state,combined)
    draft.unresolved_questions=[]
    for i,module in enumerate(draft.modules):
        module.markdown=('# ' if i==0 else '## ')+keyword+' '+module.id+'\n\n'+(
            'Sie sammeln Ihre Fragen und bereiten Ihre Unterlagen auf das persönliche Gespräch vor. '*4)
        if i in {0,3,7,12}:
            module.markdown+='\n\n[Erstgespräch vereinbaren](https://steuerkanzlei.example/mandant-werden/)'
    faq=next(m for m in draft.modules if m.id=='L14')
    for q in ['Kosten','Unterlagen','Fristen','Wechsel','Fehlende Unterlagen','Vorbereitung']:
        faq.markdown+='\n\n### Was besprechen Sie zum Thema '+q+'?\n\nSie klären Ihre Fragen persönlich.'
    urls=['https://steuerkanzlei.example/blog/test-eins/','https://steuerkanzlei.example/blog/test-zwei/']
    for url in urls:
        faq.markdown+='\n\n[Weitere Informationen]('+url+')'
        state.analysis.page_evidence.append(PageEvidence(id='fixture-'+str(len(state.analysis.page_evidence)),url=url,title='Synthetischer Ratgeber'))
    state.editorial=draft.model_dump(mode='json')
    state.article=editorial.as_article(draft,keyword)
    state.html=render(draft)
    state.research_packet={'official_sources':[source().model_dump(mode='json')],'official_errors':[]}
    original_rules=editorial.rules
    def supplied_rules(settings,page_type):
        result=original_rules(settings,page_type)
        result['approved_reviews']['reviews']=[{'text':'Synthetischer Testbeleg'}]*3
        return result
    monkeypatch.setattr(editorial,'rules',supplied_rules)
    workflow.graph.update_state(workflow._config(str(state.run_id)),{'run':state.model_dump(mode='json')})
    combined.demo_mode=False
    stream=BytesIO();Image.new('RGB',(1200,600),'white').save(stream,format='WEBP')
    state=attach(workflow,str(state.run_id),stream.getvalue(),state.revision)
    assert not state.release_blockers
    return state


def test_complete_reviewed_fixture_exports_locally_and_social_approval_stays_separate(combined,monkeypatch):
    """Exercise real gates with synthetic local evidence; never a live tax approval."""
    import src.combined.editorial as editorial
    from src.schemas.decisions import PageEvidence
    with Workflow(combined) as workflow:
        state=install_reviewable_fixture(workflow, combined, monkeypatch)
        with pytest.raises(ValueError,match='confirmation'):
            workflow.submit_review(str(state.run_id),ReviewRequest(action='approved',expected_revision=state.revision),'Alex')
        from src.security.auth import AccountStore
        from src.services.professional_review import attest
        from src.schemas.review import ProfessionalReviewRequest
        adviser=AccountStore(workflow.storage).create_user('adviser@example.org','Test-Steuerberaterin','tax_adviser','test-password-123')
        state=attest(workflow,str(state.run_id),ProfessionalReviewRequest(expected_revision=state.revision,
            factual_review_confirmed=True,graphic_review_confirmed=True,notes='Synthetische fachliche Prüfung für den Offline-Test.'),adviser['id'])
        approved=workflow.submit_review(str(state.run_id),ReviewRequest(action='approved',expected_revision=state.revision,
            factual_review_confirmed=True,graphic_review_confirmed=True,manual_upload_confirmed=True),'Alex')
        assert approved.status=='exported'
        directory=combined.output_dir/'runs'/str(state.run_id)/'approved'
        assert 'NOT PUBLISHED' in (directory/'STATUS.txt').read_text()
        assert (directory/'assets'/state.editorial['graphic']['filename']).is_file()
        combined.demo_mode=True  # Offline social fixtures only; no external request.
        social=SocialService(combined,workflow.storage)
        posts=social.prepare(approved)
        social.review(approved,str(posts[0].id),'approved','Sam')
        assert workflow.storage.get_asset(str(posts[1].id),'post').status=='pending'


def test_api_exposes_evidence_and_never_approves_an_offline_placeholder(combined):
    with TestClient(create_app(combined)) as client:
        headers=authenticated_headers(client,'Sam')
        workflow=client.app.state.workflow;state=draft_state(workflow);own_fixture(client,state)
        run_id=str(state.run_id)
        response=client.get('/runs/'+run_id+'/release')
        assert response.status_code==200 and response.json()['manual_upload_only'] is True
        assert response.json()['blockers']
        denied=client.post('/runs/'+run_id+'/reviews',headers=headers,json={
            'action':'approved','expected_revision':state.revision,
            'factual_review_confirmed':True,'graphic_review_confirmed':True,'manual_upload_confirmed':True})
        assert denied.status_code==409
        bundle=client.get('/runs/'+run_id+'/bundle')
        from zipfile import ZipFile
        with ZipFile(BytesIO(bundle.content)) as archive:
            statuses=json.loads(archive.read('platform-status.json'))
            assert statuses['website']=='pending'
            assert statuses['manual_upload_only'] is True
            assert 'reviews/claim-source-sheet.json' in archive.namelist()
