"""Hash-checked previous-revision examples, never current QA or approved assets."""
import hashlib
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path


def key(state, revision):
    return 'revision-examples:' + str(state.run_id) + ':' + str(revision)


def preserve(workflow, state):
    from src.services.evaluation import latest
    from src.services.evaluation_images import current, file
    from src.services.evaluation_vision import attachment
    storage, settings = workflow.storage, workflow.settings
    operation = key(state, state.revision)
    previous = storage.get_operation(operation)
    if previous and previous['status'] == 'completed':
        return  # Never replace a historical snapshot.
    directory = settings.output_dir / 'runs' / str(state.run_id) / 'evaluation-examples' / f'revision-{state.revision:03d}'
    assets = []
    def keep(filename, raw, kind, media_type):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(raw)
        assets.append({'filename': filename, 'sha256': hashlib.sha256(raw).hexdigest(),
                       'kind': kind, 'media_type': media_type})
    if state.graphic_file:
        raw, _ = attachment(settings, state)  # Invalid bytes must not be preserved as valid examples.
        keep('infographic.webp', raw, 'infographic', 'image/webp')
    images = current(settings, storage, state)
    for platform, item in (images or {}).get('images', {}).items():
        raw, _ = file(settings, storage, state, platform)
        keep(item['filename'], raw, platform, item['media_type'])
    evaluation = latest(storage, state, settings) or {}
    social = evaluation.get('results', {}).get('social_generate', {})
    social = {channel: text for channel, text in social.items()
              if channel in {'linkedin', 'google_business_profile'} and isinstance(text, str)}
    value = {'run_id': str(state.run_id), 'revision': state.revision,
             'article_sha256': hashlib.sha256(json.dumps(state.editorial, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
             'assets': assets, 'social': social, 'label': 'Previous revision examples — unapproved',
             'created_at': datetime.now(timezone.utc).isoformat(),
             'current_revision_compatible': False}
    storage.begin_operation(operation, str(state.run_id))
    storage.finish_operation(operation, value)


def files(settings, storage, state):
    """Only snapshots of this run's recorded edit predecessors are eligible."""
    result = {}
    for revision in sorted({h['previous_revision'] for h in state.editorial_change_history}, reverse=True):
        if revision >= state.revision:
            continue
        entry = storage.get_operation(key(state, revision))
        record = (entry or {}).get('value') or {}
        if not entry or entry['status'] != 'completed' or record.get('run_id') != str(state.run_id) or record.get('revision') != revision:
            continue
        directory = (settings.output_dir / 'runs' / str(state.run_id) / 'evaluation-examples' / f'revision-{revision:03d}').resolve()
        assets = {}
        valid = True
        for item in record.get('assets', []):
            name = item['filename']
            path = (directory / name).resolve()
            if Path(name).name != name or not path.is_relative_to(directory) or not path.is_file():
                valid = False; break
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                valid = False; break
            if item['media_type'] not in {'image/webp', 'image/jpeg', 'image/png'}:
                valid = False; break
            assets[name] = raw
        if not valid:
            continue  # Preserve records; never expose tampered or missing media.
        prefix = f'previous-output-examples/revision-{revision:03d}/'
        result.update({prefix + name: raw for name, raw in assets.items()})
        result[prefix + 'manifest.json'] = json.dumps(record, ensure_ascii=False, indent=2).encode()
        for channel, text in record.get('social', {}).items():
            if channel in {'linkedin', 'google_business_profile'}:
                result[prefix + channel + '.txt'] = (record['label'] + '\nRevision ' + str(revision) + '\n\n' + text).encode()
    return result


def gallery(assets):
    body = ''
    for name, raw in assets.items():
        if not name.endswith('/manifest.json'):
            continue
        record = json.loads(raw)
        prefix = name.rsplit('/', 1)[0] + '/'
        body += '<section><h2>Revision ' + str(record['revision']) + ' examples — unapproved</h2><p>These saved outputs belong to the previous draft. They have not been accepted for the current revision.</p>'
        for item in record['assets']:
            body += '<figure><img src="' + escape(prefix + item['filename'], quote=True) + '" alt="' + escape(item['kind']) + ' example"><figcaption>' + escape(item['kind'].replace('_', ' ')) + ' · revision ' + str(record['revision']) + ' example</figcaption></figure>'
        for channel, text in record.get('social', {}).items():
            body += '<h3>' + escape(channel.replace('_', ' ').title()) + ' — previous revision</h3><pre>' + escape(text) + '</pre>'
        body += '</section>'
    return body
