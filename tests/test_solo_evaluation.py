"""Separate unapproved evaluation, with real routing/accounting and fake providers."""
import io
import json
import hashlib
import zipfile
from datetime import date
from types import SimpleNamespace
from uuid import uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError
from src.api.app import create_app
from src.orchestrator.workflow import Workflow
from src.schemas.content import CheckResult
from src.schemas.jobs import Job
from src.schemas.evaluation import EvaluationReview,EvaluationFinding
from src.services.evaluation import preflight,execute,latest,package,current_key,validate_review,VERSION
from src.services.recovery import running_job,UncertainOperation
from src.services.qa_units import plan,schema_for,persist
from src.services.jobs import JobWorker
from src.services.social import SocialService
from tests.test_pipeline_stabilization import prepared
from tests.conftest import authenticated_headers,own_fixture

@pytest.fixture(autouse=True)
def combined_mode(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True

CRITERIA=['text','spelling','consistency','support','channel_rules']


def fixture(settings,w,*,vision=False):
    settings.combined_mode=True;settings.max_run_tokens=1000000;settings.max_run_usd=5
    settings.deepseek_api_key='fake';settings.openai_api_key='fake'
    settings.llm_model_planning=settings.llm_model_drafting='deepseek/deepseek-flash'
    settings.content_provider='openai';settings.content_model='gpt-6.1-sol'
    caps={'deepseek/deepseek-flash':{'json_output':True,'context_tokens':1000000},
          'openai/gpt-6.1-sol':{'json_output':True,'context_tokens':1050000,'vision':True}}
    if vision:caps['openai/gpt-6.1-sol']['evaluation_vision_bound']={'max_image_tokens':4000,'detail':'high','verified_at':date.today().isoformat(),'source':'https://developers.openai.com/api/docs/guides/images-vision'}
    settings.ai_model_capabilities=json.dumps(caps)
    settings.research_price_rates=json.dumps({key:{'input':.1,'output':.2,'verified_at':date.today().isoformat()} for key in caps})
    s=prepared(w,settings).model_copy(deep=True,update={'run_id':uuid4()});s.run_budget_usd=5;s.campaign_id=s.run_id;s.campaign_budget_usd=5
    s.brand_check=CheckResult(passed=True,errors=[],warnings=[]);s.seo_check=s.brand_check.model_copy(deep=True);s.status='seo_check_passed'
    path=settings.output_dir/'runs'/str(s.run_id)/'manual-assets'/'test.webp';path.parent.mkdir(parents=True)
    Image.new('RGB',(1400,1600),'white').save(path,'WEBP')
    s.graphic_file=str(path);s.graphic_sha256=hashlib.sha256(path.read_bytes()).hexdigest();s.graphic_revision=s.revision
    for u in plan(s,settings)[1]:
        result=schema_for(u['kind'])(unit_id=u['id'],coverage_complete=True,passed=True,findings=[],assessments=[{'criterion':c,'complete':True} for c in u['request']['expected_criteria']])
        persist(w.storage,'qa-unit:'+str(s.run_id)+':'+u['dependency'],s.run_id,{'result':result.model_dump(),'reviewed_revision':s.revision})
    w.storage.save_run_state(s)
    return s


def provider(monkeypatch,*,failure=None,unreadable=False,findings=None):
    import litellm
    calls=[]
    def completion(**kw):
        calls.append(kw)
        if failure:raise failure
        schema=json.loads(kw['messages'][0]['content'].split('JSON Schema: ',1)[1])['title']
        if schema=='SocialDrafts':value={'linkedin':'Sie ordnen Ihre Fragen und Unterlagen für das Beratungsgespräch.','google_business_profile':'Sie besprechen Ihre Fragen und Unterlagen im persönlichen Beratungsgespräch.'}
        else:value={'coverage':CRITERIA,'findings':findings or [],'limitations':['Synthetic evaluation only.'],'visible_text':'Unterlagen für Ihr Gespräch','unreadable_areas':['Small footer'] if unreadable and isinstance(kw['messages'][1]['content'],list) else []}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(value,ensure_ascii=False)))],usage=SimpleNamespace(prompt_tokens=100,completion_tokens=100,total_tokens=200),_hidden_params={'response_cost':.00001})
    monkeypatch.setattr(litellm,'completion',completion);monkeypatch.setattr(litellm,'token_counter',lambda **kw:100)
    return calls


def job_for(w,s,report):
    j=Job(run_id=s.run_id,kind='evaluation',idempotency_key=uuid4(),reviewer='Sam',source_revision=s.revision,payload={'source_revision':s.revision,'evaluation_plan':report})
    w.storage.create_job(j)
    return j


