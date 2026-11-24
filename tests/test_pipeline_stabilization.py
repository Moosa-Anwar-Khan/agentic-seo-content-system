"""Bounded QA, failure accounting and immutable allowances use no live APIs."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.combined.models import SupportedClaim
from src.orchestrator.workflow import Workflow
from src.schemas.jobs import Job
from src.services.jobs import JobWorker
from src.services.llm import ModelRequestError, ModelRouter
from src.services.model_routes import resolve_route
from src.services.progress import progress
from src.services.qa_units import QAUnitResult, plan, review, manifest_key
from src.services.quality import QualityReviewError
from src.storage.sqlite_storage import ResearchBudgetExceeded
from tests.conftest import authenticated_headers, own_fixture
from tests.test_combined import draft_state, source


def prepared(workflow,settings):
    state=draft_state(workflow)
    evidence=source().model_dump(mode='json')
    claim=SupportedClaim(id='test-claim',text='Sie prüfen den genauen Sachverhalt.',source_id=evidence['id'],
        quote='einer genauen einschlägigen Passage',tax_year=settings.tax_year,conditions='Synthetischer Offline-Test.')
    state.editorial['modules'][0]['markdown']+='\n\n'+claim.text
    state.editorial['claims']=[claim.model_dump(mode='json')]
    state.research_packet={'official_sources':[evidence]}
    settings.demo_mode=False
    return state


def passing(calls):
    def generate(run_id,schema,task,payload,**kwargs):
        calls.append(payload)
        assert issubclass(schema,QAUnitResult)
        return schema(unit_id=payload['unit_id'],assessments=[{'criterion':c,'complete':True} for c in payload['expected_criteria']],coverage_complete=True,passed=True,findings=[])
    return generate


def test_all_modules_and_claims_are_reviewed_and_complete_cache_survives_restart(settings,monkeypatch):
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    calls=[]
    with Workflow(settings) as workflow:
        state=prepared(workflow,settings)
        expected=1+len(state.editorial['modules'])+len(state.editorial['claims'])
        original=passing(calls)
        def interrupted(*args,**kwargs):
            if len(calls)==2:raise RuntimeError('synthetic provider interruption')
            return original(*args,**kwargs)
        monkeypatch.setattr(workflow.context.llm,'generate',interrupted)
        with pytest.raises(RuntimeError):review(workflow.context,state)
        assert len(calls)==2
        report=progress(workflow,state)
        assert report['qa']['completed']==2 and not report['qa']['complete']
    with Workflow(settings) as restarted:
        monkeypatch.setattr(restarted.context.llm,'generate',passing(calls))
        result=review(restarted.context,state)
        assert result.passed and len(calls)==expected
        assert len({p['unit_id'] for p in calls})==expected
        assert {p['unit_id'] for p in calls if p['review_kind']=='module'}=={'module:'+m['id'] for m in state.editorial['modules']}
        assert state.qa_manifest['complete']
        assert review(restarted.context,state).passed and len(calls)==expected
        state.revision+=1
        review(restarted.context,state)
        assert len(calls)==expected  # An identical dependency is reusable across revisions.
        state.editorial['modules'][2]['markdown']+='\nEin zusätzlicher redaktioneller Satz.'
        review(restarted.context,state)
        assert len(calls)==expected+2  # Page plus changed module; unaffected units stay cached.


def test_source_parts_are_complete_and_fingerprint_tracks_source_content(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    with Workflow(settings) as workflow:
        state=prepared(workflow,settings)
        record=state.research_packet['official_sources'][0]
        record['text']='Amtlicher Testtext mit Bedingungen. '*1000
        record['content_hash']=hashlib.sha256(record['text'].encode()).hexdigest()
        fingerprint,units=plan(state,settings)
        parts=next(u for u in units if u['kind']=='claim')['payload']['official_source']['ordered_text_parts']
        assert ''.join(parts)==record['text'] and all(len(part)<=10000 for part in parts)
        record['text']+=' Zusatz.';record['content_hash']=hashlib.sha256(record['text'].encode()).hexdigest()
        assert plan(state,settings)[0]!=fingerprint
        record['content_hash']='invalid'
        with pytest.raises(QualityReviewError):plan(state,settings)
        assert progress(workflow,state)['qa']['evidence_blocked']


def test_explicit_blog_kind_survives_german_intent_without_a_linked_research_snapshot(settings):
    from src.services.seo_decisions import topics
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    with Workflow(settings) as workflow:
        state=draft_state(workflow);child=state.topic_map.pillars[0].clusters[0]
        child.kind='blog';child.intent='Informationsbedarf zu abziehbaren Kosten'
        state.research_snapshot={}
        assert next(topic for topic in topics(state) if topic['id']==child.id)['page_type']=='blog'
        child.kind='cluster';child.intent='commercial'
        assert next(topic for topic in topics(state) if topic['id']==child.id)['page_type']=='landing_page'


def test_blog_reading_links_use_verified_targets_without_assuming_a_permalink_prefix(settings):
    from src.combined.editorial import link_errors
    from src.schemas.decisions import PageEvidence
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    with Workflow(settings) as workflow:
        state=draft_state(workflow)
        state.editorial['page_type']='blog'
        targets=[settings.wordpress_base_url+'/guide-'+str(i)+'/' for i in range(6)]
        landing=settings.wordpress_base_url+'/zielgruppen/immobilien/'
        state.analysis.page_evidence=[PageEvidence(id='verified-'+str(i),url=url,title='Synthetischer vorhandener Ratgeber') for i,url in enumerate(targets+[landing])]
        state.editorial['modules']=[{'id':'S08','markdown':'## Fachlicher Überblick\n'+ '\n'.join('[Ratgeber]('+url+')' for url in targets[:3])},
            {'id':'S15','markdown':'## Weitere Lektüre\n'+ '\n'.join('[Ratgeber]('+url+')' for url in targets)},
            {'id':'S03','markdown':'## Beratung\n[Unsere Beratung]('+landing+')'}]
        state.editorial['modules'] += [{'id':key,'markdown':'Synthetischer Abschnitt ohne weitere Links.'} for key in ['S01','S02','S04','S05','S06']]
        assert not link_errors(state,settings)
        state.analysis.page_evidence.pop(0)
        assert any(targets[0] in error and 'inventar' in error.lower() for error in link_errors(state,settings))


@pytest.mark.parametrize('problem',['unit_id','quote','module','claim'])
def test_ungrounded_unit_cannot_complete_or_authorize_a_rewrite(settings,monkeypatch,problem):
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    with Workflow(settings) as workflow:
        state=prepared(workflow,settings);first=state.editorial['modules'][0]
        finding={'module_id':first['id'],'claim_id':None,'quote':'Sie prüfen den genauen Sachverhalt.',
                 'message_en':'Check the quoted passage.','action_en':'Correct the saved passage.','problem':'Prüfen Sie die Bedingungen.','correction':'Ergänzen Sie die belegten Bedingungen.'}
        if problem=='quote':finding['quote']='Nicht im gespeicherten Text.'
        if problem=='module':finding['module_id']='S99'
        if problem=='claim':finding['claim_id']='made-up'
        monkeypatch.setattr(workflow.context.llm,'generate',lambda run,schema,task,payload,**kwargs:
            QAUnitResult(unit_id='wrong' if problem=='unit_id' else payload['unit_id'],
             coverage_complete=True,passed=False,findings=[finding]))
        with pytest.raises(QualityReviewError):review(workflow.context,state)
        assert not state.qa_manifest and not state.professional_review
        assert progress(workflow,state)['qa']['completed']==0


@pytest.mark.parametrize('limit',[2000,6000])
def test_saved_failure_equivalents_never_pass_even_with_complete_error_strings(settings,limit):
    content=json.dumps({'passed':False,'errors':['Wiederholter Befund. '*20]*70},ensure_ascii=False)[:limit*3]
    with pytest.raises(ModelRequestError) as error:
        ModelRouter._validate(resolve_route(settings,'seo_qa'),QAUnitResult,content,'length')
    assert error.value.code=='output_limit'


def test_repeating_unit_is_detected_in_truncated_json_without_accepting_partial_findings(settings):
    finding={'module_id':'L04','quote':'Sie prüfen den Sachverhalt.','message_en':'Check the quoted passage.','action_en':'Correct the saved passage.','problem':'Synthetisches Problem.','correction':'Synthetische Korrektur.'}
    content='{"findings":['+json.dumps(finding)+','+json.dumps(finding)+',{"unfinished":'
    with pytest.raises(ModelRequestError) as error:
        ModelRouter._validate(resolve_route(settings,'seo_qa'),QAUnitResult,content,'length')
    assert error.value.code=='quality_repetition'


def test_run_budget_is_atomic_immutable_and_preserves_legacy_limits(settings):
    from src.orchestrator.state import RunState
    from src.schemas.icp import icp_1_buy_and_hold
    with Workflow(settings) as workflow:
        state=RunState(icp=icp_1_buy_and_hold,run_budget_usd=.25)
        workflow.storage.save_run_state(state)
        def reserve(amount):
            try:workflow.storage.reserve_research_cost(str(state.run_id),amount,1000000);return True
            except ResearchBudgetExceeded:return False
        with ThreadPoolExecutor(2) as pool:accepted=list(pool.map(reserve,[160000,160000]))
        assert sum(accepted)==1
        with pytest.raises(ResearchBudgetExceeded):reserve_result=workflow.storage.reserve_research_cost(str(state.run_id),100000,1000000)
        state.run_budget_usd=1
        with pytest.raises(ValueError,match='immutable'):workflow.storage.save_run_state(state)
        assert workflow.storage.get_run_state(str(state.run_id)).run_budget_usd==.25
        legacy=state.model_copy(update={'run_id':uuid4(),'run_budget_usd':None})
        workflow.storage.save_run_state(legacy)
        workflow.storage.reserve_research_cost(str(legacy.run_id),300000,1000000)


def test_progress_reports_failed_usage_without_overwriting_checkpoint_or_draft(settings,monkeypatch):
    with Workflow(settings) as workflow:
        state=workflow.start(__import__('src.schemas.icp',fromlist=['icp_1_buy_and_hold']).icp_1_buy_and_hold)
        before=state.html;job=Job(run_id=state.run_id,kind='quality_checks',reviewer='Sam',idempotency_key=uuid4())
        workflow.storage.create_job(job)
        def fail(_):
            key=workflow.storage.reserve_tokens(str(state.run_id),200,settings.max_run_tokens)
            workflow.storage.settle_tokens(key,123)
            raise ModelRequestError(resolve_route(settings,'seo_qa'),'output_limit')
        worker=JobWorker(settings,workflow);monkeypatch.setattr(worker,'_execute',fail)
        assert worker.process_one()
        saved=workflow.storage.get_run_state(str(state.run_id))
        assert saved.tokens_used==123 and saved.html==before
        current=progress(workflow,workflow.get(str(state.run_id)))
        assert current['state']=='Checks need attention' and current['tokens_used']==123
        assert current['latest_attempt']['error_code']=='output_limit'
        assert not worker.process_one()
        from src.schemas.content import CheckResult
        from datetime import datetime,timezone
        edited=workflow.get(str(state.run_id));edited.status='draft_created'
        edited.updated_at=datetime.now(timezone.utc);edited.brand_check=CheckResult(passed=False,errors=['Synthetischer aktueller Befund.'])
        assert progress(workflow,edited)['state']=='Checks need attention'


def test_progress_and_creation_budget_api_are_private_and_backward_compatible(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        headers=authenticated_headers(client,'Sam')
        response=client.post('/jobs',headers=headers,json={'kind':'start','idempotency_key':str(uuid4()),
            'icp_id':'icp_1_buy_and_hold','run_budget_usd':.25})
        assert response.status_code==202,response.text
        run_id=response.json()['run_id'];workflow=client.app.state.workflow
        assert workflow.get(run_id).run_budget_usd==.25
        report=client.get('/runs/'+run_id+'/progress');assert report.status_code==200
        assert report.json()['costs']['limit_microusd']==250000
        assert 'api_key' not in report.text
        assert client.post('/jobs',headers=headers,json={'kind':'quality_checks','run_id':run_id,
            'idempotency_key':str(uuid4()),'run_budget_usd':.5}).status_code==422
        authenticated_headers(client,'Alex')
        assert client.get('/runs/'+run_id+'/progress').status_code==404
