"""Presentation/export regressions: no paid rewrite, state change or false readiness."""
import hashlib
import io
import json
import zipfile
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.combined.editorial import render
from src.combined.models import EditorialOutput, Module
from src.services.editorial_brand import PROFILE_VERSION, action_groups, design_contract, profile, validate_branded_document
from src.services.evaluation import package
from src.services.presentation import render_current, design_status, WORDPRESS_NOTICE
from src.services.professional_review import fingerprint
from src.services.website_package import files
from src.services.qa_units import plan
from src.orchestrator.workflow import Workflow
from tests.test_solo_evaluation import fixture
from tests.conftest import authenticated_headers, own_fixture


@pytest.fixture(autouse=True)
def combined(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True


def test_profiles_follow_later_guides_and_do_not_share_legacy_tokens():
    landing=profile('landing_page');blog=profile('blog')
    assert (landing['primary'],landing['navy'],landing['text'])==('#24569A','#1B2436','#4A5568')
    assert (blog['primary'],blog['heading'],blog['text'])==('#1F5C99','#1B1A18','#55524d')
    assert landing['line_height']==1.6 and blog['line_height']==1.7 and blog['copy_width']==720
    assert 80<=landing['section_desktop']<=120 and 56<=landing['section_mobile']<=72
    assert landing['card_radius']==12 and landing['button_radius']==8
    landing['primary']='wrong'
    assert profile('landing_page')['primary']=='#24569A'
    assert design_contract()['spacing']=={'component':24,'actions':16,'caption':12,'copy':20}


def test_action_groups_preserve_inline_links_text_order_ids_and_are_idempotent():
    body=('<p>Ein normaler <a href="https://example.org/guide">Ratgeber</a> bleibt im Text.</p>'
          '<div class="rs-card-grid"><div class="rs-card">Unveränderte Karte</div></div>'
          '<p><a class="rs-cta" href="#anfrage">Anfrage</a></p>\n'
          '<p><a class="button" href="#kontakt">Kontakt</a></p>'
          '<p id="behalten"><a class="button" href="#telefon">Telefon</a></p>'
          '<div><a class="button" href="#eins">Eins</a> oder <a class="button" href="#zwei">Zwei</a></div>'
          '<p>Ihre <a class="rs-cta" href="#inline">bestehende Anfrage</a> im Satz.</p>')
    result=action_groups(body);original=BeautifulSoup(body,'html.parser');doc=BeautifulSoup(result,'html.parser')
    assert original.get_text(' ',strip=True)==doc.get_text(' ',strip=True)
    assert [(a['href'],a.get_text()) for a in original.select('a')]==[(a['href'],a.get_text()) for a in doc.select('a')]
    assert len(doc.select('.rs-card-grid+div.rs-actions>a'))==2
    assert doc.select_one('div#behalten.rs-actions')
    assert doc.select_one('p>a[href="#inline"]') and not doc.select('.rs-actions a[href="https://example.org/guide"]')
    assert not doc.select('.rs-actions .rs-actions')
    assert action_groups(result)==result


@pytest.mark.parametrize('page_type',['blog','landing_page'])
def test_complete_component_examples_preserve_words_links_and_detect_missing_markup(settings,page_type):
    from collections import Counter
    import markdown,re
    from tests.branding_examples import representative_draft
    with Workflow(settings) as w:
        state=fixture(settings,w);draft=representative_draft(state,settings,page_type)
        before=draft.model_dump_json();html=render(draft);doc=BeautifulSoup(html,'html.parser')
        assert validate_branded_document(html,page_type)==[] and draft.model_dump_json()==before
        for module in draft.modules:
            if module.id=='S19':continue
            original=BeautifulSoup(markdown.markdown(module.markdown,extensions=['tables','sane_lists']),'html.parser')
            actual=BeautifulSoup(str(doc.select_one('[data-module="'+module.id+'"] .module-copy>div')),'html.parser')
            for figure in actual.select('figure'):figure.decompose()
            words=lambda node:Counter(re.findall(r'\w+',node.get_text(' ',strip=True)))
            assert words(original)==words(actual),module.id
            assert Counter(a['href'] for a in original.select('a[href]'))==Counter(a['href'] for a in actual.select('a[href]'))
        if page_type=='landing_page':
            assert doc.select_one('.rs-eyebrow').find_next_sibling().name=='h1'
            assert doc.select_one('.rs-fee-badge').get_text()=='StBVV'
            assert len(doc.select('.rs-key-figures>li'))==3
            assert len(doc.select('[data-module="L09"] .rs-content-card'))==3
        else:
            assert doc.select_one('.rs-breadcrumb') and doc.select_one('.rs-meta') and doc.select_one('.rs-lead')
            assert len(doc.select('.rs-callout p>strong:first-child'))==4
            assert len(doc.select('.rs-practice-quote'))==1
            doc.select_one('.rs-meta')['class']=[]
            assert any('S03' in message for message in validate_branded_document(str(doc),'blog'))


@pytest.mark.parametrize('page_type',['blog','landing_page'])
def test_rendered_components_use_one_profile_without_changing_saved_content(settings,page_type):
    from src.combined.editorial import prompt_rules
    visual=prompt_rules(settings,page_type)['visual_contract']
    assert visual['version']==PROFILE_VERSION and visual['profile']==profile(page_type)
    with Workflow(settings) as w:
        s=fixture(settings,w);draft=EditorialOutput.model_validate(s.editorial);draft.page_type=page_type
        draft.modules=[Module(id='S02' if page_type=='blog' else 'L01',markdown='# Ein Test für Ihre Beratung\n\nSie behalten den Überblick.\n\n[Beratung anfragen](https://steuerkanzlei.example/mandant-werden/)'),
            Module(id='S03',markdown='## Stand des Artikels\n\nUnveränderte Metazeile.'),
            Module(id='S08',markdown='## Unterlagen\n\n| Beleg | Zweck |\n|---|---|\n| Rechnung | Prüfung |'),
            Module(id='S09',markdown='## Unterlagen\n\n> Wichtig: Ihre gespeicherten Belege bleiben erhalten.'),
            Module(id='S14' if page_type=='blog' else 'L07',markdown='## Ablauf\n\n1. Unterlagen sammeln\n2. Beratung vorbereiten'),
            Module(id='S17' if page_type=='blog' else 'L15',markdown='## Ihr nächster Schritt\n\n[Beratung anfragen](https://steuerkanzlei.example/mandant-werden/)'),
            Module(id='S18',markdown='## Weiteres\n\nZusätzliche Hinweise für Ihr Gespräch.'),
            Module(id='S19',markdown='## Template-footer\n\nDieser Bereich gehört ausschließlich zum Website-Theme.')]
        before=draft.model_dump_json();html=render(draft);doc=BeautifulSoup(html,'html.parser')
        assert validate_branded_document(html,page_type)==[] and draft.model_dump_json()==before
        assert doc.html['data-brand-version']==PROFILE_VERSION
        assert len(doc.find_all('h1'))==1 and not doc.find(attrs={'data-module':'S19'})
        ids=[t['id'] for t in doc.select('[id]')];assert len(ids)==len(set(ids))
        assert doc.select_one('.table-scroll[tabindex="0"] table')
        assert doc.select_one('.rs-steps') and doc.select_one('.rs-final-cta a.rs-cta')
        assert not doc.find('link',rel='stylesheet')
        assert all(x.get('src','').startswith('data:') for x in doc.find_all('img'))
        if page_type=='blog':assert doc.select_one('details.rs-toc-mobile') and doc.select_one('.rs-toc-desktop')
        else:assert doc.select_one('.rs-hero-photo')


def test_current_preview_and_zip_are_identical_and_preserve_checks_approvals_costs(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);before=s.model_dump_json();qa=plan(s,settings);attestation=fingerprint(s)
        content=render_current(s,settings)
        with zipfile.ZipFile(io.BytesIO(package(settings,w.storage,s))) as z:
            assert z.read('article.html').decode()==content==z.read('website/preview.html').decode()
            assert not BeautifulSoup(content,'html.parser').select('mark')
            info=json.loads(z.read('export-manifest.json'))
            assert info['branding_version']==PROFILE_VERSION and info['revision']==s.revision
            assert not info['upload_ready'] and info['wordpress']['status']=='unverified'
            assert info['wordpress']['target_template'] is None
            assert WORDPRESS_NOTICE in z.read('START_HERE.html').decode()
            for path,record in info['artifacts'].items():
                assert hashlib.sha256(z.read(path)).hexdigest()==record['sha256']
            for asset in info['assets']:
                assert hashlib.sha256(z.read('website/'+asset['path'])).hexdigest()==asset['sha256']
            doc=BeautifulSoup(z.read('topic-maps/topic-map.html'),'html.parser')
            assert doc.html['data-page-type']=='planning' and 'keine fertig' in doc.get_text()
            assert 'Cabin' in doc.style.string and 'Montserrat' in doc.style.string
        assert s.model_dump_json()==before and w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert qa==plan(s,settings) and fingerprint(s)==attestation
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_elementor_candidate_has_scoped_content_no_site_parts_or_remote_media(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);s.briefing.target_url='https://steuerkanzlei.example/zielgruppen/einzelunternehmen/'
        out=files(s,settings)
        metadata=json.loads(out['website/metadata.json'])
        assert metadata['target_url']==s.briefing.target_url and metadata['preserve_existing_url']
        assert all('website/assets/'+name in out for name in ['cabin-OFL.txt','montserrat-OFL.txt','robotoslab-LICENSE.txt','brand-sources.json'])
        candidate=json.loads(out['website/elementor-content.UNVERIFIED.json'])
        container=candidate['content'][0];widget=container['elements'][0]
        assert candidate['version']=='0.4' and not candidate['page_settings']
        assert container['elType']=='container' and widget['widgetType']=='html'
        fragment=BeautifulSoup(widget['settings']['html'],'html.parser')
        assert fragment.select_one('main.rs-content') and fragment.select_one('div.rs-document')
        assert not fragment.select('.rs-brand-header,.site-header,.site-footer,script')
        assert fragment.select_one('h1') and 'Testentwurf' in fragment.get_text()
        assert not fragment.select('img[src^="http"],link')
        assert all(part in out['website/UPLOAD_INSTRUCTIONS.txt'] for part in ['UNAPPROVED','GENERIC CANDIDATE',WORDPRESS_NOTICE])


def test_stale_revision_and_modified_graphic_are_never_silently_exported(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);before=design_status(s);s.revision+=1
        assert design_status(s)['revision']!=before['revision']
        out=files(s,settings)
        assert json.loads(out['export-manifest.json'])['infographic']=='unavailable_or_invalid'
        assert not any(name.endswith('.webp') and 'infografik' in name for name in out)
        assert not BeautifulSoup(out['website/preview.html'],'html.parser').select('figure img')
        assert not w.storage.list_ai_calls(str(s.run_id))


def test_outdated_graphic_notes_remain_separate_from_branding_and_never_rewrite_content(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w)
        s.editorial['modules'][0]['markdown']+='\n\nEine Bilddatei liegt diesem Entwurf nicht bei.'.replace('Eine','eine',1)
        s.editorial['graphic']['caption']='Grafikentwurf zur manuellen Erstellung und Freigabe.'
        before=s.model_dump_json();info=design_status(s)
        assert info['branding']['status']=='applied' and len(info['content_notes'])==2
        assert info['wordpress']['status']=='unverified' and info['approval']=='unapproved'
        assert s.model_dump_json()==before
        s.graphic_revision=s.revision-1
        assert not design_status(s)['content_notes']


def test_unsafe_markdown_cannot_add_active_content_or_external_images(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w);draft=EditorialOutput.model_validate(s.editorial)
        draft.modules[0].markdown+='\n\n<script>alert(1)</script> ![remote](https://evil.example/x) [bad](javascript:alert(1)) [malformed](https://[broken)'
        page=BeautifulSoup(render(draft),'html.parser')
        assert not page.select('script:not([type="application/ld+json"]),img[src^="http"],a[href^="javascript:"]')


def test_authenticated_preview_download_and_progress_agree_without_mutating_state(settings):
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();headers=authenticated_headers(client,'Solo');w=client.app.state.workflow
        s=fixture(settings,w);own_fixture(client,s);w.storage.save_run_state(s)
        before=w.storage.get_run_state(str(s.run_id)).model_dump_json();base=f'/runs/{s.run_id}'
        article=client.get(base+'/article');assert article.status_code==200
        progress=client.get(base+'/progress').json();assert progress['design_status']['wordpress']['message']==WORDPRESS_NOTICE
        assert 'font-src data:' in client.get(base+'/topic-map').headers['content-security-policy']
        result=client.get(base+'/evaluation').json();assert result['design_status']==progress['design_status']
        with zipfile.ZipFile(io.BytesIO(client.get(base+'/evaluation/download').content)) as z:
            assert z.read('article.html').decode()==article.text
        response=client.get(base+'/evaluation/download')
        assert '-brand-'+PROFILE_VERSION+'.zip' in response.headers['content-disposition']
        assert response.headers['cache-control']=='no-store'
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert not w.storage.list_ai_calls(str(s.run_id))
        authenticated_headers(client,'Stranger')
        assert client.get(base+'/article').status_code==404
        assert client.get(base+'/evaluation/download').status_code==404
