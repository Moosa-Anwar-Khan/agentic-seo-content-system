"""Lossless whitespace repair only; never replace a writer's description with a template.

Unusable complete responses remain encrypted for explicit local recovery. A schema
failure never purchases another request automatically.
"""
from copy import deepcopy


def normalize_meta(value, keyword):
    if not isinstance(value, dict) or not isinstance(value.get('meta_description'), str):
        return value
    original = value['meta_description']
    if 140 <= len(original) <= 155:
        return value
    candidate = ' '.join(original.split())
    if not 140 <= len(candidate) <= 155:
        return value
    result = deepcopy(value)
    result['meta_description'] = candidate
    return result
