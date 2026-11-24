import json
from copy import deepcopy
import pytest
from src.schemas.content import TopicMap
from src.services.llm import ModelRouter,ModelRequestError
from src.services.model_routes import resolve_route


def test_complete_paid_map_can_recover_unicode_ids_without_changing_topics_or_edges(settings):
    value=json.loads((settings.input_dir/'fixtures/topic-map.json').read_text())
    previous=value['pillars'][0]['clusters'][0]['id'];bad='nießbrauch-uebertragung'
    value['pillars'][0]['clusters'][0]['id']=bad
    for edge in value['links']:
        for side in ['source','target']:
            if edge[side]==previous:edge[side]=bad
    before=deepcopy(value)
    result=ModelRouter._validate(resolve_route(settings,'topic_map'),TopicMap,json.dumps(value,ensure_ascii=False),'stop')
    assert result.pillars[0].clusters[0].id=='niessbrauch-uebertragung'
    assert result.pillars[0].clusters[0].title==before['pillars'][0]['clusters'][0]['title']
    assert len(result.links)==len(value['links'])
    assert value==before
    for source,target in zip(value['links'],result.links):
        assert source['anchor']==target.anchor and source['reason']==target.reason
    with pytest.raises(ModelRequestError) as error:
        ModelRouter._validate(resolve_route(settings,'topic_map'),TopicMap,json.dumps(value),'length')
    assert error.value.code=='output_limit'


def test_collisions_and_unknown_edges_still_fail_strict_validation(settings):
    value=json.loads((settings.input_dir/'fixtures/topic-map.json').read_text())
    value['pillars'][0]['clusters'][0]['id']='a_b';value['pillars'][0]['clusters'][1]['id']='a-b'
    with pytest.raises(ModelRequestError):ModelRouter._validate(resolve_route(settings,'topic_map'),TopicMap,json.dumps(value),'stop')
    value=json.loads((settings.input_dir/'fixtures/topic-map.json').read_text())
    value['links'][0]['target']='made-up'
    with pytest.raises(ModelRequestError):ModelRouter._validate(resolve_route(settings,'topic_map'),TopicMap,json.dumps(value),'stop')
