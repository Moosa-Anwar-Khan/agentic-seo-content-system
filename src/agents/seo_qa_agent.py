from src.orchestrator.state import RunState
import json
from bs4 import BeautifulSoup
from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.schemas.content import CheckResult
from src.services.render import topic_titles
from src.services.seo_decisions import selected_context, verified_topic_links

@register_agent("seo_qa_agent")
class SEOQAAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        if state.combined_version:
            from src.combined.pipeline import seo
            return seo(self.context,state)
        soup=BeautifulSoup(state.html,'html.parser')
        errors=[]
        warnings=['Lokale Planlinks vor WordPress-Übernahme durch bestätigte URLs ersetzen.','FAQ-Schema garantiert keine Rich Results.','Suchintention und Fachrichtigkeit benötigen menschliche Prüfung.']
        target='content_agent'
        if state.selected_recommendation:
            chosen=state.selected_recommendation
            if state.briefing.primary_keyword != chosen.keyword or state.briefing.page_type != chosen.page_type:
                errors.append('Briefing weicht vom ausgewählten Thema oder Seitentyp ab')
                target='briefing_agent'
            if state.article.primary_keyword != chosen.keyword or state.article.page_type != chosen.page_type or state.article.target_url != chosen.target_url:
                errors.append('Inhalt weicht von der bestätigten SEO-Auswahl ab')
        if len(soup.find_all('h1')) != 1: errors.append('Genau eine H1 erforderlich')
        previous=1
        for h in soup.find_all(['h1','h2','h3']):
            level=int(h.name[1])
            if level > previous+1: errors.append('Überschriftenhierarchie überspringt eine Ebene')
            previous=level
        if not soup.find('h2') or not soup.find('h3'): errors.append('H2/H3-Struktur fehlt')
        title=soup.find('title')
        desc=soup.find('meta',attrs={'name':'description'})
        if not title or not 20 <= len(title.get_text()) <= 65: errors.append('Meta-Title-Länge ungültig')
        if not desc or not 100 <= len(desc.get('content','')) <= 165: errors.append('Meta-Description-Länge ungültig')
        ids={x.get('id') for x in soup.find_all(id=True)}
        topics=topic_titles(state.topic_map)
        for a in soup.find_all('a'):
            href=a.get('href','')
            if href.startswith('#') and href[1:] not in ids: errors.append('Fehlendes internes Sprungziel')
            if href.startswith('../topic-maps/topic-map.html#') and href.split('#')[-1] not in topics: errors.append('Unbekanntes Topic-Map-Ziel')
            if not href or href=='#' or href.lower().startswith(('javascript:','data:')): errors.append('Ungültiger Link')
        if not set(state.briefing.internal_link_ids).issubset(state.article.internal_link_ids): errors.append('Interne Links aus dem Briefing fehlen')
        scripts=soup.find_all('script')
        if len(scripts)!=1 or scripts[0].get('type')!='application/ld+json': errors.append('Nur JSON-LD-Script zulässig')
        else:
            try:
                schema=json.loads(scripts[0].string or '')
                types={x['@type'] for x in schema['@graph']}
                expected_type=('BlogPosting' if state.article.page_type=='blog' else 'WebPage') if state.article.primary_keyword else 'Article'
                if not {expected_type,'FAQPage'}.issubset(types): errors.append('Passendes Seiten-/FAQ-Schema fehlt')
                faq=next(x for x in schema['@graph'] if x['@type']=='FAQPage')
                if len(faq['mainEntity']) != len(state.article.faqs): errors.append('FAQ-Schema weicht vom Text ab')
            except (ValueError,KeyError,StopIteration): errors.append('JSON-LD ist ungültig')
        if not soup.select('#beratung') or not soup.select('#faq'): errors.append('CTA oder FAQ fehlt')
        if any(k.startswith('on') for tag in soup.find_all() for k in tag.attrs): errors.append('Event-Handler im HTML verboten')
        if not errors and not self.context.settings.demo_mode:
            verified_links = verified_topic_links(state)
            semantic=self.context.llm.generate(str(state.run_id),CheckResult,
                'Check ONLY the current article and briefing against the selected intent, action and page type. Existing pages in selected_context are comparison evidence, not the draft under review. An optimization must serve the existing URL and intent; a cluster must answer the distinct reader need without duplicating the parent. Review metadata, prose, headings, FAQs and the supplied relevant link destinations. Distinguish verified URLs from planned topic IDs. Source URLs do not prove tax accuracy. Missing demand evidence is a warning for an explicitly selected provisional draft, not itself a content defect. Do not invent measurements or claim rankings, reachability or legal verification. Return German errors and warnings and a valid repair target: briefing_agent for a defective brief, content_agent for a defective draft. Passing checks is not human approval.',
                {'briefing':state.briefing.model_dump(),'article':state.article.model_dump(),'selected_context':selected_context(state),
                 'relevant_topic_links':[{'id':i,'title':topics.get(i),'verified_url':verified_links.get(i)}
                                        for i in state.article.internal_link_ids],
                 'deterministic_errors':errors,'supplied_source_urls':state.article.source_urls}, model_role='seo_qa')
            if not semantic.passed: errors.extend(semantic.errors or ['Semantische SEO-Prüfung nicht bestanden'])
            warnings.extend(semantic.warnings)
            if not semantic.passed: target=semantic.target
        state.seo_check=CheckResult(passed=not errors,errors=list(dict.fromkeys(errors)),warnings=list(dict.fromkeys(warnings)),target=target)
        if not errors: state.status='seo_check_passed'
        return state
