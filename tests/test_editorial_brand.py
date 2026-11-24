import json
import pytest
from bs4 import BeautifulSoup
from src.schemas.content import Article,TopicMap
from src.services.render import render_article
from src.services.editorial_brand import validate_editorial_design,brand_for_agents,ARTICLE_CSP,PROFILE_VERSION


@pytest.mark.parametrize('kind',['landing_page','blog'])
def test_standalone_pages_use_verified_assets_without_external_resource_requests(settings,kind):
    article=Article.model_validate_json((settings.input_dir/'fixtures/article.json').read_text(encoding='utf-8'))
    article.page_type=kind;article.primary_keyword='Immobiliensteuerberater'
    topics=TopicMap.model_validate_json((settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
    html=render_article(article,topics);soup=BeautifulSoup(html,'html.parser')
    assert not validate_editorial_design(html,article)
    assert len(soup.find_all('h1'))==1
    assert all(node['src'].startswith('data:') for node in soup.select('[src]'))
    assert 'fonts.googleapis.com' not in html and not soup.find('link',rel='stylesheet')
    schema=json.loads(soup.find('script').string)
    assert schema['@graph'][0]['@type']==('BlogPosting' if kind=='blog' else 'WebPage')
    assert 'font-src data:' in ARTICLE_CSP and 'img-src data:' in ARTICLE_CSP
    assert 'script-src' not in ARTICLE_CSP and "default-src 'none'" in ARTICLE_CSP


def test_visual_tampering_blocks_brand_approval_even_when_article_text_is_unchanged(settings):
    article=Article.model_validate_json((settings.input_dir/'fixtures/article.json').read_text(encoding='utf-8'))
    topics=TopicMap.model_validate_json((settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
    html=render_article(article,topics)
    modified=html.replace('--rs-button-radius:8px','--rs-button-radius:30px')
    assert validate_editorial_design(modified,article)
    assert validate_editorial_design(html.replace('data-brand-version="'+PROFILE_VERSION+'"','data-brand-version="unknown"'),article)
    soup=BeautifulSoup(html,'html.parser');soup.select_one('.hero-photo')['src']='https://example.org/imaginary-team.jpg'
    assert any('Kanzleibildsprache' in message for message in validate_editorial_design(str(soup),article))


def test_agent_brand_context_distinguishes_content_intents_and_blocks_unverified_promises(settings):
    rules=brand_for_agents(settings)
    assert rules['editorial_guidelines']['applies_to']==['landing_page','blog']
    assert rules['editorial_guidelines']['editorial']['landing_page']!=rules['editorial_guidelines']['editorial']['blog']
    assert 'Rückmeldung in 2 Stunden' in rules['forbidden_promises']
    assert rules['disclaimer']==Article.model_validate_json((settings.input_dir/'fixtures/article.json').read_text(encoding='utf-8')).disclaimer
