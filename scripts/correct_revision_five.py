"""Authorized, idempotent local repair of the saved service draft; no providers."""
from pathlib import Path
import json
import sys
from uuid import UUID, uuid5

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
RUN='d7d478a8-fd6d-4837-918d-4335de37b506'
EDIT=uuid5(UUID(RUN),'revision-five-approved-wording-and-sevdesk-v1')
SEVDESK='https://steuerkanzlei.example/sevdesk-zusammenarbeit/'


def corrected(editorial):
    from copy import deepcopy
    draft=deepcopy(editorial)
    replacements={
        'L10': [
            ('Wir verfügen über 12 Jahre Erfahrung in der digitalen Steuerberatung.',
             '\n\n**Über 12 Jahre Erfahrung in der digitalen Steuerberatung.**\n\n'),
            ('**48 h Rückmeldung**','**Rückmeldung innerhalb von 48 Stunden**'),
            ('**21 Tage Wechsel**','**Wechsel zu uns in 21 Tagen, vollständig digital**')],
        'L07': [
            ('Eine bestimmte Rückrufzeit oder eine Aufnahmezusage ist mit Ihrer Anfrage nicht verbunden.',
             'Eine Aufnahmezusage ist mit Ihrer Anfrage nicht verbunden.'),
            ('Diese Seite legt keine zusätzlichen Prozessschritte, Termine oder technischen Übertragungswege fest.',
             'Die konkreten nächsten Schritte und technischen Übergaben stimmen Sie persönlich mit uns ab.')],
        'L14': [
            ('Zur Einordnung allgemeiner Kostenfaktoren können Sie unseren [Überblick zu Buchhaltungskosten](https://steuerkanzlei.example/buchhaltungskosten/) lesen. Die konkreten Konditionen sind persönlich zu vereinbaren.', ''),
            ('Eine feste Wechseldauer oder eine bestimmte Form der Datenübertragung wird hier nicht zugesagt.',
             'Welche Unterlagen benötigt werden und wie die Datenübergabe in Ihrem Fall abläuft, stimmen Sie bitte persönlich mit uns ab.')]
    }
    for module in draft['modules']:
        for before,after in replacements.get(module['id'],[]):
            if module['markdown'].count(before)!=1:
                raise ValueError('The baseline changed in '+module['id']+'; inspect it before editing')
            module['markdown']=module['markdown'].replace(before,after)
        if module['id']=='L14':
            # Add the conditional resource within the existing digital FAQ;
            # headings and every other approved module remain untouched.
            import re
            parts=re.split(r'(?m)(?=^### )',module['markdown'])
            candidates=[i for i,p in enumerate(parts) if p.splitlines() and
                'digital' in p.splitlines()[0].casefold()]
            if len(candidates)!=1:raise ValueError('Identify the digital bookkeeping FAQ before editing')
            i=candidates[0]
            parts[i]=parts[i].rstrip()+'\n\nWenn Sie sevDesk nutzen oder prüfen, finden Sie Hinweise in unserem [Leitfaden: sevDesk-Steuerberater finden und einladen]('+SEVDESK+').\n\n'
            module['markdown']=''.join(parts).strip()
    return draft


def main():
    from src.config.settings import Settings
    from src.orchestrator.workflow import Workflow
    from src.schemas.review import EditorialEditRequest
    from src.combined.models import EditorialOutput
    from src.security.auth import AccountStore
    from src.services.editorial_edits import save_edit
    from src.services.qa_units import plan,estimate_units
    from src.services.qa_context import saved_page_text
    from src.services.progress import progress
    settings=Settings()
    with Workflow(settings) as w:
        # Defense in depth: this maintenance command cannot call a text provider.
        def no_provider(*args,**kwargs):raise AssertionError('Paid requests are forbidden during local repair')
        w.context.llm.generate=no_provider
        w.storage.account_store=AccountStore(w.storage)
        state=w.get(RUN)
        before=w.storage.campaign_costs(RUN)
        calls=[c.model_dump(mode='json') for c in w.storage.list_ai_calls(RUN)]
        if not any(item['edit_id']==str(EDIT) for item in state.editorial_change_history):
            if state.revision!=4:raise ValueError('Expected saved revision 4')
            assert not any(j.status in {'queued','running','paused'} for j in w.storage.list_jobs(RUN))
            page=next(p for p in state.analysis.page_evidence if p.url==SEVDESK)
            assert saved_page_text(page) and page.sections,'Complete saved source required'
            draft=EditorialOutput.model_validate(corrected(state.editorial))
            assert draft.claims==EditorialOutput.model_validate(state.editorial).claims
            candidate=state.model_copy(deep=True);candidate.editorial=draft.model_dump(mode='json')
            estimate_units(settings,plan(candidate,settings)[1])
            actor=w.storage.account_store.user('id',state.owner_user_id)
            state=save_edit(w,RUN,EditorialEditRequest(edit_id=EDIT,expected_revision=4,editorial=draft,
                notes='Authorized free local revision: clarify approved service wording, remove contradictory disclaimers, and substitute the saved verified sevDesk resource in digital bookkeeping guidance. Keep all release inputs pending.'),actor=actor)
        assert state.revision==5 and state.brand_check.passed
        assert state.seo_check is None and state.professional_review is None
        assert before==w.storage.campaign_costs(RUN)
        assert calls==[c.model_dump(mode='json') for c in w.storage.list_ai_calls(RUN)]
        report=progress(w,state)
        out=settings.output_dir/'runs'/RUN/'revision-005'
        summary={'run_id':RUN,'revision':5,'state':report['state'],'paid_calls_added':0,
            'brand_passed':state.brand_check.passed,'semantic_review':'pending',
            'qa':report['qa'],'review_plan':report['review_plan'],'costs':before,
            'changed_modules':['L07','L10','L14'],'release_approvals':'pending'}
        (out/'reviews/local-correction-verification.json').write_text(json.dumps(summary,indent=2,default=str))
        (out/'REVIEW_SUMMARY_EN.md').write_text(
            '# Locally corrected — semantic review pending\n\n'
            'Revision 5 clarifies the approved experience, response and digital switching statements; removes conflicting disclaimers; '
            'and replaces the disputed cost resource with the saved verified sevDesk guide under digital bookkeeping. The Lexware guide and target URL are unchanged.\n\n'
            'Deterministic checks passed. No semantic review has been purchased for this revision and no QA pass or approval has been granted. '
            'Earlier drafts, paid responses, findings and costs remain saved.\n\n'
            'Customer reviews are optional. Approved statement and pricing/fee wording, the revision-specific infographic, professional attestation and final approval remain pending. '
            'The infographic brief is in briefings/infographic.json; it is an instruction document, not an image.\n\n'
            'Read this draft and the displayed remaining-unit estimate. Another paid semantic review requires separate explicit confirmation. Publishing remains manual.\n')
        print(json.dumps(summary,indent=2,default=str))


if __name__=='__main__':main()