def test_free_preflight_and_partial_package_preserve_state(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);before=w.storage.get_run_state(str(s.run_id)).model_dump_json()
        p=preflight(settings,w.storage,s)
        assert p['ready'] and len(p['article_qa']['reused'])==len(plan(s,settings)[1])
        assert [u['id'] for u in p['units']]==['social_generate','social_review']
        assert any(f['section']=='infographic' and f['kind']=='blue' for f in p['findings'])
        assert p['maximum_tokens']>0
        z=zipfile.ZipFile(io.BytesIO(package(settings,w.storage,s)))
        assert 'infographic-unapproved.webp' in z.namelist()
        assert 'unapproved' in z.read('README.txt').decode()
        topic_plan=json.loads(z.read('topic-maps/topic-map.json'))
        assert topic_plan==s.topic_map.model_dump(mode='json')
        from bs4 import BeautifulSoup
        page=BeautifulSoup(z.read('topic-maps/topic-map.html'),'html.parser').get_text(' ',strip=True)
        assert all(p.title in page for p in s.topic_map.pillars)
        assert 'keine fertig erstellten oder freigegebenen Seiten' in page
        assert 'topic-maps/topic-map.html' in z.read('START_HERE.html').decode()
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_package_without_topic_plan_has_no_dangling_plan_link(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);s.topic_map=None
        z=zipfile.ZipFile(io.BytesIO(package(settings,w.storage,s)))
        assert not any(name.startswith('topic-maps/') for name in z.namelist())
        assert 'topic-maps/topic-map.html' not in z.read('START_HERE.html').decode()
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_full_evaluation_uses_actual_private_image_and_never_approves(settings,monkeypatch):
    calls=provider(monkeypatch)
    with Workflow(settings) as w:
        s=fixture(settings,w,vision=True);p=preflight(settings,w.storage,s);before=s.model_dump()
        j=job_for(w,s,p)
        with running_job(j,w.storage):r=execute(w,j)
        assert r['status']=='evaluated_unapproved' and len(calls)==3
        image=calls[-1]['messages'][1]['content'][1]['image_url']
        assert image['url'].startswith('data:image/webp;base64,') and image['detail']=='high'
        assert all(c['num_retries']==0 for c in calls)
        assert not w.storage.list_assets(str(s.run_id),'post')
        assert w.get(str(s.run_id)).model_dump(exclude={'tokens_used'})=={k:v for k,v in before.items() if k!='tokens_used'}
        assert not s.professional_review and s.status=='seo_check_passed'
        with pytest.raises(ValueError):SocialService(settings,w.storage).prepare(s)
        assert 'linkedin-evaluation.txt' in zipfile.ZipFile(io.BytesIO(package(settings,w.storage,s))).namelist()
        # Same durable job after a restart reuses all accepted units.
        with running_job(j,w.storage):execute(w,j)
        assert len(calls)==3
        p2=preflight(settings,w.storage,s)
        assert len(p2['reused'])==3 and p2['maximum_reservation_microusd']==0
        assert w.storage.tokens_used(str(s.run_id))==600


def test_stale_revision_or_evidence_never_calls_provider(settings,monkeypatch):
    calls=provider(monkeypatch)
    with Workflow(settings) as w:
        s=fixture(settings,w);p=preflight(settings,w.storage,s);j=job_for(w,s,p)
        s.editorial['modules'][0]['markdown']+=' Changed evidence.';w.storage.save_run_state(s)
        with running_job(j,w.storage),pytest.raises(ValueError):execute(w,j)
        assert calls==[] and latest(w.storage,s,settings) is None
        assert not preflight(settings,w.storage,s)['article_qa']['ready']


def test_no_replacement_after_invalid_review(settings,monkeypatch):
    bad=[{'category':'material','rule':'unsupported_claim','section':'linkedin','quote':'invented passage','evidence_ref':'approved_services','evidence_quote':'not present','message_en':'A supported material problem.','action_en':'Correct the passage using verified facts.'}]
    calls=provider(monkeypatch,findings=bad)
    with Workflow(settings) as w:
        s=fixture(settings,w);p=preflight(settings,w.storage,s);j=job_for(w,s,p)
        with running_job(j,w.storage),pytest.raises(ValueError):execute(w,j)
        r=latest(w.storage,s,settings)
        assert r['status']=='incomplete' and r['results']['social_generate']
        assert r['findings'][-1]['kind']=='blue' and len(calls)==2
        assert not preflight(settings,w.storage,s)['ready']
        with running_job(j,w.storage),pytest.raises(ValueError):execute(w,j)
        assert len(calls)==2


