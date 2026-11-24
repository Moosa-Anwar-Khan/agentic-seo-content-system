"""A fresh combined run exercises every gate with synthetic sources and identities."""
import pytest
import base64
import json
from pathlib import Path
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from src.api.app import create_app
from tests.combined_harness import install_combined_fakes
from tests.conftest import authenticated_headers


@pytest.mark.parametrize('upgraded_writer',[False,True])
@pytest.mark.parametrize('expanded',[False,True])
def test_fresh_combined_journey_research_to_reviews_export_social_and_optional_images(settings,monkeypatch,upgraded_writer,expanded):
    fake=install_combined_fakes(settings,monkeypatch)
    # Full release journey with NO testimonials, not just a pre-populated success screen.
    import src.combined.editorial as editorial
    original_rules=editorial.rules
    def without_reviews(current_settings,page_type):
        pack=original_rules(current_settings,page_type)
        pack['approved_reviews']['reviews']=[]
        return pack
    monkeypatch.setattr(editorial,'rules',without_reviews)
    assert not any(m['id']=='L12' for m in fake['sample'].editorial['modules'])
    if expanded:
        from src.integrations.google_search_console import GoogleSearchConsole
        from src.integrations.website_scan import WebsiteScan,extract_page
        from src.integrations.dataforseo import DataForSEO
        from src.schemas.content import Keyword
        from datetime import datetime,timezone
        def window(self,start,end,dimensions,country='all',urls=None):
            fake['sources']['gsc']+=1
            keys=['immobiliensteuerberater',settings.wordpress_base_url+'/zielgruppen/immobilien/'] if dimensions==['query','page'] else [start.isoformat()] if dimensions==['date'] else [settings.wordpress_base_url+'/zielgruppen/immobilien/'] if dimensions else []
            return {'available':True,'complete':True,'rows':[{'keys':keys,'clicks':2,'impressions':150,'ctr':2/150,'position':12}],'aggregation':'byProperty','retrieved_at':datetime.now(timezone.utc).isoformat()}
        def website(self,extra_urls=()):
            fake['sources']['wordpress']+=1
            excerpt='Immobiliensteuerberater begleiten Ihre Vermietung. Unterlagen werden im persönlichen Gespräch behandelt.'
            pages=[extract_page('<h1>Immobiliensteuerberater</h1><p>'+excerpt+'</p>',settings.wordpress_base_url+path) for path in ['/zielgruppen/immobilien/','/blog/test-eins/','/blog/test-zwei/']]
            return pages,{'inventory_complete':True,'content_complete':True,'pages_retrieved':3,'limitations':[]}
        def data(self,shortlist,seeds,**kwargs):
            fake['sources']['dataforseo']+=1
            return [Keyword(keyword='immobiliensteuerberater',volume=120,source='DataForSEO synthetic',measurement_context={'location_code':2276,'language_code':'de'})],[],[{'retrieved_at':datetime.now(timezone.utc).isoformat()}]
        monkeypatch.setattr(GoogleSearchConsole,'query_window',window)
        monkeypatch.setattr(WebsiteScan,'collect',website)
        monkeypatch.setattr(DataForSEO,'research',data)
    if upgraded_writer:
        settings.briefing_provider=settings.content_provider='openai'
        settings.briefing_model=settings.content_model='gpt-6.1-sol'
        settings.openai_api_key='synthetic-only'
        rates=json.loads(settings.research_price_rates)
        rates['openai/gpt-6.1-sol']=json.loads(Path('src/config/research-pricing.json').read_text())['openai/gpt-6.1-sol']
        settings.research_price_rates=json.dumps(rates)
    settings.image_generation_enabled=True;settings.image_generation_model='gpt-image-2.5-flare'
    settings.image_generation_provider='openai';settings.openai_api_key='synthetic-only';settings.image_request_max_usd=.01
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Sam')
        def queue(kind,**values):
            body={'kind':kind,'idempotency_key':str(uuid4()),**values}
            response=client.post('/jobs',headers=headers,json=body);assert response.status_code==202,response.text
            assert client.post('/jobs',headers=headers,json=body).json()['id']==response.json()['id']
            assert client.app.state.worker.process_one()
            job=client.get('/jobs/'+response.json()['id']).json();assert job['status']=='completed',json.dumps({'job_error':job['error_code'],'findings':__import__('src.services.preflight',fromlist=['inspect']).inspect(client.app.state.workflow,client.app.state.workflow.get(job['run_id']))['findings']})
            return job['run_id']
        allowance=1 if upgraded_writer else .25
        run_id=queue('start',icp_id='icp_1_buy_and_hold',**({'research_scope':'site'} if expanded else {}),run_budget_usd=allowance,internal_link_policy='starter_library' if upgraded_writer else 'strict')
        base='/runs/'+run_id;run=client.get(base).json()
        assert run['status']=='awaiting_selection' and run['run_budget_usd']==allowance
        choice=next(item for item in run['analysis']['recommendations'] if item['topic_id']=='immobiliensteuerberater')
        queue('selection',run_id=run_id,selection={'recommendation_id':choice['id'],'expected_revision':run['revision'],
            'mode':'draft','acknowledge_uncertainty':True,'notes':'Synthetische Prüfung der vollständigen Verarbeitung.'})
        run=client.get(base).json();assert run['status']=='seo_check_passed',run.get('error')
        report=client.get(base+'/progress').json();assert report['qa']['complete'] and report['qa']['completed']==report['qa']['total']
        assert client.post(base+'/reviews',headers=headers,json={'action':'approved','expected_revision':run['revision']}).status_code==409
        graphic=BytesIO();Image.new('RGB',(1200,600),'#dce8f5').save(graphic,format='WEBP')
        assert client.post(base+'/graphic',headers=headers,json={'content_base64':base64.b64encode(graphic.getvalue()).decode(),'expected_revision':run['revision']}).status_code==200
        auth=client.app.state.auth
        adviser=auth.store.create_user('qualified-test@example.org','Synthetic qualified adviser','tax_adviser','synthetic-password-123')
        session,csrf=auth.session(adviser['id']);client.cookies.set('review_session',session);client.cookies.set('review_csrf',csrf)
        # Explicit sharing is required even for a qualified reviewer.
        owner=auth.store.user('id',run['owner_user_id'])
        from src.security.run_access import RunAccess
        RunAccess(auth.store).grant(client.app.state.workflow.get(run_id),adviser,owner,True)
        response=client.post(base+'/professional-reviews',headers={'X-CSRF-Token':csrf},json={
            'expected_revision':run['revision'],'factual_review_confirmed':True,'graphic_review_confirmed':True,
            'notes':'Synthetische fachliche Testattestierung, keine reale Freigabe.'})
        assert response.status_code==200,response.text
        headers=authenticated_headers(client,'Sam')
        response=client.post(base+'/reviews',headers=headers,json={'action':'approved','expected_revision':run['revision'],
            'factual_review_confirmed':True,'graphic_review_confirmed':True,'manual_upload_confirmed':True})
        assert response.status_code==200,response.text
        assert response.json()['status']=='exported'
        queue('social',run_id=run_id)
        posts=client.get(base+'/social-posts').json();assert len(posts)==2
        for post in posts:assert client.post(base+'/social-posts/'+post['id']+'/reviews',headers=headers,json={'action':'approved'}).status_code==200
        assert not fake['master'].calls
        queue('image_set',run_id=run_id)
        variants=client.get(base+'/image-sets').json()[0]['variants'];assert len(variants)==3 and len(fake['master'].calls)==1
        for item in variants:
            assert client.post(base+'/images/'+item['id']+'/reviews',headers=headers,json={'action':'approved'}).status_code==200
            assert client.get(base+'/images/'+item['id']+'/file').status_code==200
        bundle=client.get(base+'/bundle');assert bundle.status_code==200
        with ZipFile(BytesIO(bundle.content)) as archive:assert any(name.endswith('.html') for name in archive.namelist())
        assert fake['calls'].count('EditorialOutput')==1 and sum(fake['calls'].count(name) for name in ['GuidedPageQAUnitResult','GuidedModuleQAUnitResult','GuidedClaimQAUnitResult'])==report['qa']['total']
        assert all(value>0 for key,value in fake['sources'].items() if not expanded or key!='competitor')
        assert client.get(base+'/progress').json()['costs']['recorded_microusd']<=250000
