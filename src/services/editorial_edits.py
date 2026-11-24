"""Free, revision-bound saved-draft edits. Never generate, approve or publish."""
import hashlib
import json
from datetime import datetime, timezone

from src.combined.editorial import as_article, render, release_errors
from src.combined.pipeline import brand
from src.security.run_access import RunAccess
from src.security.sanitize import sanitize_text, untrusted_context
from src.services.editorial_brand import public_contact_emails
from src.services.export import LocalExporter


def save_edit(workflow, run_id, request, actor=None, *, local_source=None):
    if actor is None and local_source != 'local_editorial_repair':
        raise PermissionError('An authenticated editor is required')
    with workflow.storage.run_lock(run_id):
        state = workflow.get(run_id)
        if actor is not None:
            RunAccess(workflow.storage.account_store).drafting(state, actor)
        payload = request.model_dump(mode='json')
        # An additive optional field must not change historical edit identities.
        with_null = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if request.fee_presentation is None:
            payload.pop('fee_presentation')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        actor_id = actor['id'] if actor else None
        prior = next((item for item in state.editorial_change_history
                      if item['edit_id'] == str(request.edit_id)), None)
        if prior:
            compatible = {fingerprint, with_null} if request.fee_presentation is None else {fingerprint}
            if prior['fingerprint'] not in compatible or prior['actor_id'] != actor_id:
                raise ValueError('This edit identifier already belongs to another request')
            return state
        if (state.revision != request.expected_revision or not state.combined_version or
                not state.briefing or not state.article or not state.editorial or
                state.status not in {'failed', 'draft_created', 'brand_check_passed', 'seo_check_passed', 'exported'}):
            raise ValueError('Open the current saved article revision before editing')
        if state.human_revisions >= workflow.settings.max_human_revisions:
            raise ValueError('The explicit revision limit has been reached')
        if any(job.status in {'queued', 'running', 'paused'}
               for job in workflow.storage.list_jobs(run_id)):
            raise ValueError('Inspect active or uncertain work before editing this article')
        draft = request.editorial
        if draft.page_type != state.briefing.page_type:
            raise ValueError('An edit cannot change the selected page type')
        if len(request.model_dump_json().encode()) > 200000:
            raise ValueError('The saved-draft edit exceeds the supported size')
        untrusted_context(draft.model_dump(mode='json'), public_emails=public_contact_emails(workflow.settings))
        notes = sanitize_text(request.notes, 2000)
        old_revision = state.revision
        if request.fee_presentation is not None and draft.page_type != 'landing_page':
            raise ValueError('The fee-presentation exception is for landing pages only')
        # Capture current, hash-checked assets before clearing revision bindings.
        from src.services.revision_examples import preserve
        preserve(workflow, state)
        if request.fee_presentation is not None:
            from src.schemas.review import FeePolicyRecord
            from src.services.fee_presentation import price_pack
            state.fee_presentation = FeePolicyRecord(
                mode=request.fee_presentation.mode, revision=old_revision + 1,
                actor_id=actor_id, source=local_source or 'authenticated_editor',
                reason=notes, price_pack_sha256=price_pack(workflow.settings)[1])
        state.revision += 1
        state.human_revisions += 1
        state.editorial = draft.model_dump(mode='json')
        state.article = as_article(draft, state.briefing.primary_keyword)
        state.article.target_url = state.briefing.target_url
        state.html = render(draft)
        state.brand_check = state.seo_check = None
        state.qa_manifest = {}
        state.agent_outputs['local_correction'] = {'revision':state.revision,'semantic_review':'pending','edit_id':str(request.edit_id)}
        state.professional_review = None
        state.graphic_file = ''; state.graphic_sha256 = ''; state.graphic_revision = -1
        state.publication = {}
        state.error = None
        state.human_feedback = ''
        state.quality_checks_only = True
        state.status = 'draft_created'
        brand(workflow.context, state)  # Deterministic; no semantic/provider QA here.
        state.status = 'draft_created'
        state.release_blockers = release_errors(state, workflow.settings)
        state.editorial_change_history.append({
            'edit_id': str(request.edit_id), 'fingerprint': fingerprint,
            'actor_id': actor_id, 'source': local_source or 'authenticated_editor',
            'previous_revision': old_revision, 'revision': state.revision,
            'fee_presentation': state.fee_presentation.model_dump(mode='json') if request.fee_presentation else None,
            'notes': notes, 'created_at': datetime.now(timezone.utc).isoformat()})
        state.updated_at = datetime.now(timezone.utc)
        workflow.graph.update_state(workflow._config(run_id),
            {'run': state.model_dump(mode='json')}, as_node='content_agent')
        workflow._save(state)
        LocalExporter(workflow.settings.output_dir,workflow.settings).write_bundle(state)
        return state
