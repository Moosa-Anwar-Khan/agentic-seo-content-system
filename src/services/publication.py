"""Manual publication records and independently reviewed social links."""
from datetime import datetime, timezone
from urllib.parse import urlsplit


def record(workflow, run_id, request, reviewer):
    if reviewer not in {'Alex', 'Sam'}:
        raise PermissionError('Manual publication recording requires an authorized reviewer')
    parts = urlsplit(request.url)
    base = urlsplit(workflow.settings.wordpress_base_url)
    if (parts.scheme != 'https' or parts.hostname != base.hostname or parts.username or parts.password or
            parts.port not in {None, 443} or parts.query or parts.fragment or parts.path in {'', '/'}):
        raise ValueError('Record a canonical HTTPS URL on the configured WordPress site')
    with workflow.storage.run_lock(run_id):
        state = workflow.get(run_id)
        if state.status != 'exported' or state.revision != request.expected_revision:
            raise ValueError('Only the current approved website revision can be recorded as published')
        state.publication = {'url': request.url, 'revision': state.revision,
                             'confirmed_by': reviewer, 'confirmed_live_at': datetime.now(timezone.utc).isoformat(),
                             'verification': 'manual_attestation'}
        workflow.graph.update_state(workflow._config(run_id), {'run': state.model_dump(mode='json')})
        workflow.storage.save_run_state(state)
        return state


def social_ready(state, post):
    if state.status != 'exported' or post.source_revision != state.revision or post.status != 'approved':
        return False
    if post.link_target == 'article':
        return bool(state.publication.get('revision') == state.revision and
                    state.publication.get('url') == post.link_url)
    return post.link_target == 'enquiry'


def use_article_link(workflow, run_id, post_id, expected_revision):
    with workflow.storage.run_lock(run_id):
        state = workflow.get(run_id)
        post = workflow.storage.get_asset(post_id, 'post')
        if (state.status != 'exported' or state.revision != expected_revision or not post or
                post.run_id != state.run_id or post.source_revision != state.revision or
                state.publication.get('revision') != state.revision):
            raise ValueError('Current article publication and social draft required')
        url = state.publication['url']
        if post.link_target == 'article' and post.link_url == url:
            return post
        post.text = post.text.replace(post.link_url, url) if post.link_url else post.text + '\n\n' + url
        maximum = 900 if post.platform == 'google_business_profile' else 1800
        if len(post.text) > maximum:
            raise ValueError('Article URL would exceed the channel length limit; revise the post')
        post.link_target = 'article'
        post.link_url = url
        post.status = 'pending'
        post.reviewed_by = post.reviewer_id = None
        workflow.storage.save_asset(post)
        return post


def record_outcome(workflow, run_id, request, reviewer):
    if reviewer not in {'Alex', 'Sam'}:
        raise PermissionError('Outcome entry requires an authorized reviewer')
    with workflow.storage.run_lock(run_id):
        state = workflow.get(run_id)
        if (state.status != 'exported' or state.revision != request.expected_revision or
                state.publication.get('revision') != state.revision):
            raise ValueError('Record publication of this revision before outcome measurements')
        from src.security.sanitize import sanitize_text
        item = request.model_dump(mode='json')
        item.update(recorded_by=reviewer, recorded_at=datetime.now(timezone.utc).isoformat(),
                    source='manual_entry', notes=sanitize_text(request.notes, 1000))
        state.outcomes.append(item)
        state.outcomes = state.outcomes[-52:]
        workflow.graph.update_state(workflow._config(run_id), {'run':state.model_dump(mode='json')})
        workflow.storage.save_run_state(state)
        return state
