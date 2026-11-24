"""Normalize technical graph identifiers without changing reader text or edges."""
from copy import deepcopy
import re
from src.services.image_processing import keyword_slug


def normalize_topic_ids(value):
    if not isinstance(value,dict):return value
    try:
        original=[value.get('seed_id','immobiliensteuerberater')]+[
            item['id'] for pillar in value['pillars'] for item in [pillar,*pillar['clusters']]]
        if not all(isinstance(item,str) for item in original) or len(set(original))!=len(original):return value
        mapping={item:item if re.fullmatch('[a-z0-9-]+',item) else keyword_slug(item) for item in original}
        if not all(mapping.values()) or len(set(mapping.values()))!=len(mapping):return value
        known=set(original)|set(mapping.values())
        if any(link['source'] not in known or link['target'] not in known for link in value['links']):return value
        if all(key==item for key,item in mapping.items()):return value
        result=deepcopy(value)
        if 'seed_id' in result:result['seed_id']=mapping[result['seed_id']]
        for pillar in result['pillars']:
            for item in [pillar,*pillar['clusters']]:item['id']=mapping[item['id']]
        for link in result['links']:
            link['source']=mapping.get(link['source'],link['source'])
            link['target']=mapping.get(link['target'],link['target'])
        return result
    except (KeyError,TypeError):return value  # Let the unchanged strict schema report it.
