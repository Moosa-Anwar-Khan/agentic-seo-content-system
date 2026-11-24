"""Staged linking, evidence eligibility and preflight never require live providers."""
import hashlib
import json
from datetime import date
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.combined.editorial import link_errors
from src.combined.evidence import allowed_official,source_issue
from src.combined.research import prompt_packet
from src.orchestrator.workflow import Workflow
from src.schemas.decisions import PageEvidence
from src.services.link_policy import contract,followups
from src.services.model_routes import resolve_route,text_request_options,ModelConfigurationError,capabilities
from src.services.preflight import inspect,ensure_ready,PreflightBlocked
from src.services.qa_units import plan,review,QAUnitResult
from src.services.research_budget import text_upper_price
from tests.test_combined import draft_state,source
from tests.conftest import authenticated_headers,own_fixture


@pytest.fixture(autouse=True)
def combined_settings(settings):
    settings.combined_mode=True
    settings.seo_decisions_enabled=True


def blog(workflow,settings):
    state=draft_state(workflow);state.editorial['page_type']='blog';state.briefing.page_type='blog'
    targets=[settings.wordpress_base_url+'/blog/guide-'+str(i)+'/' for i in range(2)]
    state.analysis.page_evidence=[PageEvidence(id=str(i),url=u,title='Immobilien Ratgeber '+str(i),content='Sie lesen den Ratgeber.') for i,u in enumerate(targets)]
    service=settings.wordpress_base_url+'/zielgruppen/immobilien/'
    state.analysis.page_evidence.append(PageEvidence(id='service',url=service,title='Steuerberatung Immobilien'))
    state.editorial['modules']=[{'id':'S08','markdown':'## Orientierung\n[Ratgeber]('+targets[0]+')'},
        {'id':'S15','markdown':'## Weiterlesen\n'+ '\n'.join('[Ratgeber]('+u+')' for u in targets)},
        {'id':'S07','markdown':'## Beratung\n[Beratung]('+service+')'}]
    state.editorial['modules'] += [{'id':'S'+str(i).zfill(2),'markdown':'## Synthetischer Abschnitt\nSie erhalten Orientierung.'} for i in [1,2,3,4,5]]
    state.research_packet={'official_sources':[source().model_dump(mode='json')],'tax_year':settings.tax_year}
    return state


def test_staged_policy_is_explicit_immutable_and_provenance_is_unchanged(settings):
    with Workflow(settings) as w:
        state=blog(w,settings)
        assert contract(state)['policy']=='strict' and len(link_errors(state,settings))==2
        state.run_id=uuid4()  # A new disposable run; never edit a persisted policy.
        state.internal_link_policy='starter_library'
        assert not link_errors(state,settings)
        assert followups(state)[0]['blocking'] is False
        w.storage.save_run_state(state)
        state.internal_link_policy='strict'
        with pytest.raises(ValueError,match='immutable'):w.storage.save_run_state(state)
        state.internal_link_policy='starter_library'
        state.editorial['modules'][0]['markdown']='## Orientierung\n[Ratgeber](https://steuerkanzlei.example/blog/invented/)'
        assert any('inventar' in e.lower() for e in link_errors(state,settings))
        state.editorial['modules'][1]['markdown']='## Weiterlesen\n[Ratgeber](https://evil.example/guide/)'
        assert any('Externer' in e for e in link_errors(state,settings))


def test_duplicate_fragments_do_not_fill_strict_quantities_and_policy_changes_qa_identity(settings):
    with Workflow(settings) as w:
        state=blog(w,settings);fingerprint=plan(state,settings)[0]
        state.internal_link_policy='starter_library'
        assert plan(state,settings)[0]!=fingerprint
        state.internal_link_policy='strict';url=state.analysis.page_evidence[0].url
        state.editorial['modules'][0]['markdown']='## Orientierung\n'+ '\n'.join('[Link]('+url+'?x='+str(i)+')' for i in range(3))
        state.editorial['modules'][1]['markdown']='## Weiterlesen\n'+ '\n'.join('[Link]('+url+'#section'+str(i)+')' for i in range(6))
        assert len(link_errors(state,settings))==2


def test_full_ordered_source_handoff_excludes_forums_partial_and_wrong_year(settings):
    record=source().model_dump(mode='json');record['text']='Amtlicher Text mit vollständigen Bedingungen. '*1000
    record['content_hash']=hashlib.sha256(record['text'].encode()).hexdigest()
    forum={**record,'id':'forum','url':'https://forum.elster.de/anwenderforum/topic'}
    wrong={**record,'id':'old-form','url':'https://www.elster.de/eportal/helpGlobal?themaGlobal=help_est_2025'}
    partial={**record,'id':'partial','truncated':True}
    state=SimpleNamespace(research_packet={'tax_year':2026,'official_sources':[record,forum,wrong,partial]})
    packet=prompt_packet(state)
    assert ''.join(packet['official_sources'][0]['ordered_text_parts'])==record['text']
    assert len(packet['excluded_sources'])==3
    assert not allowed_official(forum['url'])
    assert source_issue(wrong,2026) and not source_issue(wrong,2025)