def test_unreadable_image_is_incomplete_and_style_never_material(settings,monkeypatch):
    provider(monkeypatch,unreadable=True)
    with Workflow(settings) as w:
        s=fixture(settings,w,vision=True);j=job_for(w,s,preflight(settings,w.storage,s))
        with running_job(j,w.storage):r=execute(w,j)
        assert r['status']=='incomplete' and any(f['kind']=='blue' and f['section']=='infographic' for f in r['findings'])
    with pytest.raises(ValidationError):EvaluationFinding(category='material',rule='style',section='linkedin',quote='word',evidence_ref='x',evidence_quote='x',message_en='Use another style.',action_en='Change the style.')
    x=EvaluationReview(coverage=CRITERIA,findings=[dict(category='suggestion',rule='style',section='linkedin',quote='Sie ordnen',message_en='Optional shorter wording.',action_en='Keep it or edit locally.')],limitations=[])
    assert validate_review(x,{'linkedin':'Sie ordnen Ihre Fragen.'},{})[0]['kind']=='yellow'


def test_changed_plan_and_missing_confirmation_are_rejected_for_owner(settings,monkeypatch):
    provider(monkeypatch)
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close()
        headers=authenticated_headers(client,'Solo');w=client.app.state.workflow;s=fixture(settings,w)
        own_fixture(client,s);w.storage.save_run_state(s)
        path=f'/runs/{s.run_id}/evaluation';p=client.get(path+'/preflight').json()
        assert p['ready'] and client.get(path+'/download').status_code==200
        body={'kind':'evaluation','run_id':str(s.run_id),'expected_revision':s.revision,'idempotency_key':str(uuid4()),'evaluation_plan_id':p['plan_id']}
        assert client.post('/jobs',json=body,headers=headers).status_code==409
        body['confirm_evaluation']=True
        response=client.post('/jobs',json=body,headers=headers);assert response.status_code==202,response.text
        assert client.post('/jobs',json=body,headers=headers).json()['id']==response.json()['id']
        worker=JobWorker(settings,w);assert worker.process_one()
        assert client.get(path).json()['evaluation']['results']['social_generate']
        assert w.get(str(s.run_id)).professional_review is None
        authenticated_headers(client,'Stranger')
        assert client.get(path).status_code==404 and client.get(path+'/download').status_code==404


def test_token_budget_and_unsupported_sources_stop_before_spending(settings,monkeypatch):
    calls=provider(monkeypatch)
    with Workflow(settings) as w:
        s=fixture(settings,w);settings.max_run_tokens=1000
        assert not preflight(settings,w.storage,s)['ready']
        settings.max_run_tokens=1000000
        s.research_packet['official_sources'][0]['text']='tampered'
        assert not preflight(settings,w.storage,s)['article_qa']['ready']
        assert calls==[]


def test_malformed_or_incomplete_assessments_never_pass():
    with pytest.raises(ValidationError):EvaluationReview(coverage=['text'],findings=[],limitations=[])
    with pytest.raises(ValueError):validate_review(EvaluationReview(coverage=CRITERIA,findings=[dict(category='material',rule='contradiction',section='linkedin',quote='exact',evidence_ref='x',evidence_quote='forged',message_en='A contradiction is present.',action_en='Correct this supported contradiction.')],limitations=[]),{'linkedin':'exact'},{'x':'different'})


