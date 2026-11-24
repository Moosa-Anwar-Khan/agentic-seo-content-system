"""Free correction, explicit fee policy and honest previous-revision outputs."""
from copy import deepcopy
import hashlib
import io
import json
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.app import create_app
from src.combined.editorial import release_errors
from src.orchestrator.workflow import Workflow
from src.schemas.review import EditorialEditRequest, FeePolicyRecord
from src.services.editorial_edits import save_edit
from src.services.fee_presentation import context, price_pack
from src.services.local_review_reuse import retain
from src.services.qa_units import plan, cached_unit
from src.services import evaluation
from src.services.revision_examples import files as example_files
from tests.conftest import authenticated_headers, own_fixture
from tests.test_solo_evaluation import fixture


@pytest.fixture(autouse=True)
def combined(settings):
    settings.combined_mode=True;settings.seo_decisions_enabled=True


def policy(settings, state, mode='approved_wording_only'):
    return FeePolicyRecord(mode=mode, revision=state.revision, source='local_editorial_repair',
                           reason='Explicit synthetic user decision for this page only.',
                           price_pack_sha256=price_pack(settings)[1])


def costs(state, settings):
    wording = price_pack(settings)[0]['statement_stbvv']
    for m in state.editorial['modules']:
        if m['id'] in {'L05','L14'}: m['markdown'] += '\n\n' + wording


def test_fee_policy_is_run_only_and_legacy_requests_unchanged(settings):
    with Workflow(settings) as w:
        s = fixture(settings, w); baseline = plan(s, settings)[1]
        assert context(s, settings) is None
        s.fee_presentation = policy(settings, s)
        new = {u['id']:u for u in plan(s, settings)[1]}
        for u in baseline:
            if u['kind']!='page' and not set(u['module_ids']) & {'L05','L14'}:
                assert u['request'] == new[u['id']]['request']
        assert new['module:L05']['request']['review_contract']['fee_presentation']['approval_current']
        assert 'brand-fee:stbvv' in new['module:L05']['request']['allowed_evidence_refs']
        assert any(f['id']=='brand-fee:stbvv' for f in new['module:L14']['request']['approved_brand_facts'])
        other=s.model_copy(deep=True); other.fee_presentation=None
        assert context(other, settings) is None


def test_statement_and_prices_cannot_be_resolved_by_deleting_questions(settings):
    with Workflow(settings) as w:
        s=fixture(settings,w); s.editorial['unresolved_questions']=[]
        for m in s.editorial['modules']:
            if m['id']=='L05':m['markdown']+='\n\n[PREIS FEHLT]'
            if m['id']=='L08':m['markdown']='### Persönliche Einordnung\n\n[FREIGEGEBENES STATEMENT FEHLT]'
        assert 'Freigegebene Preise fehlen.' in release_errors(s,settings)
        assert 'Freigegebenes persönliches Statement mit Zuschreibung fehlt.' in release_errors(s,settings)
        s.fee_presentation=policy(settings,s)
        assert 'Freigegebene Preise fehlen.' in release_errors(s,settings)
        for m in s.editorial['modules']:m['markdown']=m['markdown'].replace('[PREIS FEHLT]','Kosten persönlich anfragen.')
        costs(s,settings)
        assert 'Freigegebene Preise fehlen.' not in release_errors(s,settings)
        assert 'Freigegebenes persönliches Statement mit Zuschreibung fehlt.' in release_errors(s,settings)
        s.fee_presentation=policy(settings,s,'numeric_prices')
        assert 'Freigegebene Preise fehlen.' in release_errors(s,settings)
        s.fee_presentation=policy(settings,s)
        s.fee_presentation.price_pack_sha256='changed'
        assert any('Preistabelle' in e for e in release_errors(s,settings))


def test_fee_change_requires_explicit_confirmation():
    for body in [{'mode':'approved_wording_only'}, {'mode':'approved_wording_only','confirmed':False}]:
        from src.schemas.review import FeePolicyChange
        with pytest.raises(ValidationError):FeePolicyChange.model_validate(body)


