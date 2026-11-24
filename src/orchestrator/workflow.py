import json
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict
from uuid import uuid4
from pydantic import Field, model_validator
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from src.agents.base import AgentContext
from src.config.settings import Settings
from src.integrations.sources import Sources
from src.orchestrator.registry import registry
from src.orchestrator.state import RunState
from src.schemas.common import StrictModel
from src.schemas.review import ReviewRequest, ReviewEvent
from src.services.export import LocalExporter
from src.services.llm import ModelRouter
from src.services.llm import ModelRequestError
from src.services.model_routes import ModelConfigurationError
from src.services.model_routes import resolve_route
from src.services.recovery import current_job
from src.schemas.decisions import SelectionRequest, SelectionEvent
from src.services.seo_decisions import validate_selection
from src.storage.base import BaseStorage
from src.storage.factory import create_storage, create_checkpointer

class AgentSpec(StrictModel):
    name: str = Field(pattern=r'^[a-z][a-z0-9_]+$')
    role: Literal['standard','briefing','content','brand','seo','export']

class PipelineConfig(StrictModel):
    modules: list[str]
    agents: list[AgentSpec] = Field(min_length=7)

    @model_validator(mode='after')
    def valid_order(self):
        names=[a.name for a in self.agents]
        if len(set(names)) != len(names) or any(n in {'human_review','repair','planning_selection'} for n in names):
            raise ValueError('Duplicate or reserved agent name')
        roles=[a.role for a in self.agents]
        for role in ['briefing','content','brand','seo','export']:
            if roles.count(role)!=1: raise ValueError('Exactly one '+role+' role is required')
        positions=[roles.index(r) for r in ['briefing','content','brand','seo','export']]
        if positions != sorted(positions) or roles[-1]!='export':
            raise ValueError('Required order: briefing, content, brand, seo, export')
        if any(next((a.name for a in self.agents if a.role==role),None)!=name for role,name in [('briefing','briefing_agent'),('content','content_agent')]):
            raise ValueError('Stable revision targets are briefing_agent and content_agent')
        return self

class GraphState(TypedDict):
    # Plain JSON is checkpointed, avoiding arbitrary class deserialization.
    run: dict[str, Any]

