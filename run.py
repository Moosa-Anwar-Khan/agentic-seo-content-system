"""Simple entry point; no command publishes to any platform."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description='Combined Muster SEO MVP')
    parser.add_argument('command',choices=['demo','serve','check'])
    args=parser.parse_args()
    from src.config.settings import Settings
    if args.command=='check':
        from src.services.readiness import readiness
        try:
            result=readiness(Settings())
        except Exception:
            print(json.dumps({'status':'needs_attention','label':'Configuration could not be loaded',
                'next_steps':['Check environment variable names, value formats and the encryption key. No credentials are printed.']}))
            raise SystemExit(1) from None
        print(json.dumps(result,indent=2))
        if result['status']!='ready':raise SystemExit(1)
        return
    if args.command=='serve':
        import uvicorn
        uvicorn.run('src.api.app:app',host='127.0.0.1',port=8000)
        return
    from cryptography.fernet import Fernet
    from src.orchestrator.workflow import Workflow
    from src.schemas.icp import load_icp
    from src.schemas.decisions import SelectionRequest
    from src.services.social import SocialService
    from src.services.user_errors import console_release_blockers
    settings=Settings(demo_mode=True,combined_mode=True,
                      auth_encryption_key=Fernet.generate_key().decode(),
                      data_dir=Path('var/demo'),output_dir=Path('outputs/demo'))
    with Workflow(settings) as workflow:
        state=workflow.start(load_icp(settings.input_dir/'icps/icp_1_buy_and_hold.json'))
        if state.status=='awaiting_selection':
            recommendation=next(r for r in state.analysis.recommendations if r.action!='protect')
            state=workflow.submit_selection(str(state.run_id),SelectionRequest(
                recommendation_id=recommendation.id,expected_revision=state.revision,
                acknowledge_uncertainty=True,notes='Offline-Demonstration mit synthetischen Inhalten; keine echte fachliche Freigabe.'),'Sam')
        posts=SocialService(settings,workflow.storage).prepare(state) if state.status=='seo_check_passed' else []
        print(json.dumps({'mode':'OFFLINE DEMO - UNAPPROVED PLACEHOLDERS',
              'run_id':str(state.run_id),'status':state.status,
              'pillars':len(state.topic_map.pillars) if state.topic_map else 0,
              'platforms':['website',*[p.platform for p in posts]],
              'release_blockers':console_release_blockers(state.release_blockers),
              'approval_note':'Expected demo limitations: sample content is unapproved. Original German findings remain in the saved review files.',
              'outputs':str(settings.output_dir.resolve()),'api_charges':0},indent=2,ensure_ascii=False))
        if state.status=='failed':raise SystemExit(1)


if __name__=='__main__':main()