def test_preflight_is_read_only_private_and_worker_blocks_before_generation(settings,monkeypatch):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam');w=client.app.state.workflow
        state=own_fixture(client,blog(w,settings));w.storage.save_run_state(state)
        w.graph.update_state(w._config(str(state.run_id)),{'run':state.model_dump(mode='json')},as_node='content_agent')
        before=w.storage.get_run_state(str(state.run_id)).model_dump_json()
        base='/runs/'+str(state.run_id)+'/preflight'
        data=client.get(base).json()
        assert data['status']=='blocked' and any(i['code']=='guide_inventory' for i in data['findings'])
        assert client.get(base+'?recommendation_id=missing').status_code==404
        assert w.storage.get_run_state(str(state.run_id)).model_dump_json()==before
        authenticated_headers(client,'Other');assert client.get(base).status_code==404
        settings.demo_mode=False
        with pytest.raises(PreflightBlocked):ensure_ready(w.context,state,'research')
        assert not w.storage.list_ai_calls(str(state.run_id))


def test_openai_writer_options_and_short_long_context_reservations(settings):
    settings.briefing_provider=settings.content_provider='openai'
    settings.briefing_model=settings.content_model='gpt-6.1-sol'
    for task,schema,limit in [('briefing','SelectedBriefing',4000),('content','EditorialOutput',12000)]:
        route=resolve_route(settings,task);opts=text_request_options(settings,route,schema)
        assert capabilities(settings,route)['context_tokens']==1050000
        assert opts['reasoning_effort']=='low' and opts['service_tier']=='default'
        assert 'temperature' not in opts and opts['max_tokens']==min(settings.max_output_tokens,limit)
        assert float(text_upper_price(settings,route,10000,1000))==pytest.approx(.0375)
        assert float(text_upper_price(settings,route,300000,1000))==pytest.approx(1.51875)
    settings.ai_task_options=json.dumps({'content':{'reasoning_effort':'none'}})
    with pytest.raises(ModelConfigurationError):text_request_options(settings,route,'EditorialOutput')


def test_api_persists_creation_policy_and_cannot_change_it_with_other_jobs(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam')
        payload={'kind':'start','icp_id':'icp_1_buy_and_hold','idempotency_key':str(uuid4()),'internal_link_policy':'starter_library','run_budget_usd':1}
        response=client.post('/jobs',headers=headers,json=payload);assert response.status_code==202
        assert client.post('/jobs',headers=headers,json=payload).json()['id']==response.json()['id']
        state=client.app.state.workflow.get(response.json()['run_id']);assert state.internal_link_policy=='starter_library' and state.run_budget_usd==1
        changed={**payload,'internal_link_policy':'strict'}
        assert client.post('/jobs',headers=headers,json=changed).status_code==409
        assert client.post('/jobs',headers=headers,json={**payload,'kind':'quality_checks','run_id':str(state.run_id),'idempotency_key':str(uuid4())}).status_code==422


def test_starter_policy_does_not_turn_irrelevant_links_into_a_semantic_pass(settings,monkeypatch):
    with Workflow(settings) as w:
        state=blog(w,settings);state.internal_link_policy='starter_library'
        def generate(run_id,schema,task,payload,**kwargs):
            assert payload['internal_link_policy']['policy']=='starter_library'
            findings=[]
            if payload['unit_id']=='module:S15':
                findings=[{'module_id':'S15','quote':'Ratgeber','message_en':'Check the quoted passage.','action_en':'Correct the saved passage.','severity':'blocking','category':'content_defect','rule':'required_module','requirement_id':'required_module','material_impact':'The linked resource does not satisfy the required reader need.','problem':'Das Linkziel passt nicht zum Thema.','correction':'Wählen Sie einen einschlägigen vorhandenen Ratgeber.'}]
            return schema(unit_id=payload['unit_id'],assessments=[{'criterion':c,'complete':True} for c in payload['expected_criteria']],coverage_complete=True,passed=not findings,findings=findings)
        monkeypatch.setattr(w.context.llm,'generate',generate)
        result=review(w.context,state)
        assert not result.passed and 'S15' in result.errors[0]


def test_preflight_failure_retains_researched_inputs_across_restart(settings):
    with Workflow(settings) as w:
        state=blog(w,settings);state.run_id=uuid4();w.storage.save_run_state(state)
        settings.demo_mode=False  # Missing synthetic credentials intentionally block.
        with pytest.raises(PreflightBlocked):ensure_ready(w.context,state,'content')
        key=f'preflight-inputs:{state.run_id}:{state.revision}'
        saved=w.storage.get_operation(key)['value']
        assert saved['research_packet']==state.research_packet
        retained=state.model_copy(deep=True);retained.research_packet={}
    with Workflow(settings) as restarted:
        assert inspect(restarted,retained)['eligible_source_ids']==[source().id]
        assert restarted.storage.get_operation(key)['value']==saved


def test_supervised_quality_stop_does_not_buy_an_automatic_rewrite(settings):
    from src.services.recovery import running_job
    from src.schemas.jobs import Job
    from src.schemas.content import CheckResult
    with Workflow(settings) as w:
        state=blog(w,settings);state.brand_check=CheckResult(passed=False,errors=['Synthetischer Korrekturbedarf.'])
        job=Job(run_id=state.run_id,kind='selection',reviewer='Synthetic operator',idempotency_key=uuid4(),payload={'stop_on_quality_findings':True})
        before=state.auto_revisions
        with running_job(job,w.storage):result=w._repair({'run':state.model_dump(mode='json')})
        assert result['run']['status']=='failed' and result['run']['auto_revisions']==before
