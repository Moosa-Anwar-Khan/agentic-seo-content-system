"""Read-only rehearsal of saved work; never records approvals or queues jobs."""
import hashlib
import io
import json
import zipfile

from src.services.evaluation import package
from src.services.evaluation_vision import attachment

LABEL = 'TEST WALKTHROUGH — UNAPPROVED'


def snapshot(settings, storage, state):
    try:
        attachment(settings, state)
        graphic = True
    except (ValueError, OSError):
        graphic = False
    qa = bool(state.brand_check and state.brand_check.passed and state.seo_check and state.seo_check.passed)
    checks = [bool(state.analysis), bool(state.briefing), bool(state.editorial and state.article), qa, graphic]
    labels = ['Research', 'Topic selection', 'Draft', 'QA', 'Infographic', 'Professional review', 'Final approval', 'Export']
    descriptions = [
        'Inspect saved research. This replays existing work; it does not purchase new research.',
        'Inspect the saved topic selection and briefing. No new topic is selected.',
        'Inspect the current German article. No text is rewritten.',
        'Use the saved automated QA result. No review is purchased.',
        'Inspect the validated infographic attached to this revision.',
        'Simulate the professional-review action for testing. This is not tax-adviser attestation.',
        'Simulate final approval for testing. This does not approve the article for release.',
        'Download an unapproved test package. Nothing is uploaded or published.',
    ]
    active = any(j.status in {'queued', 'running', 'paused'} for j in storage.list_jobs(str(state.run_id)))
    context = hashlib.sha256(json.dumps({
        'version': 1, 'revision': state.revision, 'editorial': state.editorial,
        'sources': state.research_packet, 'graphic': state.graphic_sha256,
        'checks': checks, 'active': active,
    }, sort_keys=True, default=str).encode()).hexdigest()
    return {'label': LABEL, 'revision': state.revision, 'context': context,
            'ready': all(checks) and not active,
            'blockers': [f'{labels[i]} must complete in the normal workflow first.' for i, ok in enumerate(checks) if not ok]
                        + (['Wait for active work or resolve the uncertain request first.'] if active else []),
            'steps': [{'number': i + 1, 'label': name, 'description': descriptions[i],
                       'mode': 'saved work' if i < 5 else 'simulation' if i < 7 else 'test download',
                       'available': checks[i] if i < 5 else True} for i, name in enumerate(labels)],
            'notice': 'Only the test panel completes. Real professional review, final approval and approved export remain unchanged. Earlier output examples retain their original revision labels.'}


def export(settings, storage, state, revision, context):
    current = snapshot(settings, storage, state)
    if revision != state.revision or context != current['context']:
        raise ValueError('Saved work changed. Reopen the test walkthrough before downloading.')
    if not current['ready']:
        raise ValueError('Complete the missing saved steps or resolve active work before the test export.')
    receipt = {**current, 'professional_attestation': 'SIMULATED ONLY — no attestation recorded',
               'final_approval': 'SIMULATED ONLY — no approval recorded',
               'export': 'unapproved test artifact', 'publication': False,
               'note': 'This file explains a rehearsal, not proof of actual approvals or user clicks.'}
    # Even rehearsals of an already-approved run must export unapproved copies.
    # This copy is never persisted or fed to the ordinary publishing pipeline.
    preview = state.model_copy(deep=True)
    preview.status = 'seo_check_passed'
    preview.professional_review = None
    original = package(settings, storage, preview)
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'README.txt':
                data = (LABEL + '\nOpen TEST_WALKTHROUGH.txt, then START_HERE.html.\n\n').encode() + data
            if name == 'START_HERE.html':
                from bs4 import BeautifulSoup
                page = BeautifulSoup(data.decode(), 'html.parser')
                notice = page.new_tag('p')
                notice.string = LABEL + '. Professional review and final approval are simulated only. Not an approved release.'
                if page.body: page.body.insert(0, notice)
                data = str(page).encode()
            target.writestr(name, data)
        target.writestr('test-walkthrough.json', json.dumps(receipt, ensure_ascii=False, indent=2))
        target.writestr('TEST_WALKTHROUGH.txt', LABEL + '\n\nSteps 1–5 replay saved work. Steps 6–7 simulate approvals. Step 8 downloads this test package.\nNo professional attestation or final approval was recorded. No publication occurred.\nOld social/image examples remain labelled by their original revision.\n')
    return result.getvalue()
