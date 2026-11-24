"""Offline geometry regressions across output templates; no provider requests."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tests.conftest  # Strip credentials and prohibit non-loopback networking.
import argparse,json,tempfile
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright
from src.config.settings import Settings
from src.orchestrator.workflow import Workflow
from src.combined.editorial import render
from src.services.editorial_brand import branded_document
from src.services.presentation import render_current
from src.services.render import render_article,render_topic_map,render_recommendations
from src.services.website_package import start_page
from src.services.assistant_review import render_current_review
from src.schemas.content import Article,TopicMap
from tests.branding_examples import representative_draft
from tests.test_solo_evaluation import fixture


GEOMETRY=r'''()=>{
 const visible=e=>e.getClientRects().length&&getComputedStyle(e).display!=='none';
 const rect=e=>e.getBoundingClientRect();const errors=[],gaps=[];
 const check=(ok,kind,actual)=>{if(!ok)errors.push({kind,actual});};
 for(const group of document.querySelectorAll('.rs-actions')){
  if(!visible(group))continue;
  let previous=group.previousElementSibling;
  while(previous&&!visible(previous))previous=previous.previousElementSibling;
  if(previous){const gap=rect(group).top-rect(previous).bottom;gaps.push({kind:'before-actions',gap});check(gap>=23.5,'before-actions',gap);}
  const buttons=[...group.querySelectorAll('a.rs-cta,a.button')].filter(visible);
  for(let i=0;i<buttons.length;i++){
   const a=rect(buttons[i]),g=rect(group);
   check(a.left>=g.left-.5&&a.right<=g.right+.5,'button-contained',buttons[i].textContent);
   check(buttons[i].scrollWidth<=buttons[i].clientWidth+1&&buttons[i].scrollHeight<=buttons[i].clientHeight+1,'button-clipping',buttons[i].textContent);
   for(let j=i+1;j<buttons.length;j++){
    const b=rect(buttons[j]);const sameLine=Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)+.5;
    const gap=sameLine?Math.max(a.left,b.left)-Math.min(a.right,b.right):Math.max(a.top,b.top)-Math.min(a.bottom,b.bottom);
    gaps.push({kind:'between-actions',gap});check(gap>=15.5,'between-actions',gap);
   }
  }
 }
 for(const figure of document.querySelectorAll('figure')){
  const image=figure.querySelector(':scope>img'),caption=figure.querySelector(':scope>figcaption');
  if(image&&caption&&visible(image)){const gap=rect(caption).top-rect(image).bottom;gaps.push({kind:'caption',gap});check(Math.abs(gap-12)<.5,'caption',gap);}
 }
 for(const container of document.querySelectorAll('.module-copy>div,.article-copy,.rs-report>section')){
  const children=[...container.children].filter(e=>visible(e)&&getComputedStyle(e).position!=='absolute');
  for(let i=1;i<children.length;i++){
   const a=children[i-1],b=children[i];
   if(['A','SPAN','STRONG','EM','STYLE','SCRIPT'].includes(a.tagName)||['A','SPAN','STYLE','SCRIPT'].includes(b.tagName))continue;
   const gap=rect(b).top-rect(a).bottom;
   check(gap>=-.5,'overlapping-flow',a.tagName+' -> '+b.tagName+': '+gap);
   // FAQ rows intentionally form one bordered accordion, with internal padding.
   if(a.matches('.rs-faq')&&b.matches('.rs-faq')){
    check(parseFloat(getComputedStyle(a).paddingBottom)>=20&&parseFloat(getComputedStyle(b).paddingTop)>=20,'faq-padding',gap);continue;
   }
   if(b.matches('.rs-actions'))continue;
   check(gap>=11.5,'flow-separation',a.tagName+' -> '+b.tagName+': '+gap);
  }
 }
 for(const card of document.querySelectorAll('.rs-card,.rs-content-card,.rs-card-grid>li')){
  const s=getComputedStyle(card);check(parseFloat(s.paddingTop)>=24&&parseFloat(s.paddingBottom)>=24,'card-padding',s.padding);
 }
 const module=document.querySelector('[data-module="L04"]');
 let serviceGap=null;
 if(module){const cards=[...module.querySelectorAll('.rs-card-grid>.rs-card')],button=module.querySelector('.rs-actions a.rs-cta');
  if(cards.length&&button){serviceGap=rect(button).top-Math.max(...cards.map(c=>rect(c).bottom));check(serviceGap>=23.5,'service-button-gap',serviceGap);}}
 check(document.documentElement.scrollWidth<=innerWidth+1,'horizontal-overflow',document.documentElement.scrollWidth-innerWidth);
 return {errors,gaps,serviceGap};
}'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'var/assessment/spacing-repair/geometry')
    parser.add_argument('--saved-article',type=Path);args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='outputs-',dir=out))
    settings=Settings(_env_file=None,demo_mode=True,combined_mode=True,seo_decisions_enabled=True,
        data_dir=work/'var',output_dir=work/'outputs',auth_encryption_key=Fernet.generate_key().decode())
    pages={};results=[];browser_errors=[];external=[]
    with Workflow(settings) as w:
        s=fixture(settings,w);before=w.storage.get_run_state(str(s.run_id)).model_dump_json()
        for kind in ['landing_page','blog']:
            draft=representative_draft(s,settings,kind)
            # Unequal cards and two long actions reproduce realistic wrapping.
            target=next(m for m in draft.modules if m.id==('L04' if kind=='landing_page' else 'S14'))
            target.markdown+='\n\n[Persönliche Beratung für Ihre offenen Fragen zur digitalen Zusammenarbeit unverbindlich anfragen](https://steuerkanzlei.example/mandant-werden/)'
            s.editorial=draft.model_dump(mode='json');pages[kind]=render_current(s,settings)
        topic=TopicMap.model_validate_json((settings.input_dir/'fixtures/topic-map.json').read_text())
        article=Article.model_validate_json((settings.input_dir/'fixtures/article.json').read_text())
        for kind in ['landing_page','blog']:
            article.page_type=kind;pages['legacy-'+kind]=render_article(article,topic)
        pages['planning']=render_topic_map(topic);pages['recommendations']=render_recommendations(s)
        pages['package']=start_page(s,'<section><h2>Your saved outputs</h2><p>Open the prepared page and channel previews.</p><a class="button" href="#article">View article</a><a class="button" href="#images">View prepared images</a></section>')
        record={'review':{'label':'Synthetic review — unapproved','notice':'Offline presentation test.',
            'observations':[{'message_en':'Synthetic observation.'}],'limitations':['No professional approval.']}}
        pages['review']=render_current_review(settings,w.storage,s,record)
        # Probe headings, cards, tables, callouts, figures and action groups in
        # each actual profile, in addition to the complete representative pages.
        import base64
        from src.services.evaluation_vision import attachment
        image=base64.b64encode(attachment(settings,s)[0]).decode()
        specimen=('<section><h2>Complete spacing example</h2><p>Existing explanatory text with an <a href="#source">ordinary inline link</a>.</p>'
            '<div class="rs-card-grid"><div class="rs-card"><h3>First card</h3><p>Short explanation.</p></div><div class="rs-card"><h3>Second card</h3><p>A longer explanation that naturally wraps across additional lines on a narrow screen.</p></div><div class="rs-card"><h3>Third card</h3><p>Another existing explanation.</p></div></div>'
            '<p><a class="button" href="#one">Prepare your questions for an individual discussion</a></p><p><a class="button" href="#two">View the existing documents checklist</a></p>'
            '<p>Follow-up guidance belongs after the action group.</p><div class="table-scroll"><table><thead><tr><th>Document</th><th>Preparation</th></tr></thead><tbody><tr><td>Existing receipts</td><td>Describe your current filing system.</td></tr></tbody></table></div>'
            '<p>Keep the original evidence.</p><blockquote class="rs-callout"><p><strong>Important:</strong> This is an offline layout example.</p></blockquote>'
            '<p><a class="button" href="#three">Open the next step</a></p><figure><img alt="Synthetic test graphic" src="data:image/webp;base64,'+image+'"><figcaption>Unchanged example caption.</figcaption></figure><h3>What remains saved</h3><p>Your work stays available.</p></section>')
        for kind in ['landing_page','blog','planning']:pages['spacing-'+kind]=branded_document('Spacing test',specimen,page_type=kind,report=True)
        if args.saved_article:pages['saved-article']=args.saved_article.read_text()
        paths={}
        for name,html in pages.items():
            path=work/(name+'.html');path.write_text(html);paths[name]=path
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            context=browser.new_context();page=context.new_page();page.set_default_timeout(5000)
            def route(r):
                if r.request.url.startswith((work.as_uri()+'/','data:','blob:','about:')):r.continue_()
                else:external.append(r.request.url);r.abort()
            context.route('**/*',route);page.on('pageerror',lambda e:browser_errors.append(str(e)))
            scenarios=[(w,1) for w in [390,600,601,768,899,900,1099,1100,1440]]+[(390,2),(1440,2)]
            for name,path in paths.items():
                for width,scale in scenarios:
                    page.set_viewport_size({'width':width,'height':1000});page.goto(path.as_uri());page.evaluate('document.fonts.ready')
                    if scale==2:
                        page.evaluate('''()=>{const items=[document.body,...document.querySelectorAll('body *')].map(e=>[e,getComputedStyle(e).fontSize,getComputedStyle(e).lineHeight]);for(const [e,size,line]of items){e.style.fontSize=parseFloat(size)*2+'px';if(line.endsWith('px'))e.style.lineHeight=parseFloat(line)*2+'px';}}''')
                    result=page.evaluate(GEOMETRY);result.update(page=name,width=width,text_scale=scale)
                    assert not result['errors'],json.dumps(result,ensure_ascii=False)
                    assert page.locator('img').evaluate_all('(items)=>items.every(i=>i.complete&&i.naturalWidth>0)')
                    results.append(result)
                    if name=='saved-article' and width in {390,768,1440} and scale==1:
                        page.locator('[data-module="L04"]').screenshot(path=str(out/('after-service-'+str(width)+'.png')))
            context.close();browser.close()
        assert not browser_errors and not external
        assert w.storage.get_run_state(str(s.run_id)).model_dump_json()==before
        assert not w.storage.list_ai_calls(str(s.run_id))
    report={'views_passed':len(results),'results':results,'browser_errors':browser_errors,'external_requests':external,'paid_requests':0}
    (out/'spacing-results.json').write_text(json.dumps(report,indent=2));print(json.dumps({'views_passed':len(results),'templates':len(pages),'paid_requests':0}))


if __name__=='__main__':main()
