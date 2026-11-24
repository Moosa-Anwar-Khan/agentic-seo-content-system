"""Evaluation images exercise real accounting/recovery, with a fake image provider."""
import io,json,zipfile
from uuid import uuid4
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.orchestrator.workflow import Workflow
from src.schemas.jobs import Job
from src.services import evaluation as ev
from src.services import evaluation_images as images
from src.services.images import OpenAIImageProvider,ImageService
from src.services.jobs import JobWorker
from src.services.recovery import running_job,UncertainOperation
from src.services.preflight import PreflightBlocked
from tests.test_solo_evaluation import fixture,CRITERIA
from tests.conftest import authenticated_headers,own_fixture


@pytest.fixture(autouse=True)
def combined(settings):settings.combined_mode=True;settings.seo_decisions_enabled=True

def setup(settings,w,monkeypatch):
 s=fixture(settings,w);settings.demo_mode=False;settings.image_generation_enabled=True
 settings.image_generation_provider='openai';settings.image_generation_model='gpt-image-2.5-flare'
 settings.image_request_max_usd=.25;settings.image_quality='medium';settings.image_prompt_enabled=False
 caps=json.loads(settings.ai_model_capabilities);caps['openai/gpt-image-2.5-flare']={'image_generation':True};settings.ai_model_capabilities=json.dumps(caps)
 social={'linkedin':'Sie bereiten Ihre Fragen für das Gespräch vor.','google_business_profile':'Sie ordnen Ihre Unterlagen für das Beratungsgespräch.'}
 review={'coverage':CRITERIA,'findings':[],'limitations':['Synthetic only.'],'visible_text':'','unreadable_areas':[]}
 context=ev.current_key(s,settings)
 for name,result in [('social_generate',social),('social_review',review)]:ev.write(w.storage,ev.unit_key(s,context,name),s,{'accepted':True,'result':result})
 ev.write(w.storage,'evaluation:'+str(s.run_id)+':'+context,s,{'status':'incomplete','results':{'social_generate':social,'social_review':review},'findings':[{'kind':'blue','section':'infographic'}]})
 calls=[]
 def generate(self,prompt,size):
  calls.append((prompt,size));out=io.BytesIO();Image.new('RGB',size,'#1f5c99').save(out,'PNG');return out.getvalue(),.01
 monkeypatch.setattr(OpenAIImageProvider,'generate',generate)
 return s,calls


def job(w,s,report):
 j=Job(run_id=s.run_id,reviewer='Solo',kind='evaluation_images',idempotency_key=uuid4(),source_revision=s.revision,payload={'source_revision':s.revision,'image_plan':report})
 w.storage.create_job(j);return j


def test_one_request_three_images_export_and_reuse(settings,monkeypatch):
 with Workflow(settings) as w:
  s,calls=setup(settings,w,monkeypatch);original=w.storage.get_run_state(str(s.run_id)).model_dump_json()
  report=images.preflight(settings,w.storage,s);assert report['ready'] and report['maximum_reservation_microusd']==250000
  j=job(w,s,report)
  with running_job(j,w.storage):result=images.execute(w,j)
  assert len(calls)==1 and calls[0][1]==(2048,2048)
  for platform in images.PLATFORMS:
   data,item=images.file(settings,w.storage,s,platform)
   assert Image.open(io.BytesIO(data)).size==tuple(report['platforms'][platform])
  assert w.storage.research_costs(str(s.run_id))['recorded_microusd']==10000
  z=zipfile.ZipFile(io.BytesIO(ev.package(settings,w.storage,s)))
  assert len([n for n in z.namelist() if n.startswith('channel-images/')])==4
  assert 'unapproved' in z.read('channel-images/manifest.json').decode()
  with running_job(j,w.storage):assert images.execute(w,j)==result
  assert len(calls)==1 and not images.preflight(settings,w.storage,s)['ready']
  assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==original
  assert not w.storage.list_assets(str(s.run_id),'image') and not w.storage.list_assets(str(s.run_id),'master')
  with pytest.raises(ValueError):ImageService(settings,w.storage)._source(s,'website')
  s.editorial['title']+=' changed';assert images.current(settings,w.storage,s) is None


