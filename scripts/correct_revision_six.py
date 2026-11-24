"""Approved fee/graphic-note correction. No providers, role changes or approval."""
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid5
import argparse
import hashlib
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUN = 'd7d478a8-fd6d-4837-918d-4335de37b506'
EDIT = uuid5(UUID(RUN), 'revision-six-approved-fee-wording-and-graphic-notes-v1')
RESOLVED = 'Preise für Finanzbuchhaltung und Steuererklärungen fehlen. Platzhalter vor Veröffentlichung durch freigegebene Angaben ersetzen; vergütungsrechtliche Hinweise gesondert belegen und prüfen.'


def corrected(editorial, fee_wording):
    draft = deepcopy(editorial)
    by_id = {m['id']: m for m in draft['modules']}
    by_id['L05']['markdown'] = (
        '## Kosten für Ihren gewünschten Leistungsumfang\n\n'
        '**Finanzbuchhaltung:** Die Kosten für die Finanzbuchhaltung klären Sie bitte persönlich mit uns.\n\n'
        '**Steuererklärungen:** ' + fee_wording + '\n\n'
        'Beschreiben Sie in Ihrer Anfrage, für welche Aufgaben Sie Unterstützung suchen. '
        'Fragen Sie die dazugehörigen Kosten persönlich an. Der konkrete Leistungsumfang wird persönlich vereinbart.')
    old = '[PREIS FEHLT] Bitte fragen Sie die Kosten für den von Ihnen gewünschten Leistungsumfang persönlich an. Eine freigegebene Preisangabe für die hier dargestellten Leistungen liegt noch nicht vor.'
    if by_id['L14']['markdown'].count(old) != 1:
        raise ValueError('Inspect the changed cost FAQ before editing')
    by_id['L14']['markdown'] = by_id['L14']['markdown'].replace(old,
        'Für Steuererklärungen gilt: ' + fee_wording + ' Die Kosten für die Finanzbuchhaltung klären Sie bitte persönlich mit uns. '
        'Nennen Sie in Ihrer Anfrage den gewünschten Leistungsumfang.')
    old = 'Für diesen Anfrage-Kompass ist eine Infografik vorgesehen. Die Tabelle enthält bereits das vollständige Textäquivalent; eine Bilddatei liegt diesem Entwurf nicht bei.'
    if by_id['L04a']['markdown'].count(old) != 1:
        raise ValueError('Inspect the changed graphic note before editing')
    by_id['L04a']['markdown'] = by_id['L04a']['markdown'].replace(old,
        'Der Anfrage-Kompass umfasst fünf Vorbereitungspunkte: Finanzbuchhaltung, Steuererklärungen, Belegablage, verwendete Software und Ihr Gesprächsanliegen. Die Tabelle enthält das Textäquivalent zur Infografik.')
    draft['graphic']['alt'] = 'Anfrage-Kompass für Einzelunternehmen: Finanzbuchhaltung, Steuererklärungen, Belegablage, verwendete Software und Gesprächsanliegen.'
    draft['graphic']['caption'] = 'Fünf Vorbereitungspunkte für Ihre Beratungsanfrage bei Muster & Partner.'
    if draft['unresolved_questions'].count(RESOLVED) != 1:
        raise ValueError('Inspect the release-input list before editing')
    draft['unresolved_questions'].remove(RESOLVED)
    return draft


def main():
    # Fail closed even if an unexpected downstream path attempts networking.
    import os, socket
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    def no_network(*args, **kwargs):
        raise AssertionError('Network requests are forbidden during local correction')
    socket.socket.connect = socket.socket.connect_ex = no_network
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from src.config.settings import Settings
    from src.orchestrator.workflow import Workflow
    from src.schemas.review import EditorialEditRequest
    from src.services.editorial_edits import save_edit
    from src.services.fee_presentation import price_pack
    from src.services.local_review_reuse import retain
    from src.services.progress import progress
    from src.services.evaluation import package
    from src.orchestrator.state import RunState
    settings = Settings()
    # The supplied private database backup must be readable and internally valid.
    with sqlite3.connect('file:' + str(args.backup.resolve()) + '?mode=ro', uri=True) as db:
        assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
        original = db.execute('select state from runs where run_id=?', (RUN,)).fetchone()[0]
    with Workflow(settings) as workflow:
        def forbidden(*a, **kw): raise AssertionError('Provider requests are forbidden during this repair')
        workflow.context.llm.generate = forbidden
        state = workflow.get(RUN)
        before = RunState.model_validate_json(original)
        if any(h['edit_id'] == str(EDIT) for h in state.editorial_change_history):
            assert state.revision == 6
        else:
            assert state.revision == 5 and state.seo_check.passed
            # get() restores the authoritative graph checkpoint, whose save
            # timestamp may precede the SQL projection. Every substantive field
            # must still match the verified backup before changing content.
            actual = state.model_dump(mode='json')
            baseline = RunState.model_validate_json(original).model_dump(mode='json')
            actual.pop('updated_at'); baseline.pop('updated_at')
            assert actual == baseline
            assert not any(j.status in {'queued', 'running', 'paused'} for s in workflow.storage.list_runs() for j in workflow.storage.list_jobs(str(s.run_id)))
            before = state.model_copy(deep=True)
            costs = workflow.storage.campaign_costs(RUN)
            calls = [c.model_dump(mode='json') for c in workflow.storage.list_ai_calls(RUN)]
            state = save_edit(workflow, RUN, EditorialEditRequest(
                edit_id=EDIT, expected_revision=5, editorial=corrected(state.editorial, price_pack(settings)[0]['statement_stbvv']),
                fee_presentation={'mode': 'approved_wording_only', 'confirmed': True},
                notes='Explicit user decision on 22 October 2026: use approved StBVV fee wording for applicable services without numeric prices; keep Anna’s statement pending. Correct existing infographic notes locally; no paid requests or approvals.'),
                local_source='local_editorial_repair')
            assert workflow.storage.campaign_costs(RUN) == costs
            assert [c.model_dump(mode='json') for c in workflow.storage.list_ai_calls(RUN)] == calls
        # Also recover a process interruption after the revision was saved but
        # before the free compatibility manifest was recorded.
        retain(workflow.storage, before, state, settings, resolved_release_inputs=[RESOLVED])
        assert state.revision == 6 and state.seo_check is None and state.professional_review is None
        assert state.graphic_revision == -1 and state.brand_check.passed
        report = progress(workflow, state)
        out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
        name = 'evaluation-unapproved-' + RUN + '-r6-brand-2.2.zip'
        (out / name).write_bytes(package(settings, workflow.storage, state))
        result = {'run_id': RUN, 'revision': 6, 'state': report['state'], 'qa': report['qa'],
            'review_plan': report['review_plan'], 'release_blockers': state.release_blockers,
            'paid_calls_added': 0, 'professional_approval': 'pending', 'final_approval': 'pending',
            'new_current_graphic_attachment': False, 'package': name}
        (out / 'local-correction.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        print(json.dumps({k: result[k] for k in ['revision','state','qa','review_plan','paid_calls_added','package']}, indent=2))


if __name__ == '__main__': main()
