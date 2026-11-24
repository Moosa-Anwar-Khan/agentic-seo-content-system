"""Local editing never purchases generation or substitutes for human approval."""
from uuid import uuid4
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.combined.models import EditorialOutput, SupportedClaim
from src.combined.evidence import normalize_source_quotes
from src.combined.editorial import rules, structural_errors
from src.services.editorial_cleanup import cleanup_formatting
from src.schemas.jobs import Job
from tests.test_combined import draft_state
from tests.conftest import authenticated_headers, own_fixture


def editable(client, settings):
    settings.combined_mode = True
    settings.seo_decisions_enabled = True
    client.app.state.worker.close()
    state = draft_state(client.app.state.workflow)
    headers = authenticated_headers(client, 'Sam'); own_fixture(client, state)
    document = deepcopy(state.editorial)
    document['modules'][0]['markdown'] += '\n\nSie besprechen Ihre Fragen im persönlichen Gespräch.'
    return state, headers, {'edit_id': str(uuid4()), 'expected_revision': state.revision,
        'editorial': document, 'notes': 'Lokale redaktionelle Korrektur zur Prüfung ohne KI-Anfrage.'}


def test_local_edit_is_private_idempotent_and_invalidates_checks_without_paid_work(settings, monkeypatch):
    settings.combined_mode = True; settings.seo_decisions_enabled = True
    with TestClient(create_app(settings)) as client:
        state, headers, body = editable(client, settings)
        workflow = client.app.state.workflow
        calls_before = workflow.storage.list_ai_calls(str(state.run_id))
        costs_before = workflow.storage.research_costs(str(state.run_id))
        monkeypatch.setattr(workflow.context.llm, 'generate', lambda *a, **kw: pytest.fail('Local save must not call AI'))
        response = client.post('/runs/'+str(state.run_id)+'/editorial', headers=headers, json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['revision'] == state.revision+1 and result['human_revisions'] == state.human_revisions+1
        assert result['status'] == 'draft_created' and result['seo_check'] is None
        assert result['professional_review'] is None and result['publication'] == {}
        assert result['graphic_revision'] == -1
        assert result['analysis'] == state.model_dump(mode='json')['analysis']
        assert result['owner_user_id'] == state.owner_user_id
        assert len(result['editorial_change_history']) == 1
        assert workflow.storage.list_ai_calls(str(state.run_id)) == calls_before
        assert workflow.storage.research_costs(str(state.run_id)) == costs_before
        repeat = client.post('/runs/'+str(state.run_id)+'/editorial', headers=headers, json=body)
        assert repeat.status_code == 200 and repeat.json()['revision'] == result['revision']
        assert client.post('/runs/'+str(state.run_id)+'/reviews', headers=headers, json={
            'action': 'approved', 'expected_revision': result['revision']}).status_code == 409
        assert not (settings.output_dir/'runs'/str(state.run_id)/'approved').exists()
        other = authenticated_headers(client, 'Alex')
        assert client.post('/runs/'+str(state.run_id)+'/editorial', headers=other, json=body).status_code in {403,404}


@pytest.mark.parametrize('problem', ['stale','paused','active','limit','type','private_input'])
def test_invalid_or_uncertain_local_edits_are_blocked_before_writing(settings, problem):
    settings.combined_mode = True; settings.seo_decisions_enabled = True
    with TestClient(create_app(settings)) as client:
        state, headers, body = editable(client, settings);workflow=client.app.state.workflow
        if problem == 'stale': body['expected_revision'] += 1
        if problem == 'type': body['editorial']['page_type'] = 'blog'
        if problem == 'private_input': body['editorial']['modules'][0]['markdown'] += '\nprivate-client@example.org'
        if problem in {'paused','active'}:
            workflow.storage.create_job(Job(run_id=state.run_id,kind='social',reviewer='Sam',
                reviewer_id=state.owner_user_id,idempotency_key=uuid4(),status='paused' if problem=='paused' else 'queued'))
        if problem == 'limit':
            state.human_revisions=settings.max_human_revisions
            workflow.graph.update_state(workflow._config(str(state.run_id)),{'run':state.model_dump(mode='json')})
            workflow.storage.save_run_state(state)
        response=client.post('/runs/'+str(state.run_id)+'/editorial',headers=headers,json=body)
        assert response.status_code==(422 if problem=='private_input' else 409),response.text
        assert workflow.get(str(state.run_id)).revision==state.revision


@pytest.mark.parametrize('source,quote,expected', [
    ('Vorauszahlungen am 10.\u00a0März sind zu prüfen.', 'Vorauszahlungen am 10. März sind zu prüfen.', True),
    ('Vorauszahlungen am 11. März sind zu prüfen.', 'Vorauszahlungen am 10. März sind zu prüfen.', False),
    ('Vorauszahlungen am 10. März sind zu prüfen!', 'Vorauszahlungen am 10. März sind zu prüfen.', False),
    ('Wort A B mit Beleg. Wort A\tB mit Beleg.', 'Wort A  B mit Beleg.', False),
])
def test_quote_typography_repair_never_invents_a_source_match(settings, source, quote, expected):
    claim=SupportedClaim(id='test',text='Sie prüfen die Voraussetzungen.',source_id='test-source',
        quote=quote,tax_year=settings.tax_year,conditions='Synthetischer Offline-Test.')
    from types import SimpleNamespace
    draft=SimpleNamespace(claims=[claim])
    changes=normalize_source_quotes(draft,[{'id':'test-source','text':source}])
    assert bool(changes)==expected
    assert claim.quote in source if expected else claim.quote==quote


def test_local_formatting_is_idempotent_preserves_claims_and_updates_real_anchors(settings):
    from types import SimpleNamespace
    from src.combined.pipeline import demo_draft
    from src.combined.editorial import markdown, same_page
    from src.combined.markdown import parse
    from src.services.image_processing import keyword_slug
    draft=demo_draft(SimpleNamespace(briefing=SimpleNamespace(page_type='landing_page',
        primary_keyword='Immobiliensteuerberater')),settings)
    original=draft.model_copy(deep=True)
    process=next(module for module in draft.modules if module.id=='L07')
    old_heading=process.markdown.splitlines()[0][3:]
    draft.modules[0].markdown+='\n\n[Ablauf](#'+keyword_slug(old_heading)+')'
    for module_id in ['L01','L04','L05','L07','L09','L10','L13','L15']:
        next(module for module in draft.modules if module.id==module_id).markdown+='\n\n[Gespräch](https://steuerkanzlei.example/mandant-werden/?quelle=test)'
    pack=rules(settings,'landing_page')
    fixed,changes=cleanup_formatting(draft,'Immobiliensteuerberater',pack)
    doc=parse(markdown(fixed))
    assert sum(heading.level==2 and 'Immobiliensteuerberater' in heading.text for heading in doc.headings)>=2
    assert sum(same_page(link.url,pack['cta_target']) for link in doc.links)==6
    assert not any('Sprungziel' in error for error in structural_errors(fixed,'Immobiliensteuerberater',pack))
    assert fixed.claims==draft.claims and fixed.graphic==draft.graphic
    assert fixed.unresolved_questions==draft.unresolved_questions
    assert all(original.modules[i].markdown.split('\n\n',1)[1] in fixed.modules[i].markdown for i in range(len(fixed.modules)))
    twice,second_changes=cleanup_formatting(fixed,'Immobiliensteuerberater',pack)
    assert twice==fixed and not second_changes and changes
