from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.orchestrator.state import RunState
from src.schemas.content import SEOAnalysis, SEOInterpretation
from src.schemas.decisions import ResearchCoverage
from src.services.seo_decisions import snapshot_pages, context_keywords

@register_agent("seo_strategy_agent")
class SEOStrategyAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        collection = self.context.sources.collect(state.icp.seed_topic)
        keywords, pages = collection
        state.status = "data_loaded"
        known = [p['url'] for p in pages]
        if self.context.settings.demo_mode:
            state.analysis = SEOAnalysis(data_mode="demo", keywords=keywords, existing_pages=known,
                opportunities=["Mögliche Inhalte zu Werbungskosten fachlich prüfen und in die Themenstruktur einordnen.", "Immobiliensteuerberater als möglichen Beratungseinstieg untersuchen."],
                gaps=["Thematische Abdeckung und Suchnachfrage noch anhand echter SEO-Daten prüfen."],
                limitations=["Keine Live-GSC-, DataForSEO- oder WordPress-Abfrage.", "Keine gemessenen Volumina, Schwierigkeiten, Rankings oder Wettbewerberdaten."])
        else:
            model_keywords = context_keywords(keywords, state.icp.seed_topic)
            payload = {
                'icp': state.icp.model_dump(),
                'keywords': [k.model_dump(mode='json') for k in model_keywords],
                'public_pages': [{**p, 'excerpt': p.get('excerpt', '')[:1200],
                    'content_truncated': p.get('content_truncated', True) or len(p.get('excerpt', '')) > 1200}
                    for p in pages[:15]],
                'coverage': getattr(collection, 'coverage', ResearchCoverage()).model_dump(mode='json'),
                'context_limits': {'keyword_rows_sent': len(model_keywords), 'keyword_rows_saved': len(keywords),
                    'keyword_rows_omitted': max(0, len(keywords) - len(model_keywords)),
                    'pages_sent': min(15, len(pages)), 'pages_saved': len(pages), 'characters_per_page': 1200},
                'supplied_source_urls': known,
            }
            interpretation = self.context.llm.generate(str(state.run_id), SEOInterpretation,
                "Analyze only the supplied ICP, GSC query/page measurements, DataForSEO demand and public page excerpts. Return only opportunities, gaps and limitations: at most five concise findings per field, each under 200 characters. Never reproduce keyword rows, page inventories or metrics as output fields; application code retains those originals. Distinguish observations from hypotheses and missing evidence. Cite supplied keywords or URLs within findings when relevant. Do not recommend rewriting high-ranking pages or treat low CTR as a universal defect. Return compact German JSON within the output budget; recommendations are validated later.",
                payload, model_role='seo_strategy')
            state.analysis = SEOAnalysis(data_mode='live', keywords=keywords, existing_pages=known,
                                        **interpretation.model_dump())
            # Keep provider metrics immutable: the LLM may only interpret them.
            state.analysis.keywords = keywords
            state.analysis.existing_pages = known
            state.analysis.data_mode = "live"
            state.analysis.limitations.append("Der Modellkontext nutzt höchstens 50 Keyword-Zeilen und 15 Seitenauszüge. Alle gelieferten Messwerte bleiben im Analyseexport erhalten; kein vollständiger Wettbewerber-Crawl.")
        state.analysis.page_evidence = snapshot_pages(pages, self.context.settings.wordpress_base_url)
        state.analysis.coverage = getattr(collection, 'coverage', ResearchCoverage())
        if len(state.analysis.page_evidence) != len(pages):
            state.analysis.coverage.inventory_complete = False
            state.analysis.coverage.limitations.append('Einige gelieferte URLs konnten nicht als vorhandene Kanzleiseiten bestätigt werden.')
        state.analysis.limitations.extend(state.analysis.coverage.limitations)
        if state.combined_version:
            from src.combined.research import collect_strategy
            collect_strategy(self.context,state)
        state.status = "seo_analysis_done"
        return state