class Workflow:
    def __init__(self, settings: Settings | None = None, storage: BaseStorage | None = None, checkpointer=None):
        self.settings=settings or Settings()
        self.storage=storage or create_storage(self.settings)
        self.stack=ExitStack()
        self.settings.data_dir.mkdir(parents=True,exist_ok=True)
        if checkpointer is None:
            checkpointer=create_checkpointer(self.settings,self.stack)
        self.checkpointer=checkpointer
        config_path=self.settings.input_dir/'config/pipeline.json'
        self.pipeline=PipelineConfig.model_validate_json(config_path.read_text(encoding='utf-8'))
        registry.load_modules(self.pipeline.modules)
        self.context=AgentContext(self.settings,self.storage,ModelRouter(self.settings,self.storage),Sources(self.settings))
        self.agents={spec.name:registry.get(spec.name)(self.context) for spec in self.pipeline.agents}
        self.graph=self._build()

    def __enter__(self): return self
    def __exit__(self,*args): self.stack.close()

    def _config(self,run_id: str):
        return {'configurable':{'thread_id':run_id},'recursion_limit':200}

    def _save(self,state: RunState) -> GraphState:
        saved=self.storage.get_run_state(str(state.run_id))
        if saved:
            # Observations are independent of editorial revisions/checkpoints.
            merged={o['id']:o for o in state.visibility_observations}
            merged.update({o['id']:o for o in saved.visibility_observations})
            state.visibility_observations=list(merged.values())
            from src.services.visibility_audit import restore_saved_signals
            restore_saved_signals(state,saved)
        store = getattr(self.storage, 'account_store', None)
        if store:
            from src.security.run_access import RunAccess
            state.owner_user_id = RunAccess(store).owner(state)
        state.updated_at=datetime.now(timezone.utc)
        state.tokens_used=self.storage.tokens_used(str(state.run_id))
        self.storage.save_run_state(state)
        return {'run':state.model_dump(mode='json')}

    def _agent_node(self,name: str):
        role = next(spec.role for spec in self.pipeline.agents if spec.name == name)
        def node(envelope: GraphState):
            state=RunState.model_validate(envelope['run'])
            if self.settings.combined_mode: state.combined_version = 1
            context = current_job.get()
            if context:
                context.step(name)
            try:
                from src.combined.costs import paid_scope
                from src.services.failures import boundary
                with boundary(name, 'Execute workflow step'), paid_scope(self.settings,self.storage,str(state.run_id)):
                    state=self.agents[name].execute(state)
                self.agents[name].validate(state)
                state.executed_agents.append(name)
                if state.quality_checks_only and role in {'brand', 'seo'}:
                    check = state.brand_check if role == 'brand' else state.seo_check
                    if check is not None and not check.passed:
                        state.status = 'failed'
                        state.error = 'Die erneute Qualitätsprüfung wurde nicht bestanden. Der gespeicherte Entwurf wurde nicht verändert.'
                    elif role == 'seo' and check is not None and check.passed:
                        state.quality_checks_only = False
            except Exception as exc:
                if context:
                    raise
                # Never log raw provider responses / credentials or input text.
                import logging
                logging.getLogger(__name__).error('%s failed (%s); inspect configuration or sanitized inputs and start a new run.', name, type(exc).__name__)
                state.status='failed'
                from src.services.failures import capture
                event=capture(self.storage,code=getattr(exc,'code','configuration' if isinstance(exc,ModelConfigurationError) else 'processing'),exc=exc,state=state,component=name)
                state.agent_outputs['diagnostic_failure']=event['summary']
                if isinstance(exc, (ModelConfigurationError, ModelRequestError)):
                    task = {'seo_strategy_agent': 'seo_strategy', 'topic_map_agent': 'topic_map',
                            'briefing_agent': 'briefing', 'content_agent': 'content',
                            'brand_agent': 'brand', 'seo_qa_agent': 'seo_qa'}.get(name, name)
                    try:
                        route = exc.route if isinstance(exc, ModelRequestError) else resolve_route(self.settings, task)
                        label = f'{route.provider}/{route.model}'
                    except ModelConfigurationError:
                        label = 'unbekannt'
                    state.error=f'Modellfehler bei {task} ({label}). Bitte prüfen Sie Modell und Zugangsdaten und starten Sie einen neuen Lauf.'
                else:
                    state.error=f'Fehler im Verarbeitungsschritt {name}. Bitte prüfen Sie die Konfiguration oder die bereinigten Eingaben und starten Sie einen neuen Lauf.'
            return self._save(state)
        return node

    def _build(self):
        builder=StateGraph(GraphState)
        specs=self.pipeline.agents
        export_name=next(s.name for s in specs if s.role=='export')
        for spec in specs:
            builder.add_node(spec.name,self._agent_node(spec.name))
        builder.add_node('human_review',self._human_review)
        builder.add_node('repair',self._repair)
        builder.add_node('planning_selection', self._planning_selection)
        builder.add_edge(START,specs[0].name)
        for i,spec in enumerate(specs):
            if spec.role=='export':
                builder.add_edge(spec.name,END)
            else:
                next_name=specs[i+1].name
                if next_name==export_name: next_name='human_review'
                def route(envelope,role=spec.role,following=next_name,name=spec.name):
                    state=RunState.model_validate(envelope['run'])
                    if state.status=='failed': return END
                    if name == 'topic_map_agent' and state.decision_workflow_version:
                        return 'planning_selection'
                    check=state.brand_check if role=='brand' else state.seo_check if role=='seo' else None
                    if check is not None and not check.passed: return 'repair'
                    return following
                builder.add_conditional_edges(spec.name,route)
        builder.add_conditional_edges('repair',lambda e: END if e['run']['status']=='failed' else e['run']['feedback_target_agent'])
        builder.add_conditional_edges('planning_selection', lambda e: END if e['run']['status'] == 'recommendations_only' else 'briefing_agent')
        builder.add_conditional_edges('human_review',lambda e: export_name if e['run']['status']=='approved' else e['run']['feedback_target_agent'] if e['run']['status']=='needs_revision' else END)
        return builder.compile(checkpointer=self.checkpointer)

    def _planning_selection(self, envelope: GraphState):
        state = RunState.model_validate(envelope['run'])
        # Recommendation generation already completed in the checkpointed Topic Map node.
        answer = interrupt({'run_id': str(state.run_id), 'revision': state.revision,
                            'status': 'awaiting_selection', 'allowed_reviewers': ['Alex', 'Sam']})
        event = SelectionEvent.model_validate(answer)
        self._authorize_drafting(state, event.reviewer_id, event.reviewer_name, selection=True)
        chosen = validate_selection(state, event)
        state.selected_recommendation = chosen
        if state.research_snapshot:
            from src.services.seo_decisions import selected_context
            import hashlib,json
            package=selected_context(state)
            package.update({'research_run_id':str(state.run_id),'snapshot_created_at':state.research_snapshot.get('created_at'),
                'audience':state.icp.model_dump(mode='json'),'professional_assessment':event.model_dump(mode='json')})
            package['evidence_hash']=hashlib.sha256(json.dumps(package,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            state.selected_evidence=package
        if not any(item.event_id == event.event_id for item in state.selection_history):
            state.selection_history.append(event)
        state.revision += 1
        state.status = 'recommendations_only' if event.mode == 'links_only' else 'topic_map_done'
        return self._save(state)

    def select_queued(self, run_id: str, event: SelectionEvent) -> RunState:
        with self.storage.run_lock(run_id):
            checkpoint = self.graph.get_state(self._config(run_id))
            state = self.get(run_id)
            if any(item.event_id == event.event_id for item in state.selection_history):
                if checkpoint.next and checkpoint.next not in {('human_review',), ('planning_selection',)}:
                    return self._result(self.graph.invoke(None, self._config(run_id), durability='sync'))
                return self._result({'run': state.model_dump(mode='json')})
            validate_selection(state, event)
            if checkpoint.next != ('planning_selection',):
                raise ValueError('Run is not awaiting planning selection')
            return self._result(self.graph.invoke(Command(resume=event.model_dump(mode='json')),
                                                  self._config(run_id), durability='sync'))

    def _authorize_drafting(self, state, actor_id, name, *, selection=False):
        store = getattr(self.storage, 'account_store', None)
        if actor_id and store:
            from src.security.run_access import RunAccess
            actor = store.user('id', actor_id)
            RunAccess(store).require(state, actor)
            if selection:
                RunAccess(store).drafting(state, actor)
            if actor['name'] != name:
                raise PermissionError('Review identity does not match the account')
        elif state.owner_user_id or name not in {'Alex', 'Sam'}:
            raise PermissionError('An authorized account is required')

    def _require_review(self, state, event):
        from src.combined.pipeline import require_release
        allowed = False
        if event.action == 'needs_revision' and event.reviewer_id:
            self._authorize_drafting(state, event.reviewer_id, event.reviewer_name)
            allowed = True
        require_release(self.settings, state, event, allow_correction=allowed)

    def submit_selection(self, run_id: str, request: SelectionRequest, reviewer_name: str,
                         reviewer_id: str | None = None) -> RunState:
        return self.select_queued(run_id, SelectionEvent(**request.model_dump(), reviewer_name=reviewer_name,
                                 reviewer_id=reviewer_id, event_id=str(uuid4())))

    def _repair(self,envelope: GraphState):
        state=RunState.model_validate(envelope['run'])
        failed=state.brand_check if state.brand_check and not state.brand_check.passed else state.seo_check
        context=current_job.get()
        if not self.settings.demo_mode or (context and context.job.payload.get('stop_on_quality_findings')):
            state.status='failed'
            state.error='Der Entwurf ist gespeichert. Die Befunde erfordern eine ausdrückliche Entscheidung; für diesen betreuten Test wurde keine automatische kostenpflichtige Korrektur gestartet.'
            return self._save(state)
        import hashlib
        fingerprint = hashlib.sha256(json.dumps(
            {'draft': state.editorial or (state.article.model_dump(mode='json') if state.article else None),
             'briefing': state.briefing.model_dump(mode='json') if state.briefing else None,
             'errors': sorted(failed.errors) if failed else [], 'target': failed.target if failed else None},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if fingerprint in state.quality_repair_fingerprints:
            state.status = 'failed'
            state.error = 'Die automatische Korrektur hat denselben Entwurf mit denselben Befunden zurückgegeben. Weitere kostenpflichtige Wiederholungen wurden gestoppt; der Entwurf ist gespeichert.'
            return self._save(state)
        if state.auto_revisions >= self.settings.max_auto_revisions or failed is None:
            state.status='failed'
            state.error='Die Qualitätsprüfung wurde innerhalb der festgelegten Überarbeitungsgrenze nicht bestanden. Eine manuelle Prüfung ist erforderlich.'
            return self._save(state)
        state.quality_repair_fingerprints.append(fingerprint)
        state.auto_revisions+=1
        state.revision+=1
        state.status='needs_revision'
        state.feedback_target_agent=failed.target
        state.human_feedback='; '.join(failed.errors)
        return self._save(state)

    def _human_review(self,envelope: GraphState):
        state=RunState.model_validate(envelope['run'])
        # No non-idempotent work before interrupt: LangGraph replays this node on resume.
        answer=interrupt({'run_id':str(state.run_id),'revision':state.revision,'status':state.status,'allowed_reviewers':['Alex','Sam'],'draft_title':state.article.title if state.article else None})
        event=ReviewEvent.model_validate(answer)
        self._require_review(state, event)
        if event.expected_revision != state.revision:
            raise ValueError('Stale review revision')
        if not any(e.event_id==event.event_id for e in state.review_history):
            state.review_history.append(event)
        self.storage.log_human_feedback(str(state.run_id),event)
        state.status=event.action
        if event.action=='needs_revision':
            state.revision+=1
            state.human_revisions+=1
            state.human_feedback=event.feedback_notes
            state.feedback_target_agent=event.feedback_target_agent
        else:
            state.human_feedback=''
        return self._save(state)

    def _result(self,result: GraphState) -> RunState:
        state=RunState.model_validate(result['run'])
        self._save(state)
        if state.status=='failed' and current_job.get() is None and not state.agent_outputs.get('diagnostic_failure'):
            from src.services.failures import capture
            capture(self.storage,code='quality_failed',state=state)
        if state.status=='seo_check_passed' or (state.status=='failed' and state.topic_map and state.briefing and state.article):
            # Local preview artifacts are not a final approved export.
            LocalExporter(self.settings.output_dir,self.settings).write_bundle(state)
        elif state.status in {'awaiting_selection', 'recommendations_only'}:
            LocalExporter(self.settings.output_dir,self.settings).write_planning_bundle(state)
        return state

    def start(self,icp, owner_user_id: str | None = None) -> RunState:
        from src.security.sanitize import sanitize_payload
        sanitize_payload(icp.model_dump())
        if (self.settings.combined_mode or self.settings.demo_mode) and icp.id!="icp_1_buy_and_hold":
            raise ValueError("Offline fixtures cover ICP 1 only; configure live inputs for another ICP")
        state=RunState(icp=icp, owner_user_id=owner_user_id, decision_workflow_version=int(self.settings.seo_decisions_enabled), combined_version=int(self.settings.combined_mode))
        with self.storage.run_lock(str(state.run_id)):
            self.storage.save_run_state(state)
            return self._result(self.graph.invoke({'run':state.model_dump(mode='json')},self._config(str(state.run_id)),durability='sync'))

    def continue_queued(self, run_id: str) -> RunState:
        with self.storage.run_lock(run_id):
            checkpoint = self.graph.get_state(self._config(run_id))
            if checkpoint.values:
                interrupted = checkpoint.next in {('human_review',), ('planning_selection',)} and any(task.interrupts for task in checkpoint.tasks)
                if not checkpoint.next or interrupted:
                    return self._result({'run': self.get(run_id).model_dump(mode='json')})
                result = self.graph.invoke(None, self._config(run_id), durability='sync')
            else:
                state = self.storage.get_run_state(run_id)
                if state is None:
                    raise KeyError('Run not found')
                result = self.graph.invoke({'run': state.model_dump(mode='json')},
                                           self._config(run_id), durability='sync')
            return self._result(result)

    def quality_checks_queued(self, run_id: str, expected_revision: int) -> RunState:
        """Explicitly review a retained draft; do not restart drafting or reset repair limits."""
        with self.storage.run_lock(run_id):
            state = self.get(run_id)
            if state.revision != expected_revision:
                raise ValueError('Stale quality-review revision')
            checkpoint = self.graph.get_state(self._config(run_id))
            if state.status == 'seo_check_passed' and checkpoint.next == ('human_review',):
                return self._result({'run': state.model_dump(mode='json')})
            if (not state.article or not state.briefing or not state.html or
                    state.status not in {'failed', 'draft_created', 'brand_check_passed'}):
                raise ValueError('No retained draft available for quality review')
            quality_nodes = {spec.name for spec in self.pipeline.agents if spec.role in {'brand', 'seo'}}
            resuming = state.quality_checks_only and checkpoint.next and set(checkpoint.next).issubset(quality_nodes)
            if not resuming:
                state.quality_checks_only = True
                state.status = 'draft_created'
                state.error = None
                state.brand_check = None
                state.seo_check = None
                # A durable checkpoint makes a server restart resume the same checks.
                self.graph.update_state(self._config(run_id), {'run': state.model_dump(mode='json')},
                                        as_node='content_agent')
                self._save(state)
            return self._result(self.graph.invoke(None, self._config(run_id), durability='sync'))

    def review_queued(self, run_id: str, event: ReviewEvent) -> RunState:
        with self.storage.run_lock(run_id):
            checkpoint = self.graph.get_state(self._config(run_id))
            state = self.get(run_id)
            if any(item.event_id == event.event_id for item in state.review_history):
                if checkpoint.next and checkpoint.next != ('human_review',):
                    return self._result(self.graph.invoke(None, self._config(run_id), durability='sync'))
                return self._result({'run': state.model_dump(mode='json')})
            self._require_review(state, event)
            if event.action == 'needs_revision' and state.human_revisions >= self.settings.max_human_revisions:
                raise ValueError('Human revision limit reached; reject or create a new run')
            if event.action == 'needs_revision' and state.status == 'failed':
                return self._correct_failed_draft(state, event)
            if state.status != 'seo_check_passed' or 'human_review' not in checkpoint.next:
                raise ValueError('Run is not awaiting human review')
            if event.expected_revision != state.revision:
                raise ValueError('Stale review revision')
            return self._result(self.graph.invoke(Command(resume=event.model_dump(mode='json')),
                                                  self._config(run_id), durability='sync'))

    def _correct_failed_draft(self, state, event):
        """An explicit correction creates a revision; it never grants approval."""
        if (state.revision != event.expected_revision or not state.article or not state.briefing or
                not state.html or not any(check and not check.passed for check in (state.brand_check, state.seo_check))):
            raise ValueError('A current saved draft with failed quality checks is required')
        if any(job.status == 'paused' for job in self.storage.list_jobs(str(state.run_id))):
            raise ValueError('Inspect uncertain provider requests before requesting corrections')
        state.review_history.append(event)
        self.storage.log_human_feedback(str(state.run_id), event)
        state.revision += 1
        state.human_revisions += 1
        state.human_feedback = event.feedback_notes
        state.feedback_target_agent = event.feedback_target_agent
        state.status = 'needs_revision'
        state.error = None
        state.quality_checks_only = False
        state.graphic_file = ''; state.graphic_sha256 = ''; state.graphic_revision = -1
        state.professional_review = None
        state.publication = {}
        # Durable routing starts at the requested step, retaining upstream work,
        # repair limits and the same idempotent review event across interruptions.
        self.graph.update_state(self._config(str(state.run_id)),
                                {'run': state.model_dump(mode='json')}, as_node='repair')
        self._save(state)
        return self._result(self.graph.invoke(None, self._config(str(state.run_id)), durability='sync'))

    def get(self,run_id: str) -> RunState:
        from uuid import UUID
        run_id=str(UUID(run_id))
        # Graph checkpoint is authoritative if a process died between graph/DB writes.
        checkpoint=self.graph.get_state(self._config(run_id))
        if checkpoint.values and 'run' in checkpoint.values:
            state=RunState.model_validate(checkpoint.values['run'])
        else:
            state=self.storage.get_run_state(run_id)
        if state is None: raise KeyError('Run not found')
        saved=self.storage.get_run_state(run_id)
        if saved:
            state.visibility_observations=saved.visibility_observations
            from src.services.visibility_audit import restore_saved_signals
            restore_saved_signals(state,saved)
        if state.research_scope and state.analysis:
            from src.services.visibility import annotate
            annotate(state)
        store = getattr(self.storage, 'account_store', None)
        if store:
            from src.security.run_access import RunAccess
            state.owner_user_id = RunAccess(store).owner(state)
        state.tokens_used=self.storage.tokens_used(run_id)
        return state

    def submit_review(self,run_id: str,request: ReviewRequest,reviewer_name: str,reviewer_id: str | None = None) -> RunState:
        if request.action == 'needs_revision':
            request = ReviewRequest.model_validate(request.model_dump())
            return self.review_queued(run_id, ReviewEvent(**request.model_dump(), reviewer_name=reviewer_name,
                                                         reviewer_id=reviewer_id, event_id=str(uuid4())))
        with self.storage.run_lock(run_id):
            state=self.get(run_id)
            checkpoint=self.graph.get_state(self._config(run_id))
            if state.status!='seo_check_passed' or 'human_review' not in checkpoint.next:
                raise ValueError('Run is not awaiting human review')
            if request.expected_revision!=state.revision: raise ValueError('Stale review revision; refresh the draft')
            if request.action=='needs_revision' and state.human_revisions>=self.settings.max_human_revisions:
                raise ValueError('Human revision limit reached; reject or create a new run')
            # Validate again at the trust boundary even for direct Python callers.
            request=ReviewRequest.model_validate(request.model_dump())
            event=ReviewEvent(**request.model_dump(),reviewer_name=reviewer_name,reviewer_id=reviewer_id,event_id=str(uuid4()))
            self._require_review(state, event)
            result=self.graph.invoke(Command(resume=event.model_dump(mode='json')),self._config(run_id))
            return self._result(result)
