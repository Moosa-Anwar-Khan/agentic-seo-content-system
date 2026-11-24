from src.orchestrator.state import RunState
from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.schemas.content import TopicMap
from src.services.seo_decisions import DecisionService, analysis_context
from src.combined.research import prompt_packet

@register_agent("topic_map_agent")
class TopicMapAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        if not state.analysis:
            raise ValueError("SEO analysis missing")
        if self.context.settings.demo_mode:
            state.topic_map = TopicMap.model_validate_json((self.context.settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
        else:
            state.topic_map = self.context.llm.generate(str(state.run_id), TopicMap,
                "Follow research_scope: site means all verified website services; icp_1 means property investors only. Create 6 to 8 distinct German pillar plans, each with at least two cluster pages or blog ideas. Prefer six pillars with two children each for this MVP. The seed_id is immobiliensteuerberater: reserve it for the seed alone and never repeat it as any pillar or child ID. All IDs must contain lowercase ASCII a-z, digits and hyphens only; transliterate German umlauts (ae, oe, ue) and ss. IDs must be globally unique. German natural language belongs in titles, keywords, intent, anchors and reasons, not in technical IDs. Keep titles and link reasons concise (under 100 characters) and return compact JSON within the output budget. For EACH pillar include seed-to-pillar AND pillar-to-seed links. For EACH child include pillar-to-child AND child-to-pillar links. Six pillars with two children need 36 directed links in total. Never link an ID to itself; every endpoint must match an existing ID exactly. Check ID uniqueness and all required reciprocal links before returning. Separate search intents and dedicated pages from secondary mentions. Repeated keywords alone do not prove cannibalization. Do not invent URLs or demand measurements. Mark unmeasured ideas and incomplete coverage as provisional; saved full metrics exceed this bounded input when disclosed.",
                {"research_scope":state.research_scope or "icp_1","icp":state.icp.model_dump(), "analysis":analysis_context(state), "research":prompt_packet(state), "supplied_source_urls":[]}, model_role='topic_map')
        state.status = "topic_map_done"
        if state.decision_workflow_version:
            state.analysis.recommendations = DecisionService(self.context).build(state)
            state.status = 'awaiting_selection'
        return state
