import argparse
import json
from src.config.settings import Settings
from src.orchestrator.workflow import Workflow
from src.schemas.icp import load_icp
from src.schemas.review import ReviewRequest

def main():
    parser=argparse.ArgumentParser(description='Muster & Partner Agentic SEO MVP')
    parser.add_argument('--demo',action='store_true',help='Run offline and simulate one Alex revision; leave final approval pending')
    parser.add_argument('--icp',default='icp_1_buy_and_hold',help='Configured ICP JSON/YAML filename stem')
    args=parser.parse_args()
    import re
    if not re.fullmatch(r'[a-z0-9_]+',args.icp): parser.error('Invalid ICP ID')
    settings=Settings(demo_mode=True) if args.demo else Settings()
    candidates=[settings.input_dir/'icps'/f'{args.icp}{suffix}' for suffix in ['.json','.yaml','.yml']]
    path=next((p for p in candidates if p.is_file()),None)
    if path is None: parser.error('ICP configuration not found')
    icp=load_icp(path)
    with Workflow(settings) as workflow:
        state=workflow.start(icp)
        # CLI diagnostics are English; stored errors are German for the review UI.
        print(json.dumps({'run_id':str(state.run_id),'status':state.status,'revision':state.revision,'error':'Workflow failed; inspect configuration and run state.' if state.error else None},ensure_ascii=False,indent=2))
        if args.demo and state.status=='seo_check_passed':
            state=workflow.submit_review(str(state.run_id),ReviewRequest(action='needs_revision',feedback_target_agent='content_agent',feedback_notes='Bitte ergänzen Sie eine konkrete Checkliste für die Vorbereitung des Erstgesprächs.',expected_revision=state.revision),'Demo')
            print(json.dumps({'demo_review':'Simulated Alex revision; not a real human approval','status':state.status,'revision':state.revision,'preview':str(settings.output_dir/'runs'/str(state.run_id)/f'revision-{state.revision:03d}')},ensure_ascii=False,indent=2))
        if state.status=='failed': raise SystemExit(1)

if __name__=='__main__': main()
