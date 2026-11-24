import json
import pytest
from bs4 import BeautifulSoup
from src.schemas.content import Article,TopicMap
from src.services.render import render_article
from src.storage.postgres_storage import PostgresStorage

def test_renderer_escapes_closing_script_and_html(settings):
    article=Article.model_validate_json((settings.input_dir/'fixtures/article.json').read_text())
    topic_map=TopicMap.model_validate_json((settings.input_dir/'fixtures/topic-map.json').read_text())
    article.title='Text </script><script>alert(1)</script>'
    article.introduction[0]='<img src=x onerror=alert(1)>'
    html=render_article(article,topic_map)
    soup=BeautifulSoup(html,'html.parser')
    assert len(soup.find_all('script'))==1
    assert not soup.select('img[onerror]')
    assert all(img.get('src','').startswith('data:image/') for img in soup.find_all('img'))
    schema=json.loads(soup.find('script').string)
    assert schema['@graph'][0]['headline']==article.title

def test_postgres_refuses_missing_tls_before_connecting():
    with pytest.raises(ValueError): PostgresStorage('postgresql://localhost/test')
