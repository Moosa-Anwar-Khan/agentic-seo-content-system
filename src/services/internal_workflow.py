"""Actual owner-led internal review/export; never professional or publication approval."""
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field
from src.schemas.common import StrictModel
from src.security.run_access import RunAccess
from src.security.sanitize import sanitize_text
from src.services.evaluation_vision import attachment
from src.services.professional_review import fingerprint as content_fingerprint

LABEL = 'Internally reviewed — professionally unapproved'
VERSION = 'internal-review-v1'

class InternalAction(StrictModel):
    action: Literal['enable', 'disable', 'review', 'approve', 'export']
    request_id: UUID
    expected_revision: int = Field(ge=0)
    context: str = Field(pattern=r'^[0-9a-f]{64}$')
    notes: str = Field(default='', max_length=2000)
    content_reviewed: bool = False
    sources_reviewed: bool = False
    graphic_reviewed: bool = False
    limitations_acknowledged: bool = False
    approve_internal_only: bool = False


def context(state, settings):
    from src.services.editorial_brand import PROFILE_VERSION
    return hashlib.sha256(json.dumps({'content': content_fingerprint(state),
        'policy': VERSION, 'owner_user_id':state.owner_user_id, 'active': bool(state.internal_workflow.get('active')),
        'fee_policy': state.fee_presentation.model_dump(mode='json') if state.fee_presentation else None,
        'brand': PROFILE_VERSION}, sort_keys=True).encode()).hexdigest()


def flags(state, settings):
    data=state.internal_workflow; ctx=context(state,settings)
    review=data.get('review') or {}; approval=data.get('approval') or {}; exported=data.get('export') or {}
    active=bool(data.get('active'))
    reviewed=active and review.get('context')==ctx
    approved=reviewed and approval.get('context')==ctx and approval.get('review_id')==review.get('id')
    exported=approved and exported.get('context')==ctx and exported.get('approval_id')==approval.get('id')
    return {'active':active,'reviewed':bool(reviewed),'approved':bool(approved),'exported':bool(exported)}


def prerequisites(settings,storage,state):
    errors=[]
    if state.status not in {'seo_check_passed','exported'} or not state.brand_check or not state.brand_check.passed or not state.seo_check or not state.seo_check.passed:
        errors.append('Complete genuine automated QA before internal review.')
    else:
        from src.services.qa_units import plan,cached_unit,schema_for,validate_unit
        from src.services.quality import QualityReviewError
        try:
            for unit in plan(state,settings)[1]:
                entry=cached_unit(storage,state,unit)
                if not entry:raise ValueError('Missing accepted check')
                result=validate_unit(state,unit,schema_for(unit['kind']).model_validate(entry['value']['result']))
                if not result.passed:raise ValueError('Content correction required')
        except (ValueError,KeyError,QualityReviewError):errors.append('Current QA coverage is incomplete or invalid. Resolve the normal QA findings first.')
    try:attachment(settings,state)
    except (ValueError,OSError):errors.append('Attach a valid infographic to the current revision first.')
    if any(j.status in {'queued','running','paused'} for j in storage.list_jobs(str(state.run_id))):
        errors.append('Resolve active or uncertain work first.')
    return errors



def outstanding_requirements(settings,state):
    from src.combined.editorial import release_errors
    from src.combined.evidence import source_issue
    from src.services.user_errors import console_release_blockers
    result=console_release_blockers(release_errors(state,settings))
    for source in state.research_packet.get('official_sources',[]):
        issue=source_issue(source,settings.tax_year)
        if issue:result.append('Source limitation: '+source.get('url','')+' — '+issue+'.')
    return list(dict.fromkeys(result))


def view(settings,storage,state):
    from src.combined.editorial import release_errors
    status=flags(state,settings)
    return {'label':LABEL,'version':VERSION,'revision':state.revision,'context':context(state,settings),**status,
        'blocking':prerequisites(settings,storage,state),
        'outstanding_release_requirements':outstanding_requirements(settings,state),
        'review':state.internal_workflow.get('review'),'approval':state.internal_workflow.get('approval'),
        'export':{k:v for k,v in (state.internal_workflow.get('export') or {}).items() if k!='path'},
        'professional_approval':'not granted by this workflow','publication':'not permitted by internal approval'}


def preview(settings,state):
    from bs4 import BeautifulSoup
    from src.services.presentation import render_current
    soup=BeautifulSoup(render_current(state,settings),'html.parser')
    note=soup.new_tag('aside',attrs={'class':'rs-notice'})
    note.string='Intern geprüft — fachlich nicht freigegeben. Nicht zur Veröffentlichung freigegeben.'
    soup.body.insert(0,note)
    return str(soup)


