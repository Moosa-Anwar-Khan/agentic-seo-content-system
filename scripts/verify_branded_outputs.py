"""Offline Chrome layout verification. Synthetic content; no live providers or sites."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest  # no credentials, no external sockets
import io,json,tempfile,zipfile,argparse
from types import SimpleNamespace
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright
from src.config.settings import Settings
from src.orchestrator.workflow import Workflow
from tests.branding_examples import representative_draft
from src.services.presentation import render_current
from src.services.evaluation import package
from src.services.website_package import files
from tests.test_solo_evaluation import fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'var/assessment/branding-repair');args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='layout-',dir=out))
    settings=Settings(_env_file=None,demo_mode=True,combined_mode=True,seo_decisions_enabled=True,
        data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode())
    checks=[];errors=[];external=[];measurements=[]
    with Workflow(settings) as w:
        state=fixture(settings,w);before=state.model_dump_json()
        state.editorial=representative_draft(state,settings,'landing_page').model_dump(mode='json')
        archive=zipfile.ZipFile(io.BytesIO(package(settings,w.storage,state)))
        for name in archive.namelist():
            target=work/'package'/name;assert target.resolve().is_relative_to((work/'package').resolve())
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name))
        blog=representative_draft(state,settings,'blog')
        state.editorial=blog.model_dump(mode='json');blog_path=work/'blog.html';blog_path.write_text(render_current(state,settings))
        candidate=files(state,settings)
        # A hostile surrounding page demonstrates CSS containment, not actual
        # Astra/Elementor compatibility (which remains explicitly unverified).
        outside='<h1 id="outside">Surrounding theme heading</h1>'
        fragment_path=work/'fragment.html';fragment_path.write_text('<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0}#outside{color:rgb(1,2,3);font-family:serif}</style></head><body>'+outside+candidate['website/content-fragment.html']+'</body></html>')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            context=browser.new_context()
            def route(r):
                if r.request.url.startswith((work.as_uri()+'/','data:','blob:')):r.continue_()
                else:external.append(r.request.url);r.abort()
            context.route('**/*',route)
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            for label,path in [('landing',work/'package/article.html'),('blog',blog_path),('planning',work/'package/topic-maps/topic-map.html'),('package',work/'package/START_HERE.html'),('fragment',fragment_path)]:
                for width,device in [(1440,'desktop'),(390,'mobile')]:
                    page.set_viewport_size({'width':width,'height':1000 if width==1440 else 844})
                    page.goto(path.as_uri());page.evaluate('document.fonts.ready');page.wait_for_timeout(100)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(label,device,'overflow')
                    assert page.evaluate('document.fonts.check("400 18px Cabin") && document.fonts.check("600 18px Montserrat")')
                    assert page.locator('img').evaluate_all('(items)=>items.every(i=>i.complete&&i.naturalWidth>0)'),(label,device,'images')
                    if label in {'landing','blog'}:
                        body=page.locator('body').evaluate('(e)=>({color:getComputedStyle(e).color,line:getComputedStyle(e).lineHeight})')
                        assert body['color']==('rgb(74, 85, 104)' if label=='landing' else 'rgb(85, 82, 77)')
                        assert body['line']==('28.8px' if label=='landing' else '30.6px')
                        def style(selector,prop,expected):
                            actual=page.locator(selector).first.evaluate('(e,p)=>getComputedStyle(e).getPropertyValue(p)',prop)
                            assert actual==expected,(label,device,selector,prop,actual,expected)
                            measurements.append({'page':label,'device':device,'selector':selector,'property':prop,'actual':actual,'expected':expected})
                        # Independent expectations transcribed from the original
                        # guides, rather than comparing the stylesheet to itself.
                        style('h1','font-family','Montserrat, sans-serif')
                        style('h1','font-weight','600')
                        style('.rs-cta','border-radius','8px')
                        assert page.locator('.rs-cta').first.bounding_box()['height']>=48
                        style('.rs-final-cta','background-color','rgb(27, 36, 54)' if label=='landing' else 'rgb(27, 47, 74)')
                        style('.rs-final-cta a.rs-cta','background-color','rgb(255, 255, 255)')
                        style('th','background-color','rgb(27, 36, 54)' if label=='landing' else 'rgb(27, 47, 74)')
                        style('figcaption','font-size','14px')
                        style('figcaption','color','rgb(136, 147, 165)' if label=='landing' else 'rgb(152, 149, 143)')
                        style('.rs-practice-quote','font-style','italic')
                        style('.rs-practice-quote','font-size','21px')
                        style('.rs-card-grid>li','border-radius','12px')
                        style('.rs-card-grid>li','border-top-color','rgb(211, 215, 223)' if label=='landing' else 'rgb(218, 214, 207)')
                        assert page.locator('.rs-card-grid').first.evaluate('(e)=>getComputedStyle(e).gridTemplateColumns.split(" ").length')==(2 if device=='desktop' else 1)
                        if device=='mobile':
                            assert abs(page.locator('.rs-cta').first.bounding_box()['width']-page.locator('.rs-cta').first.evaluate('(e)=>e.parentElement.clientWidth'))<1
                        if label=='landing':
                            style('.rs-eyebrow','font-size','13px');style('.rs-eyebrow','letter-spacing','1px')
                            style('.rs-eyebrow','text-transform','uppercase');style('.rs-eyebrow','color','rgb(36, 86, 154)')
                            assert page.locator('.rs-eyebrow').evaluate('(e)=>Boolean(e.nextElementSibling?.matches("h1"))')
                            style('[data-module="L02"]','padding-top','80px' if device=='desktop' else '56px')
                            style('.rs-price-section .module-copy>div','border-radius','12px')
                            style('.rs-fee-badge','border-top-color','rgb(217, 138, 28)')
                            assert page.locator('.rs-key-figures').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns.split(" ").length')==(3 if device=='desktop' else 1)
                            assert page.locator('[data-module="L09"] .rs-content-card').count()==3
                            page.locator('.rs-faq summary').first.focus();page.keyboard.press('Enter');assert page.locator('.rs-faq[open]').count()==1
                        else:
                            style('.rs-breadcrumb','font-size','14px');style('.rs-meta','color','rgb(152, 149, 143)');style('.rs-meta','font-size','14px')
                            style('.rs-lead','font-size','21px')
                            for module in ['S02','S03','S08','S09','S16']:style('[data-module="'+module+'"]','background-color','rgb(255, 255, 255)')
                            for module in ['S06','S14']:style('[data-module="'+module+'"]','background-color','rgb(242, 240, 234)')
                            style('.rs-attention','border-left-color','rgb(160, 59, 51)')
                            style('[data-module="S08"] .rs-callout:not(.rs-good)','background-color','rgb(231, 238, 248)')
                            style('[data-module="S17"] .rs-good','background-color','rgb(235, 251, 243)')
                            style('[data-module="S17"] .rs-good p','color','rgb(85, 82, 77)')
                            assert page.locator('.rs-callout>p>strong:first-child').count()==4
                            assert page.locator('[data-module="S19"]').count()==0
                            assert page.locator('.rs-article-flow .module-copy').first.bounding_box()['width']<=720
                    if label=='blog':
                        if device=='mobile':
                            page.locator('.rs-toc-mobile summary').click();assert page.locator('.rs-toc-mobile nav').is_visible()
                        else:
                            assert page.locator('.rs-toc-sidebar').evaluate('(e)=>getComputedStyle(e).position')=='sticky'
                            page.evaluate('scrollTo(0,600)');assert page.locator('.rs-toc-sidebar').bounding_box()['y']>=20;page.evaluate('scrollTo(0,0)')
                        navigation=page.locator('.rs-toc-mobile nav' if device=='mobile' else '.rs-toc-sidebar')
                        assert navigation.locator('a').evaluate_all('(items)=>items.every(a=>document.getElementById(a.hash.slice(1)))')
                        link=navigation.locator('a').first;destination=link.get_attribute('href');link.click()
                        assert 0<=page.locator(destination).bounding_box()['y']<200
                        page.evaluate('scrollTo(0,0)')
                    if label=='fragment':assert page.locator('#outside').evaluate('(e)=>getComputedStyle(e).color')=='rgb(1, 2, 3)'
                    page.screenshot(path=str(out/(label+'-'+device+'.png')),full_page=label in {'landing','blog'})
                    checks.append(label+' '+device)
            context.close();browser.close()
        assert not errors and not external
        assert w.storage.get_run_state(str(state.run_id)).model_dump_json()==before
        assert not w.storage.list_ai_calls(str(state.run_id))
    report={'passed':checks,'browser_errors':errors,'external_requests':external,'paid_requests':0,
        'component_measurements':measurements,
        'source':'Synthetic output and scoped-fragment browser tests; not an actual WordPress import.',
        'wordpress_import':'not run — private templates and test copy unavailable'}
    (out/'browser-results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
