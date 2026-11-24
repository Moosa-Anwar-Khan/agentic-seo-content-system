"""New failure causes are appended once; curated, disabled and broken cases stay silent."""
from types import SimpleNamespace

from src.schemas.content import CheckResult, ReviewFinding
from src.services import failure_checklist
from src.services.failures import capture
from src.storage.sqlite_storage import SQLiteStorage


def checklist(tmp_path):
    path = tmp_path / 'DRAFT_FAILURE_CHECKLIST.md'
    path.write_text('# Draft failure checklist\n\nCurated tables.\n', encoding='utf-8')
    return path


def quality_event(run='1c665c17-0000'):
    return {'summary': {'code': 'quality_failed', 'component': 'Brand Check', 'run_id': run,
                        'occurred_at': '2026-10-27T01:00:00+00:00', 'reason': 'Draft saved, checks failed.'}}


def state(brand_errors=(), findings=()):
    return SimpleNamespace(editorial={'page_type': 'blog'}, briefing=None,
        brand_check=CheckResult(passed=not brand_errors, errors=list(brand_errors)),
        seo_check=CheckResult(passed=not findings, findings=list(findings)))


def test_new_cause_is_listed_once_and_repeat_is_skipped(tmp_path):
    path = checklist(tmp_path)
    failed = state(['Interner Link nicht im vorhandenen Seiteninventar: https://example.test/a/'])
    assert failure_checklist.record(quality_event(), failed, path=path, active=True) == 1
    text = path.read_text(encoding='utf-8')
    assert 'Curated tables.' in text and '2026-10-27 · 1c665c17 · blog' in text
    assert '`brand:Interner Link nicht im vorhandenen Seiteninventar`' in text
    again = state(['Interner Link nicht im vorhandenen Seiteninventar: https://example.test/b/'])
    assert failure_checklist.record(quality_event(), again, path=path, active=True) == 0
    assert path.read_text(encoding='utf-8') == text


def test_curated_causes_and_suggestions_are_not_listed(tmp_path):
    path = checklist(tmp_path)
    failure_checklist.register_known(['qa:blog:S10:required_module'], path=path)
    findings = [ReviewFinding(module_id='S10', quote='x', rule='required_module', message_en='FAQ missing.'),
                ReviewFinding(module_id='S15', quote='y', rule='links', severity='suggestion', category='suggestion',
                              message_en='Optional wording.')]
    assert failure_checklist.record(quality_event(), state(findings=findings), path=path, active=True) == 0
    assert 'FAQ missing.' not in path.read_text(encoding='utf-8')


def test_other_failures_use_public_summary_only(tmp_path):
    path = checklist(tmp_path)
    event = {'summary': {'code': 'run_budget', 'component': 'SEO QA', 'run_id': 'abcdef12-x', 'unit_id': 'claim:C17',
                         'reason': 'The next AI step could not fit within the run token budget.'},
             'technical': {'provider_request_id': 'secret-request', 'stack_locations': [{'file': 'src/x.py'}]}}
    assert failure_checklist.record(event, None, path=path, active=True) == 1
    text = path.read_text(encoding='utf-8')
    assert '`failure:SEO QA:run_budget`' in text and 'unit claim:C17' in text
    assert 'secret-request' not in text and 'src/x.py' not in text


def test_disabled_checklist_is_untouched(tmp_path):
    path = checklist(tmp_path)
    before = path.read_text(encoding='utf-8')
    assert failure_checklist.record(quality_event(), state(['Genau eine H1 erforderlich.']), path=path, active=False) == 0
    assert path.read_text(encoding='utf-8') == before


def test_checklist_error_never_breaks_failure_diagnostics(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError('read-only file system')
    monkeypatch.setattr(failure_checklist, 'record', broken)
    storage = SQLiteStorage(tmp_path / 'runs.sqlite')
    event = capture(storage, code='processing')
    assert 'diagnostics_unavailable' not in event['technical']
    assert event['summary']['code'] == 'processing' and event['summary']['reference'] == event['id']
