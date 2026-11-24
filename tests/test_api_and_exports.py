from io import BytesIO
from urllib.parse import urlsplit
from zipfile import ZipFile
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from src.api.app import create_app
from tests.conftest import authenticated_headers

def test_authenticated_api_and_downloaded_link_targets(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get('/health').status_code==200
        assert client.get('/runs').status_code==401
        headers=authenticated_headers(client)
        response=client.post('/runs',headers=headers,json={'icp_id':'icp_1_buy_and_hold'})
        assert response.status_code==200
        state=response.json(); run_id=state['run_id']
        assert state['status']=='seo_check_passed'
        spoof=client.post('/runs/'+run_id+'/reviews',headers=headers,json={'action':'approved','expected_revision':0,'reviewer_name':'Sam'})
        assert spoof.status_code==422
        assert client.post('/runs/'+run_id+'/reviews',headers=headers,json={'action':'approved','expected_revision':9}).status_code==409
        response=client.get('/runs/'+run_id+'/bundle',headers=headers)
        assert response.status_code==200
        with ZipFile(BytesIO(response.content)) as archive:
            article=BeautifulSoup(archive.read('drafts-html/immobiliensteuerberater.html'),'html.parser')
            topics=BeautifulSoup(archive.read('topic-maps/topic-map.html'),'html.parser')
            for a in article.find_all('a'):
                href=a.get('href','')
                if href.startswith('../topic-maps/'):
                    assert topics.find(id=urlsplit(href).fragment) is not None
                elif href.startswith('#'):
                    assert article.find(id=href[1:]) is not None
            assert len(article.find_all('h1'))==1
            assert archive.read('reviews/state.json')
