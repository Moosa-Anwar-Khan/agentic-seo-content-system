from src.orchestrator.state import RunState
from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.services.export import LocalExporter

@register_agent("publisher_agent")
class PublisherAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        if state.status != 'approved' or not state.review_history or state.review_history[-1].action != 'approved':
            raise PermissionError('Local final export requires human approval')
        if not state.brand_check or not state.brand_check.passed or not state.seo_check or not state.seo_check.passed:
            raise PermissionError('Both QA gates must pass')
        # This agent has no WordPress mutation client or credentials.
        state.status='exported'
        destination=self.context.settings.output_dir/'runs'/str(state.run_id)/'approved'
        state.exported_paths=LocalExporter(self.context.settings.output_dir,self.context.settings).write_bundle(state,destination)
        return state
