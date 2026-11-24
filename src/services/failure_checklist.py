"""Append newly detected failure causes to the root draft-failure checklist.

Shipped disabled; an installation enables it with FAILURE_CHECKLIST_AUTO. Causes are
compared by stable identity only: brand-check rule text, QA page type/module/rule and
failure component/code. Technical diagnostics, provider text and draft quotations are
never written. A checklist problem must never affect the failure being recorded.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import json
import os
import re
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'DRAFT_FAILURE_CHECKLIST.md'
START = '<!-- AUTO-DETECTED:START -->'
END = '<!-- AUTO-DETECTED:END -->'
KNOWN = re.compile(r'<!-- known-causes: (\[.*?\]) -->', re.S)
SECTION = (
    '\n## 8. Automatically detected, not yet curated\n\n'
    'The application appends a cause here the first time it records a new kind of failure. '
    'Agents move reviewed entries into the tables above and keep the identity in the known list.\n\n'
    f'{START}\n<!-- known-causes: [] -->\n{END}\n')


def _text(value, limit=300):
    # Single line, no HTML comments or tags, so the Markdown section cannot be broken.
    value = re.sub(r'\s+', ' ', str(value or '')).replace('<', '‹').replace('>', '›').strip()
    return value[:limit]


def causes(event, state=None):
    """Return (identity, page type, description) for every cause in one failure event."""
    summary = event.get('summary') or {}
    page = 'unknown'
    if state is not None:
        page = (state.editorial or {}).get('page_type') or getattr(state.briefing, 'page_type', None) or 'unknown'
    found = []
    if summary.get('code') == 'quality_failed' and state is not None:
        brand, seo = state.brand_check, state.seo_check
        if brand and not brand.passed:
            found += [('brand:' + _text(e.split(': ')[0], 120), page, _text(e)) for e in brand.errors]
        if seo and not seo.passed:
            found += [(f'qa:{page}:{f.module_id}:{f.rule}', page, _text(f.message_en))
                      for f in seo.findings if f.severity == 'blocking']
    if not found:
        found = [(f"failure:{_text(summary.get('component'), 60)}:{_text(summary.get('code'), 60)}", page,
                  _text(summary.get('reason')) + (f" (unit {_text(summary['unit_id'], 40)})" if summary.get('unit_id') else ''))]
    unique = {}
    for identity, kind, description in found:
        unique.setdefault(identity, (kind, description))
    return [(identity, kind, description) for identity, (kind, description) in unique.items()]


@contextmanager
def _locked():
    with open(Path(tempfile.gettempdir()) / 'muster-failure-checklist.lock', 'w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _update(path, entries, identities):
    """Add identities to the known list and list the given entries; returns the number listed."""
    with _locked():
        text = path.read_text(encoding='utf-8')
        if START not in text or END not in text:
            text = text.rstrip('\n') + '\n' + SECTION
        head, rest = text.split(START, 1)
        body, tail = rest.split(END, 1)
        match = KNOWN.search(body)
        known = json.loads(match.group(1)) if match else []
        new = [i for i in dict.fromkeys(identities) if i not in known]
        listed = [e for e in entries if e[0] in new]
        if not new:
            return 0
        lines = [line for line in KNOWN.sub('', body).strip('\n').split('\n') if line.strip()]
        lines += [f'- **{date} · {run} · {kind}**: {description} (`{identity}`)' for identity, kind, description, date, run in listed]
        known += new
        body = '\n' + '\n'.join([f'<!-- known-causes: {json.dumps(known, ensure_ascii=False)} -->', *lines]) + '\n'
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix='.draft-checklist-')
        with os.fdopen(handle, 'w', encoding='utf-8') as out:
            out.write(head + START + body + END + tail)
        os.replace(temporary, path)
        return len(listed)


def enabled():
    try:
        from src.config.settings import Settings
        return Settings().failure_checklist_auto
    except Exception:
        return False


def record(event, state=None, *, path=None, active=None):
    """List causes from one recorded failure that the checklist does not know yet."""
    if not (enabled() if active is None else active):
        return 0
    summary = event.get('summary') or {}
    date = (summary.get('occurred_at') or datetime.now(timezone.utc).isoformat())[:10]
    run = _text(summary.get('run_id'), 8) or 'no run'
    found = causes(event, state)
    entries = [(identity, kind, description, date, run) for identity, kind, description in found]
    return _update(path or PATH, entries, [identity for identity, _, _ in found])


def register_known(identities, *, path=None):
    """Mark already-curated causes as known without listing them again."""
    return _update(path or PATH, [], list(identities))