def bundle(settings,storage,state):
    from bs4 import BeautifulSoup
    from src.services.evaluation import package
    from src.services.website_package import start_page
    from src.combined.editorial import release_errors
    # Reuse the shared layout/assets packer, never the ordinary approved exporter.
    copy=state.model_copy(deep=True);copy.status='seo_check_passed';copy.professional_review=None
    source=package(settings,storage,copy);output=io.BytesIO();article=preview(settings,copy)
    receipts={k:v for k,v in state.internal_workflow.items() if k!='export'}
    receipts['events']=[{k:v for k,v in e.items() if k!='path'} for e in receipts.get('events',[])]
    requirements=outstanding_requirements(settings,state)
    with zipfile.ZipFile(io.BytesIO(source)) as original,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
        for name in original.namelist():
            data=original.read(name)
            if name=='evaluation.json':
                name='output-readiness.json';value=json.loads(data);value.update(label=LABEL,final_internal_approval='recorded',professional_release_approval='pending');data=json.dumps(value,ensure_ascii=False,indent=2).encode()
            if name=='requirements.json':
                value=json.loads(data);value.update(final_internal_approval='recorded',professional_release_final_approval='pending');data=json.dumps(value,ensure_ascii=False,indent=2).encode()
            if name=='export-manifest.json':
                value=json.loads(data);value.update(review_scope='internal_only',final_internal_approval='recorded',approval='professionally_unapproved');data=json.dumps(value,ensure_ascii=False,indent=2).encode()
            if name=='article.md':
                data=(LABEL+'\n'+data.decode().split('\n',1)[1]).encode()
            if name=='website/UPLOAD_INSTRUCTIONS.txt':
                data=data.replace(b'UNAPPROVED TEST OUTPUT',b'INTERNALLY REVIEWED; PROFESSIONALLY UNAPPROVED')
            if name in {'article.html','website/preview.html'}:data=article.encode()
            if name=='START_HERE.html':
                page=BeautifulSoup(data.decode(),'html.parser')
                page.title.string=LABEL
                if page.h1:page.h1.string=LABEL
                notice=page.new_tag('aside');notice.string='Actual internal review and final internal approval are recorded. Qualified tax-adviser approval and publication approval are NOT granted.'
                page.body.insert(0,notice);data=str(page).replace('Professional attestation and final approval remain pending.','Professional release approval remains pending; internal approval is recorded separately.').encode()
            if name=='README.txt':
                data=(LABEL+'\nRevision '+str(state.revision)+'\nOpen START_HERE.html. Actual internal review/approval receipts: internal-approval.json.\nNo tax-adviser attestation or publication approval. Outstanding inputs remain in requirements.json.\nOlder examples retain their original revision labels.\n').encode()
            target.writestr(name,data)
        target.writestr('internal-approval.json',json.dumps({'label':LABEL,'policy':VERSION,'revision':state.revision,
            'records':receipts,'outstanding_release_requirements':requirements,
            'professional_approval':False,'publication_approved':False},ensure_ascii=False,indent=2))
    return output.getvalue()


def act(workflow,run_id,request,user):
    store=workflow.storage;settings=workflow.settings
    with store.run_lock(run_id):
        state=workflow.get(run_id);RunAccess(store.account_store).manage(state,user)
        payload=request.model_dump(mode='json');identity=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        data=dict(state.internal_workflow);events=list(data.get('events',[]))
        prior=next((e for e in events if e['id']==str(request.request_id)),None)
        if prior:
            if prior['request_hash']!=identity or prior['actor_id']!=user['id']:raise ValueError('This request identifier already has different content.')
            store.save_run_state(state)  # Recover projection after a checkpoint-only save.
            return view(settings,store,state)
        if request.expected_revision!=state.revision or request.context!=context(state,settings):raise ValueError('Saved work changed. Refresh the internal workflow before proceeding.')
        if any(j.status in {'queued','running','paused'} for j in store.list_jobs(run_id)):raise ValueError('Resolve active or uncertain work first.')
        notes=sanitize_text(request.notes,2000)
        event={'id':str(request.request_id),'request_hash':identity,'action':request.action,'actor_id':user['id'],
            'actor_name':user['name'],'revision':state.revision,'context':context(state,settings),'notes':notes,
            'recorded_at':datetime.now(timezone.utc).isoformat(),'policy':VERSION}
        if request.action in {'enable','disable'}:
            if not request.limitations_acknowledged:raise ValueError('Confirm the internal-only policy and its limitations.')
            data['active']=request.action=='enable'
            # Switching policy cannot revive older internal approvals.
            for key in ['review','approval','export']:data.pop(key,None)
        else:
            if not data.get('active'):raise ValueError('Enable the internal-only workflow first.')
            blockers=prerequisites(settings,store,state)
            if blockers:raise ValueError(' '.join(blockers))
            current=flags(state,settings)
            if request.action=='review':
                if not all([request.content_reviewed,request.sources_reviewed,request.graphic_reviewed,request.limitations_acknowledged]) or len(notes)<10:
                    raise ValueError('Inspect the outputs, acknowledge unresolved requirements and enter a review note.')
                from src.combined.editorial import release_errors
                event['outstanding_release_requirements']=outstanding_requirements(settings,state)
                data['review']=event
                for key in ['approval','export']:data.pop(key,None)
            elif request.action=='approve':
                if not current['reviewed'] or not request.approve_internal_only or not request.limitations_acknowledged:
                    raise ValueError('Record current internal review and explicitly approve internal use only.')
                event['review_id']=data['review']['id'];data['approval']=event;data.pop('export',None)
            elif request.action=='export':
                if not current['approved']:raise ValueError('Record current final internal approval first.')
                raw=bundle(settings,store,state);digest=hashlib.sha256(raw).hexdigest()
                directory=settings.output_dir/'runs'/run_id/'internal-exports';directory.mkdir(parents=True,exist_ok=True)
                path=directory/(str(request.request_id)+'.zip');temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(path)
                event.update(approval_id=data['approval']['id'],path=str(path.resolve()),sha256=digest)
                data['export']=event
        events.append(event);data['events']=events;state.internal_workflow=data
        state.updated_at=datetime.now(timezone.utc)
        workflow.graph.update_state(workflow._config(run_id),{'run':state.model_dump(mode='json')})
        store.save_run_state(state)
        return view(settings,store,state)


def download(settings,storage,state):
    if not flags(state,settings)['exported'] or prerequisites(settings,storage,state):raise ValueError('Create a current internally approved export first.')
    record=state.internal_workflow['export'];root=(settings.output_dir/'runs'/str(state.run_id)/'internal-exports').resolve()
    path=Path(record['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('Internal export file unavailable.')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Internal export hash changed. Inspect the saved artifact.')
    return raw
