"""Explicit compatibility proof for a local correction; no reviewer purchases."""
from copy import deepcopy
from pydantic import ValidationError
from src.services.quality import QualityReviewError
from src.services.qa_units import plan, cached_unit, schema_for, validate_unit, persist, retain_compatible_units


def retain(storage, before, after, settings, *, resolved_release_inputs=()):
    old_questions = before.editorial.get('unresolved_questions', [])
    new_questions = after.editorial.get('unresolved_questions', [])
    if [q for q in old_questions if q not in resolved_release_inputs] != new_questions:
        raise ValueError('Only the explicitly verified resolved release inputs may be removed')
    previous = {u['id']: u for u in plan(before, settings)[1]}
    for unit in plan(after, settings)[1]:
        old = previous.get(unit['id'])
        if not old or unit['kind'] == 'page':
            continue
        request = deepcopy(old['request'])
        request['pending_release_inputs'] = [q for q in request.get('pending_release_inputs', [])
                                              if q not in resolved_release_inputs]
        if request != unit['request'] or old.get('prompt') != unit.get('prompt'):
            continue
        operation = 'qa-unit:' + str(after.run_id) + ':' + unit['dependency']
        if storage.get_operation(operation):
            continue  # Never replace a failed, uncertain or completed operation.
        try:
            entry = cached_unit(storage, before, old)
            if not entry or entry['status'] != 'completed':
                continue
            value = entry['value']
            result = schema_for(unit['kind']).model_validate(value['result'])
            validate_unit(after, unit, result)
            if not result.passed or not result.coverage_complete:
                continue
        except (ValidationError, QualityReviewError, KeyError):
            continue
        persist(storage, operation, after.run_id, {
            **value, 'dependency': unit['dependency'],
            'compatibility': {
                'source_operation': 'qa-unit:' + str(before.run_id) + ':' + old['dependency'],
                'source_revision': before.revision,
                'validation': 'Identical complete review request, prompt, schema, validator, evidence and model settings except explicitly resolved release-checklist entries; result grounding revalidated.',
                'resolved_release_inputs': list(resolved_release_inputs),
            }})
    return retain_compatible_units(storage, after, settings)