def test_audited_edit_preserves_assets_and_blocks_approvals_without_provider_calls(settings,monkeypatch):
    settings.combined_mode=True;settings.seo_decisions_enabled=True
    with TestClient(create_app(settings)) as client:
        client.app.state.worker.close();w=client.app.state.workflow;s=fixture(settings,w)
        headers=authenticated_headers(client,'Sam');own_fixture(client,s)
        before=s.model_copy(deep=True);old_graphic=s.graphic_file
        social={'linkedin':'Sie ordnen Ihre Unterlagen.','google_business_profile':'Sie bereiten Ihre Anfrage vor.'}
        evaluation.write(w.storage,'evaluation:'+str(s.run_id)+':'+evaluation.current_key(s,settings),s,
                         {'results':{'social_generate':social},'status':'incomplete'})
        monkeypatch.setattr(w.context.llm,'generate',lambda *a,**k:pytest.fail('No paid calls'))
        document=deepcopy(s.editorial);document['modules'][0]['markdown']+='\n\nSie schildern Ihr Anliegen.'
        request={'edit_id':str(uuid4()),'expected_revision':s.revision,'editorial':document,
                 'notes':'Approved fee presentation, with statement pending.',
                 'fee_presentation':{'mode':'approved_wording_only','confirmed':True}}
        r=client.post('/runs/'+str(s.run_id)+'/editorial',headers=headers,json=request)
        assert r.status_code==200,r.text
        current=w.get(str(s.run_id));assert current.revision==s.revision+1
        assert current.fee_presentation.actor_id==s.owner_user_id
        assert current.seo_check is None and current.graphic_revision==-1 and not current.graphic_file
        assert current.professional_review is None
        assert not w.storage.list_ai_calls(str(s.run_id))
        assert client.post('/runs/'+str(s.run_id)+'/editorial',headers=headers,json=request).json()['revision']==current.revision
        url='/runs/'+str(s.run_id)+'/evaluation'
        z=zipfile.ZipFile(io.BytesIO(client.get(url+'/download',headers=headers).content))
        prefix=f'previous-output-examples/revision-{s.revision:03d}/'
        assert prefix+'infographic.webp' in z.namelist()
        assert z.read(prefix+'infographic.webp')==__import__('pathlib').Path(old_graphic).read_bytes()
        assert prefix+'linkedin.txt' in z.namelist()
        assert 'infographic-unapproved.webp' not in z.namelist()
        assert 'Zuordnung und Prüfung für diese Revision stehen noch aus.' in z.read('article.html').decode()
        assert b'previous draft' in z.read('START_HERE.html')
        page=client.get(url+'/review-package',headers=headers)
        assert page.status_code==200 and 'frame-src about:' in page.headers['content-security-policy']
        assert 'sandbox=""' in page.text and 'srcdoc=' in page.text and 'data:image/webp;base64,' in page.text
        assert 'download="evaluation-unapproved-' in page.text
        assert 'current_revision_compatible' in z.read(prefix+'manifest.json').decode()
        assert client.post('/runs/'+str(s.run_id)+'/reviews',headers=headers,json={'action':'approved','expected_revision':current.revision}).status_code==409
        other=authenticated_headers(client,'Alex')
        assert client.get(url+'/review-package',headers=other).status_code in {403,404}
        # Missing or tampered media cannot become downloadable examples.
        path=settings.output_dir/'runs'/str(s.run_id)/'evaluation-examples'/f'revision-{s.revision:03d}'/'infographic.webp'
        path.write_bytes(b'tampered');assert not example_files(settings,w.storage,current)
        assert before.editorial==s.editorial


def test_only_exact_unchanged_review_context_is_retained(settings):
    with Workflow(settings) as w:
        before=fixture(settings,w);before.editorial['unresolved_questions']=['Resolved pricing input','Keep source question']
        # Save clean accepted results for these exact inputs.
        from src.services.qa_units import persist,schema_for
        for u in plan(before,settings)[1]:
            r=schema_for(u['kind'])(unit_id=u['id'],coverage_complete=True,passed=True,findings=[],assessments=[{'criterion':c,'complete':True} for c in u['request']['expected_criteria']])
            persist(w.storage,'qa-unit:'+str(before.run_id)+':'+u['dependency'],before.run_id,{'result':r.model_dump(),'reviewed_revision':before.revision})
        after=before.model_copy(deep=True);after.revision+=1;after.fee_presentation=policy(settings,after)
        after.editorial['unresolved_questions'].remove('Resolved pricing input')
        changed=after.editorial['modules'][0]['id'];after.editorial['modules'][0]['markdown']+='\n\nNeue Aussage zur Prüfung.'
        report=retain(w.storage,before,after,settings,resolved_release_inputs=['Resolved pricing input'])
        assert not report['complete'] and 0<report['completed']<report['total']
        for u in plan(after,settings)[1]:
            if u['id'] in {'page','module:'+changed,'module:L05','module:L14'}:assert cached_unit(w.storage,after,u) is None
        after.editorial['unresolved_questions']=[]
        with pytest.raises(ValueError):retain(w.storage,before,after,settings,resolved_release_inputs=['Resolved pricing input'])


