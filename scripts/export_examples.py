from pathlib import Path
from src.config.settings import Settings
from src.orchestrator.state import RunState
from src.schemas.icp import icp_1_buy_and_hold
from src.schemas.content import TopicMap, Briefing, Article
from src.services.render import render_article
from src.services.export import LocalExporter

def main():
    settings=Settings(demo_mode=True)
    state=RunState(icp=icp_1_buy_and_hold)
    state.topic_map=TopicMap.model_validate_json((settings.input_dir/'fixtures/topic-map.json').read_text(encoding='utf-8'))
    state.briefing=Briefing.model_validate_json((settings.input_dir/'fixtures/briefing.json').read_text(encoding='utf-8'))
    state.article=Article.model_validate_json((settings.input_dir/'fixtures/article.json').read_text(encoding='utf-8'))
    state.html=render_article(state.article,state.topic_map)
    state.status='draft_created'
    LocalExporter(settings.output_dir).write_bundle(state,settings.output_dir/'examples')
    print('Offline example artifacts written; human approval pending.')
if __name__=='__main__': main()