def test_changed_plan_and_failed_attempt_do_not_purchase_replacement(settings,monkeypatch):
 with Workflow(settings) as w:
  s,calls=setup(settings,w,monkeypatch);report=images.preflight(settings,w.storage,s);j=job(w,s,report)
  settings.image_quality='high'
  with running_job(j,w.storage),pytest.raises(PreflightBlocked):images.execute(w,j)
  assert not calls
  settings.image_quality='medium'
  def timeout(*args):calls.append('failed');raise TimeoutError('fake')
  monkeypatch.setattr(OpenAIImageProvider,'generate',timeout)
  with running_job(j,w.storage),pytest.raises(Exception):images.execute(w,j)
  assert not images.preflight(settings,w.storage,s)['ready']
  j2=job(w,s,report)
  with running_job(j2,w.storage),pytest.raises(PreflightBlocked):images.execute(w,j2)
  assert len(calls)==1


def test_restart_processing_uses_saved_master(settings,monkeypatch):
 with Workflow(settings) as w:
  s,calls=setup(settings,w,monkeypatch);report=images.preflight(settings,w.storage,s);j=job(w,s,report)
  real=images.process_image;stable=images.identity(settings,w.storage,s)
  monkeypatch.setattr(images,'identity',lambda *args:stable)
  monkeypatch.setattr(images,'process_image',lambda *a,**k:(_ for _ in ()).throw(ValueError('fake processing failure')))
  # Inject a transient processing failure without changing the confirmed context.
  report=images.preflight(settings,w.storage,s);j.payload['image_plan']=report
  with running_job(j,w.storage),pytest.raises(ValueError):images.execute(w,j)
  monkeypatch.setattr(images,'process_image',real)
  with running_job(j,w.storage):images.execute(w,j)
  assert len(calls)==1 and images.current(settings,w.storage,s)


def test_readiness_and_private_api_confirmed_job(settings,monkeypatch):
 with TestClient(create_app(settings)) as c:
  c.app.state.worker.close();headers=authenticated_headers(c,'Solo');w=c.app.state.workflow
  s,calls=setup(settings,w,monkeypatch);own_fixture(c,s);w.storage.save_run_state(s)
  base=f'/runs/{s.run_id}/evaluation/images';r=c.get(base+'/preflight');assert r.status_code==200 and not calls
  report=r.json();body={'kind':'evaluation_images','idempotency_key':str(uuid4()),'run_id':str(s.run_id),'expected_revision':s.revision,'evaluation_plan_id':report['plan_id'],'confirm_evaluation':False}
  assert c.post('/jobs',headers=headers,json=body).status_code==409
  body['confirm_evaluation']=True
  first=c.post('/jobs',headers=headers,json=body);assert first.status_code==202,first.text
  assert c.post('/jobs',headers=headers,json=body).json()['id']==first.json()['id']
  assert JobWorker(settings,w).process_one() and len(calls)==1
  assert c.get(base+'/website').status_code==200
  assert c.get(base+'/invalid').status_code==422
  body['idempotency_key']=str(uuid4());assert c.post('/jobs',headers=headers,json=body).status_code==409
  authenticated_headers(c,'Stranger');assert c.get(base+'/website').status_code==404
  c.cookies.clear();assert c.get(base+'/preflight').status_code==401

@pytest.mark.parametrize('block',['quality','material','funds'])
def test_missing_prerequisites_never_buy_images(settings,monkeypatch,block):
 with Workflow(settings) as w:
  s,calls=setup(settings,w,monkeypatch)
  if block=='quality':s.seo_check.passed=False
  elif block=='material':
   entry=ev.latest(w.storage,s,settings);entry['findings']=[{'kind':'red','section':'linkedin'}]
   ev.write(w.storage,'evaluation:'+str(s.run_id)+':'+ev.current_key(s,settings),s,entry)
  else:settings.max_run_usd=.1
  report=images.preflight(settings,w.storage,s);assert not report['ready'] and report['reason']
  j=job(w,s,report)
  # Worker always rechecks readiness; crafting a job cannot bypass it.
  w.storage.save_run_state(s)
  with running_job(j,w.storage),pytest.raises(PreflightBlocked):images.execute(w,j)
  assert not calls
