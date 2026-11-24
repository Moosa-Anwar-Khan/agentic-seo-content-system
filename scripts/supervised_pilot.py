"""Trusted-local, explicitly authorized pilot. Default action is read-only status.

This command can start ONE capped run and select an actual eligible blog topic.
It cannot retry requests, create reviewers, attest, approve, generate images or
publish. Jobs retain the existing operator identity and worker authorization.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from filelock import FileLock

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.config.settings import Settings
from src.orchestrator.workflow import Workflow
from src.orchestrator.state import RunState
from src.schemas.jobs import Job
from src.schemas.icp import load_icp
from src.schemas.decisions import SelectionRequest,SelectionEvent
from src.security.run_access import RunAccess
from src.security.auth import AccountStore
from src.services.seo_decisions import validate_selection
from src.services.progress import progress

MARKER=ROOT/'var/assessment/supervised-pilot.json'
PARENT='873ac975-6ad0-4aa8-a43f-f89f02c40a1c'


def save(value):
    temporary=MARKER.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temporary.chmod(0o600);temporary.replace(MARKER)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','start','select'],nargs='?',default='status')
    parser.add_argument('--campaign',choices=['original','writer-upgrade'],default='original')
    parser.add_argument('--allow-paid',action='store_true',help='Explicitly authorize this paid pilot stage within this campaign’s immutable cap')
    global MARKER
    args=parser.parse_args();settings=Settings()
    upgraded=args.campaign=='writer-upgrade'
    if upgraded:MARKER=ROOT/'var/assessment/writer-upgrade-pilot.json'
    if args.action!='status' and not args.allow_paid:parser.error('Paid stages require explicit --allow-paid authorization')
    with FileLock(str(MARKER)+'.lock'), Workflow(settings) as workflow:
        workflow.storage.account_store=AccountStore(workflow.storage)
        if args.action=='start':
            if MARKER.exists():raise ValueError('One pilot already exists. Inspect status; do not create replacement runs.')
            if settings.demo_mode:raise ValueError('Live mode is required; private settings will not be changed by this command.')
            from src.services.readiness import readiness
            if readiness(settings,workflow.storage)['status']!='ready':raise ValueError('Resolve local readiness issues before spending.')
            if any(job.status in {'queued','running','paused'} for job in workflow.storage.list_jobs()):raise ValueError('Inspect existing active or uncertain work first.')
            parent=workflow.get(PARENT)
            actor=workflow.storage.account_store.user('id',parent.owner_user_id)
            if not RunAccess.active(actor):raise PermissionError('The original operator must be active.')
            state=RunState(icp=load_icp(settings.input_dir/'icps/icp_1_buy_and_hold.json'),owner_user_id=actor['id'],
                run_budget_usd=min(1.0 if upgraded else .25,settings.max_run_usd),
                internal_link_policy='starter_library' if upgraded else 'strict',decision_workflow_version=1)
            job=Job(run_id=state.run_id,kind='start',reviewer=actor['name'],reviewer_id=actor['id'],idempotency_key=uuid4())
            marker={'run_id':str(state.run_id),'owner_user_id':actor['id'],'start_job_id':str(job.id),
                'start_idempotency_key':str(job.idempotency_key),'budget_usd':state.run_budget_usd,
                'created_at':datetime.now(timezone.utc).isoformat(),'selection_job_id':None}
            save(marker)
            workflow.storage.create_job(job,state)
            print(json.dumps({'queued':'fresh research','run_id':str(state.run_id),'immutable_budget_usd':state.run_budget_usd}))
            return
        if not MARKER.exists():print('No supervised pilot exists. No work was submitted.');return
        marker=json.loads(MARKER.read_text());state=workflow.get(marker['run_id'])
        if args.action=='select':
            if marker['selection_job_id']:raise ValueError('A selection was already submitted. Inspect its saved status; do not repeat paid work.')
            actor=workflow.storage.account_store.user('id',marker['owner_user_id'])
            RunAccess(workflow.storage.account_store).drafting(state,actor)
            if state.status!='awaiting_selection':raise ValueError('Research has not completed successfully; no selection was submitted.')
            choices=[r for r in state.analysis.recommendations if r.page_type=='blog' and r.drafting_eligible and r.action in {'create_new','create_cluster','optimize','manual_review'}
                     and (not upgraded or (not r.uncertainties and not r.research_needed and r.action!='manual_review'))]
            interests=['werbungskosten','kaufpreisaufteilung','afa','vermietung','erwerbsnebenkosten','spekulationsfrist']
            choices.sort(key=lambda r:(r.research_needed,next((i for i,term in enumerate(interests) if term in r.keyword.casefold()),len(interests)),-r.priority,r.id))
            for chosen in choices:
                if upgraded:
                    from src.services.preflight import inspect
                    preflight=inspect(workflow,state,chosen.id)
                    if preflight['status']=='blocked':continue
                request=SelectionRequest(recommendation_id=chosen.id,expected_revision=state.revision,mode='draft',
                  acknowledge_uncertainty=not upgraded,notes='Betreuter ICP1-Test: Informationsartikel auf Basis der gespeicherten Recherche. Fachliche Freigabe und Veröffentlichung bleiben ausstehend.')
                try:validate_selection(state,request)
                except ValueError:continue
                break
            else:
                if upgraded:
                    marker['selection_stop_reason']='open_topic_selection_assumptions'
                    save(marker)
                    print(json.dumps({'stopped':'An informational topic needs an explicit evidence-based selection decision. No assumptions were acknowledged and no writing job was submitted.','run_id':str(state.run_id)}))
                    return
                raise ValueError('No eligible informational article exists in the actual recommendations. Saved research needs a professional topic decision.')
            event=SelectionEvent(**request.model_dump(),reviewer_name=actor['name'],reviewer_id=actor['id'],event_id=str(uuid4()))
            job=Job(run_id=state.run_id,kind='selection',reviewer=actor['name'],reviewer_id=actor['id'],
                idempotency_key=uuid4(),payload={'event':event.model_dump(mode='json'),**({'stop_on_quality_findings':True} if upgraded else {})})
            marker.update(selection_job_id=str(job.id),selection_idempotency_key=str(job.idempotency_key),
                selected_recommendation=chosen.model_dump(mode='json'))
            save(marker);workflow.storage.create_job(job)
            print(json.dumps({'queued':'selected informational article','run_id':str(state.run_id),'keyword':chosen.keyword,'page_type':chosen.page_type},ensure_ascii=False))
            return
        report=progress(workflow,state)
        (MARKER.parent/('writer-upgrade-progress.json' if upgraded else 'supervised-pilot-progress.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n')
        print(json.dumps({k:report[k] for k in ['run_id','revision','state','status','tokens_used','qa','costs','latest_attempt']},ensure_ascii=False,default=str))


if __name__=='__main__':main()