def test_referenced_stale_original_is_retrieved_free_and_does_not_revalidate_old_qa(settings,monkeypatch):
    from datetime import datetime,timedelta,timezone
    calls=provider(monkeypatch);retrieved=[]
    def fetch(conf,urls,**kwargs):
        assert not conf.firecrawl_enabled
        retrieved.extend(urls)
        return [{'id':urls[0]['id'],'url':urls[0]['url'],'text':'Changed original source.','content_hash':'different'}],[]
    monkeypatch.setattr('src.combined.evidence.retrieve',fetch)
    with Workflow(settings) as w:
        s=fixture(settings,w);s.research_packet['official_sources'][0]['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat();w.storage.save_run_state(s)
        before=s.model_dump_json();p=preflight(settings,w.storage,s)
        assert p['units'][0]['id']=='source_refresh' and p['maximum_reservation_microusd']==0
        j=job_for(w,s,p)
        with running_job(j,w.storage):r=execute(w,j)
        assert len(retrieved)==1 and calls==[] and r['status']=='incomplete'
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert r['source_verification']['sources'][0]['content_hash']=='different'


def test_uncertain_provider_request_preserves_reservations_and_blocks_replacement(settings,monkeypatch):
    import litellm
    error=litellm.Timeout(message='synthetic timeout',model='deepseek-flash',llm_provider='deepseek')
    calls=provider(monkeypatch,failure=error)
    with Workflow(settings) as w:
        s=fixture(settings,w);j=job_for(w,s,preflight(settings,w.storage,s))
        with running_job(j,w.storage),pytest.raises(UncertainOperation):execute(w,j)
        assert len(calls)==1 and w.storage.research_costs(str(s.run_id))['reserved_microusd']>0
        assert not preflight(settings,w.storage,s)['ready']
        other=Job(run_id=s.run_id,kind='evaluation',idempotency_key=uuid4(),reviewer='Sam',source_revision=s.revision,payload=j.payload)
        with running_job(other,w.storage),pytest.raises(UncertainOperation):execute(w,other)
        assert len(calls)==1
        j.status='paused';w.storage.save_job(j)
        with pytest.raises(ValueError):w.storage.retry_job(str(j.id))


def test_red_material_finding_is_preserved_on_resume_and_stops_dependent_image(settings,monkeypatch):
    calls=provider(monkeypatch)
    import litellm
    original=litellm.completion
    def complete(**kwargs):
        result=original(**kwargs)
        if len(calls)==2:
            payload=json.loads(kwargs['messages'][1]['content'])
            m=payload['source_modules'][0]
            result.choices[0].message.content=json.dumps({'coverage':CRITERIA,'findings':[dict(category='material',rule='contradiction',section='linkedin',quote='Sie ordnen',evidence_ref=m['id'],evidence_quote=m['text'],message_en='Synthetic contradictory assertion.',action_en='Correct the conflicting assertion before dependent generation.')],'limitations':[],'visible_text':'','unreadable_areas':[]})
        return result
    monkeypatch.setattr(litellm,'completion',complete)
    with Workflow(settings) as w:
        s=fixture(settings,w,vision=True);j=job_for(w,s,preflight(settings,w.storage,s))
        with running_job(j,w.storage):r=execute(w,j)
        assert r['status']=='corrections_required' and len(calls)==2
        # Simulate the process stopping after its unit commit, before the manifest commit.
        from src.services.evaluation import write
        damaged=dict(r);damaged['findings']=[];damaged['results']=dict(r['results']);damaged['results'].pop('social_review')
        write(w.storage,'evaluation:'+str(s.run_id)+':'+current_key(s,settings),s,damaged)
        with running_job(j,w.storage):r2=execute(w,j)
        assert r2['reuse_provenance']['social_review']['source_operation']
        assert r2['status']=='corrections_required' and len(calls)==2
        assert any(f['kind']=='red' for f in r2['findings'])


def test_worker_rejects_changed_request_contract_and_exceeded_reservation(settings,monkeypatch):
    calls=provider(monkeypatch)
    with Workflow(settings) as w:
        s=fixture(settings,w);p=preflight(settings,w.storage,s);p['units'][0]['reservation_microusd']=0
        j=job_for(w,s,p)
        with running_job(j,w.storage),pytest.raises(ValueError):execute(w,j)
        assert not calls


def test_preview_path_hash_and_permissions_are_not_bypassed(settings):
    from src.services.evaluation_vision import attachment
    with Workflow(settings) as w:
        s=fixture(settings,w);s.graphic_file=str(settings.output_dir/'outside.webp')
        with pytest.raises(ValueError):attachment(settings,s)
        s.graphic_file=str(settings.output_dir/'runs'/str(s.run_id)/'manual-assets/test.webp')
        s.graphic_sha256='0'*64
        with pytest.raises(ValueError):attachment(settings,s)


def test_failed_evaluation_never_changes_passed_article_progress(settings,monkeypatch):
    from src.services.progress import progress
    from src.services.failures import capture
    with Workflow(settings) as w:
        s=fixture(settings,w)
        j=Job(run_id=s.run_id,kind='evaluation',idempotency_key=uuid4(),reviewer='Sam',source_revision=s.revision,status='failed',error_code='schema_validation')
        w.storage.create_job(j)
        event=capture(w.storage,code='schema_validation',exc=ValueError('synthetic invalid evaluation'),job=j,state=s,component='SEO QA',operation='Review social evaluation')
        j.failure_summary=event['summary'];w.storage.save_job(j)
        p=progress(w,s)
        assert p['check_status']['seo']=='passed' and p['current_failure'] is None
        assert p['evaluation_attempt']['status']=='failed'


def test_legacy_allowance_is_not_reinterpreted_as_configured_five_dollars(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w).model_copy(deep=True,update={'run_id':uuid4(),'run_budget_usd':None,'campaign_id':None,'campaign_budget_usd':None})
        w.storage.save_run_state(s)
        assert preflight(settings,w.storage,s)['remaining_microusd']==1000000