def test_exact_saved_graphic_and_price_note_correction():
    from scripts.correct_revision_six import corrected,RESOLVED
    editorial={'modules':[
        {'id':'L05','markdown':'[PREIS FEHLT]'},
        {'id':'L14','markdown':'[PREIS FEHLT] Bitte fragen Sie die Kosten für den von Ihnen gewünschten Leistungsumfang persönlich an. Eine freigegebene Preisangabe für die hier dargestellten Leistungen liegt noch nicht vor.'},
        {'id':'L04a','markdown':'Für diesen Anfrage-Kompass ist eine Infografik vorgesehen. Die Tabelle enthält bereits das vollständige Textäquivalent; eine Bilddatei liegt diesem Entwurf nicht bei.'},
        {'id':'L08','markdown':'[FREIGEGEBENES STATEMENT FEHLT]'}],
        'graphic':{'facts':['a','b','c','d','e'],'notebooklm_prompt':'Original design brief'},'unresolved_questions':[RESOLVED,'Keep statement pending']}
    result=corrected(editorial,'Die Vergütung richtet sich nach der Steuerberatervergütungsverordnung (StBVV).')
    assert '[PREIS FEHLT]' not in json.dumps(result)
    assert 'eine Bilddatei liegt' not in json.dumps(result)
    assert result['graphic']['facts']==editorial['graphic']['facts']
    assert result['graphic']['notebooklm_prompt']==editorial['graphic']['notebooklm_prompt']
    assert result['modules'][3]==editorial['modules'][3]
    assert result['unresolved_questions']==['Keep statement pending']
    assert '€' not in json.dumps(result)


def test_three_images_are_preserved_only_as_previous_examples(settings,monkeypatch):
    from tests.test_evaluation_images import setup,job
    from src.services import evaluation_images as images
    from src.services.recovery import running_job
    with Workflow(settings) as w:
        s,calls=setup(settings,w,monkeypatch)
        j=job(w,s,images.preflight(settings,w.storage,s))
        with running_job(j,w.storage):images.execute(w,j)
        j.status='completed';w.storage.save_job(j)
        document=deepcopy(s.editorial);document['modules'][0]['markdown']+='\n\nSie nennen Ihr Anliegen.'
        current=save_edit(w,str(s.run_id),EditorialEditRequest(edit_id=uuid4(),expected_revision=s.revision,
            editorial=document,notes='Free local synthetic correction with prior outputs preserved.'),local_source='local_editorial_repair')
        z=zipfile.ZipFile(io.BytesIO(evaluation.package(settings,w.storage,current)))
        records=[json.loads(z.read(n)) for n in z.namelist() if n.startswith('previous-output-examples/') and n.endswith('/manifest.json')]
        assert len(records)==1 and len(records[0]['assets'])==4
        assert {a['kind'] for a in records[0]['assets']}=={'infographic','website','linkedin','google_business_profile'}
        assert not records[0]['current_revision_compatible'] and 'channel-images/manifest.json' not in z.namelist()
        assert len(calls)==1


def test_interrupted_free_example_snapshot_can_resume_without_overwriting_history(settings):
    from src.services.revision_examples import key,preserve
    with Workflow(settings) as w:
        state=fixture(settings,w)
        operation=key(state,state.revision)
        w.storage.begin_operation(operation,str(state.run_id))
        preserve(w,state)
        record=w.storage.get_operation(operation)
        assert record['status']=='completed' and len(record['value']['assets'])==1
        preserve(w,state)
        assert w.storage.get_operation(operation)==record


def test_legacy_edit_identity_does_not_gain_a_null_fee_field(settings):
    with Workflow(settings) as w:
        state=fixture(settings,w)
        request=EditorialEditRequest(edit_id=uuid4(),expected_revision=state.revision,
            editorial=state.editorial,notes='Existing clients do not send the optional fee policy.')
        original=request.model_dump(mode='json');original.pop('fee_presentation')
        expected=hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        updated=save_edit(w,str(state.run_id),request,local_source='local_editorial_repair')
        assert updated.editorial_change_history[-1]['fingerprint']==expected
        assert save_edit(w,str(state.run_id),request,local_source='local_editorial_repair').revision==updated.revision
        assert updated.fee_presentation is None
