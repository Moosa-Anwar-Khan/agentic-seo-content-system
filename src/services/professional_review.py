"""Authenticated professional attestation bound to exact evidence and graphic."""
import hashlib
import json

from src.schemas.review import ProfessionalReviewRecord
from src.security.auth import AccountStore
from src.security.sanitize import sanitize_text


def fingerprint(state):
    data = {'revision': state.revision, 'editorial': state.editorial,
            'graphic_sha256': state.graphic_sha256,
            'official_sources': state.research_packet.get('official_sources', [])}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def is_current(state):
    review = state.professional_review
    return bool(review and review.revision == state.revision and review.fingerprint == fingerprint(state))


def attest(workflow, run_id, request, user_id):
    user = AccountStore(workflow.storage).user('id', user_id)
    if not user or not user['enabled'] or user['role'] != 'tax_adviser':
        raise PermissionError('Professional review requires an enabled tax-adviser account')
    with workflow.storage.run_lock(run_id):
        state = workflow.get(run_id)
        if (workflow.settings.demo_mode or not state.combined_version or
                state.status != 'seo_check_passed' or state.revision != request.expected_revision or
                not state.graphic_file or state.graphic_revision != state.revision):
            raise ValueError('A current live draft and infographic are required')
        from src.combined.editorial import release_errors
        errors = release_errors(state, workflow.settings)
        if errors:
            raise ValueError('Resolve release blockers before professional attestation')
        from pathlib import Path
        if hashlib.sha256(Path(state.graphic_file).read_bytes()).hexdigest() != state.graphic_sha256:
            raise ValueError('Infographic changed')
        state.professional_review = ProfessionalReviewRecord(
            revision=state.revision, reviewer_id=user_id, reviewer_name=user['name'],
            fingerprint=fingerprint(state), notes=sanitize_text(request.notes, 2000))
        workflow.graph.update_state(workflow._config(run_id), {'run': state.model_dump(mode='json')})
        workflow.storage.save_run_state(state)
        return state
