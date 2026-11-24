from src.combined.evidence import source_issue as source_eligibility_issue
from src.services.link_policy import contract as link_contract
from src.services.review_contract import contract as review_contract
from src.orchestrator.state import RunState
import json
from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.schemas.content import Briefing, Heading, SelectedBriefing
from src.services.seo_decisions import selected_context
from src.services.editorial_brand import brand_for_agents

@register_agent("briefing_agent")
class BriefingAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        if state.combined_version:
            from src.services.preflight import ensure_ready
            ensure_ready(self.context,state,'research')
            from src.combined.research import prepare_brief
            prepare_brief(self.context,state)
            # Preserve complete evidence even if input validation or generation fails.
            self.context.storage.save_run_state(state)
            ensure_ready(self.context,state,'briefing')
        if not state.topic_map:
            raise ValueError("Topic map missing")
        if self.context.settings.demo_mode:
            previous = state.briefing
            state.briefing = previous.model_copy(deep=True) if previous else Briefing.model_validate_json((self.context.settings.input_dir/'fixtures/briefing.json').read_text(encoding='utf-8'))
            if state.selected_recommendation and previous is None:
                chosen = state.selected_recommendation
                state.briefing.title = chosen.title
                state.briefing.primary_keyword = chosen.keyword
                state.briefing.page_type = chosen.page_type
                state.briefing.target_url = chosen.target_url
                state.briefing.search_intent = chosen.search_intent
                if chosen.topic_id != state.topic_map.seed_id:
                    state.briefing.secondary_keywords = []
                    state.briefing.headings = [Heading(h2=h, h3=['Ihre Vorbereitung'], guidance='Demo-Gliederung; keine neuen steuerlichen Tatsachen oder Berechnungen erfinden.') for h in
                        [f'{chosen.title}: Fragen und Orientierung', 'Unterlagen nachvollziehbar vorbereiten', 'Offene Fragen für die Beratung', 'Ihr persönliches Gespräch vorbereiten']]
                    state.briefing.faq_questions = [f'Welche Fragen sind zu {chosen.title} zu klären?', 'Welche Unterlagen sind hilfreich?', 'Warum ist eine individuelle Prüfung erforderlich?']
                state.briefing.editorial_notes.append('Demo-Beispiel zum ausdrücklich ausgewählten Thema; keine Live-Recherche oder individuelle Steuerberatung.')
            if state.human_feedback:
                state.briefing.editorial_notes.append("Demo-Redaktionsauftrag: " + state.human_feedback)
                if not any(h.h2=="Unterlagen für das Erstgespräch" for h in state.briefing.headings):
                    state.briefing.headings.append(Heading(h2="Unterlagen für das Erstgespräch", h3=["Objektübersicht und geplante Entscheidungen"], guidance="Allgemeine Checkliste erweitern; keine vertraulichen Angaben in SEO-Prompts."))
                # Validate dictionaries appended into nested lists before returning.
                state.briefing = Briefing.model_validate(state.briefing.model_dump())
        else:
            from src.combined.research import prompt_packet
            catalog = json.loads((self.context.settings.input_dir/'fixtures/source-catalog.json').read_text(encoding='utf-8'))
            state.briefing = self.context.llm.generate(str(state.run_id), SelectedBriefing if state.selected_recommendation else Briefing,
                "Create or revise a detailed German briefing for the selected topic and page type; use the seed service page only for legacy runs without a selection. Preserve the selected primary keyword and target URL. For optimization retain the existing intent and unaffected content. For a new cluster explain its distinct reader need and link back to the verified parent. Define audience, outcome, headings, CTA, FAQ and evidence needs. For a blog guide plan 2000-3500 words, initially 2200; the legacy 1400-word default is not the blog-guide policy. The supplied internal_link_policy overrides conflicting link quantities in preserved brand references only; retain all provenance and relevance requirements. Use only topic-map IDs. Flag missing evidence; source URLs do not prove claims. Describe permitted revisions only in supported schema fields.",
                {"review_contract":review_contract(state,self.context.settings),"icp":state.icp.model_dump(),"brand":brand_for_agents(self.context.settings),"internal_link_policy":link_contract(state),"topic_map":state.topic_map.model_dump(),"selected_context":selected_context(state),"previous":state.briefing.model_dump() if state.briefing else None,"feedback":self.human_feedback_prompt(state),"research":prompt_packet(state) if state.combined_version else {},
                 "supplied_source_urls":[s['url'] for s in state.research_packet.get('official_sources',[]) if not source_eligibility_issue(s,self.context.settings.tax_year)] if state.combined_version else catalog['urls']}, model_role='briefing')
        if state.selected_recommendation:
            chosen = state.selected_recommendation
            if state.briefing.primary_keyword != chosen.keyword or state.briefing.page_type != chosen.page_type:
                raise ValueError('Briefing changed the selected keyword or page type')
            if state.briefing.target_url not in {None, chosen.target_url}:
                raise ValueError('Briefing invented a target URL')
            state.briefing.target_url = chosen.target_url
        if state.combined_version and state.briefing.page_type=='blog':
            if state.briefing.suggested_words<2000:state.briefing.suggested_words=2200
            elif state.briefing.suggested_words>3500:state.briefing.suggested_words=3500
        state.status = "briefing_done"
        return state
