from src.orchestrator.state import RunState
import json
from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.schemas.content import Article, Section, Subsection, SelectedArticle
from src.services.render import render_article
from src.services.seo_decisions import selected_context, verified_topic_links
from src.services.editorial_brand import brand_for_agents

@register_agent("content_agent")
class ContentAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        if state.combined_version:
            from src.combined.pipeline import content
            return content(self.context,state)
        if not state.briefing or not state.topic_map:
            raise ValueError("Briefing/topic map missing")
        if self.context.settings.demo_mode:
            state.article = state.article.model_copy(deep=True) if state.article else Article.model_validate_json((self.context.settings.input_dir/'fixtures/article.json').read_text(encoding='utf-8'))
            if state.selected_recommendation:
                chosen = state.selected_recommendation
                state.article.primary_keyword = chosen.keyword
                state.article.page_type = chosen.page_type
                state.article.target_url = chosen.target_url
                if chosen.topic_id != state.topic_map.seed_id:
                    state.article.title = state.briefing.title
                    state.article.meta_title = (chosen.title[:37] + ' | Muster & Partner')[:65]
                    state.article.meta_description = 'Erhalten Sie einen allgemeinen Überblick und bereiten Sie Ihre Fragen für ein persönliches Beratungsgespräch mit Muster & Partner vor.'
                    state.article.introduction = [f'Sie möchten Fragen zu {chosen.title} besprechen? Dieser Demo-Entwurf zeigt eine mögliche Struktur für Ihr Gespräch.',
                        'Wir prüfen Ihren konkreten Fall im persönlichen Gespräch. Dieses Beispiel enthält keine neue rechtliche Bewertung oder Berechnung.']
                    state.article.sections = [Section(id=f'thema-{i+1}', heading=h.h2,
                        paragraphs=['Sammeln Sie Ihre offenen Fragen und halten Sie die vorhandenen Unterlagen nachvollziehbar bereit.'],
                        subsections=[Subsection(heading='Ihre Vorbereitung', paragraphs=['Stimmen Sie die erforderlichen Unterlagen und den sicheren Übermittlungsweg mit uns ab.'])])
                        for i, h in enumerate(state.briefing.headings)]
                    from src.schemas.content import FAQ
                    state.article.faqs = [FAQ(question=q, answer='Wir klären die notwendigen Angaben und die individuellen Voraussetzungen im persönlichen Gespräch.') for q in state.briefing.faq_questions]
                    state.article.editorial_notes = ['Demo-Beispiel zum ausgewählten Thema; fachliche Freigabe und vollständige Recherche vor Veröffentlichung erforderlich.']
            if state.human_feedback:
                # The demo demonstrates the loop deterministically; live models interpret notes.
                state.article.sections = [s for s in state.article.sections if s.id != 'review-ergaenzung']
                state.article.sections.append(Section(id='review-ergaenzung',heading='Unterlagen für Ihr Erstgespräch',paragraphs=['Bereiten Sie eine kurze Übersicht Ihrer Mietobjekte und Ihrer geplanten Entscheidungen vor. Den Umfang der vertraulichen Unterlagen und den sicheren Übermittlungsweg stimmen Sie mit der Kanzlei ab.'],subsections=[Subsection(heading='Eine allgemeine Checkliste',paragraphs=['Die folgenden Punkte können Ihnen helfen, das Gespräch zu strukturieren.'],bullets=['Anzahl und Art der Mietobjekte allgemein beschreiben.','Geplante Käufe, Finanzierungen und Maßnahmen notieren.','Offene Fragen zur laufenden Zusammenarbeit sammeln.'])]))
                state.article.editorial_notes.append('Demonstrierte Revision: Erstgespräch-Checkliste ergänzt. Redaktionsauftrag: '+state.human_feedback)
                state.feedback_resolutions.append('Demo: Checkliste ergänzt; beliebige Freitextwünsche werden nur im Live-Modus semantisch umgesetzt.')
        else:
            brand = brand_for_agents(self.context.settings)
            state.article = self.context.llm.generate(str(state.run_id), SelectedArticle if state.selected_recommendation else Article,
                "Create or revise the complete German draft for the selected keyword, intent and page type in the briefing. Write structured values without HTML or JSON-LD. For optimization preserve unaffected existing content and qualifications; do not repurpose a protected page for another intent. A cluster must answer its distinct reader need. Use formal Sie and firm voice wir, the exact schema disclaimer and only approved services. Do not invent facts, citations, tax calculations or blanket depreciation rates. Explain technical terms and missing evidence. Implement permitted feedback transparently in supported fields.",
                {"briefing":state.briefing.model_dump(),"brand":brand,"selected_context":selected_context(state),"previous":state.article.model_dump() if state.article else None,"feedback":self.human_feedback_prompt(state),"supplied_source_urls":state.briefing.source_urls},model_role='content')
            if state.human_feedback:
                state.feedback_resolutions.extend(state.article.editorial_notes)
        if state.selected_recommendation:
            if state.article.primary_keyword != state.briefing.primary_keyword or state.article.page_type != state.briefing.page_type:
                raise ValueError('Content changed the selected keyword or page type')
            if state.article.target_url not in {None, state.briefing.target_url}:
                raise ValueError('Content invented a target URL')
            state.article.target_url = state.briefing.target_url
        state.html = render_article(state.article, state.topic_map, verified_topic_links(state),
                                    state.selected_recommendation.parent_url if state.selected_recommendation else None)
        state.brand_check = None
        state.seo_check = None
        state.status = "draft_created"
        return state
