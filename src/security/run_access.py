"""Account and run authorization shared by HTTP handlers and durable jobs."""
from datetime import datetime, timezone
from uuid import uuid4


class AccessRevoked(PermissionError):
    """An actor can no longer continue work on a run."""


class RunAccess:
    def __init__(self, store):
        self.store = store

    def owner(self, state):
        row = self.store.execute(
            f'SELECT user_id FROM {self.store.prefix}run_owners WHERE run_id=?',
            (str(state.run_id),), one=True)
        return row[0] if row else state.owner_user_id

    @staticmethod
    def active(user):
        return bool(user and user['enabled'] and user['registration_status'] == 'active')

    @staticmethod
    def reviewer(user):
        return user['role'] == 'tax_adviser' or (
            user['role'] in {'reviewer', 'admin'} and user['name'] in {'Alex', 'Sam'})

    def shared(self, state, user):
        return bool(self.owner(state) and self.reviewer(user) and self.store.execute(
            f'SELECT user_id FROM {self.store.prefix}run_shares WHERE run_id=? AND user_id=?',
            (str(state.run_id), user['id']), one=True))

    def can_read(self, state, user):
        return self.active(user) and (user['role'] == 'admin' or
            self.owner(state) == user['id'] or self.shared(state, user))

    def require(self, state, user):
        if not self.can_read(state, user):
            raise AccessRevoked('This account no longer has access to this run')

    def manage(self, state, user):
        self.require(state, user)
        if user['role'] != 'admin' and self.owner(state) != user['id']:
            raise AccessRevoked('Only the owner or an administrator can manage sharing')

    def drafting(self, state, user):
        self.require(state, user)
        if (user['role'] != 'admin' and self.owner(state) != user['id'] and
                not (user['role'] == 'reviewer' and user['name'] in {'Alex', 'Sam'})):
            raise AccessRevoked('Topic selection, quality checks and infographic upload require the owner or a final reviewer')

    def audit(self, db, actor_id, action, target):
        p = self.store.param
        db.execute(f'INSERT INTO {self.store.prefix}access_audit VALUES ({p},{p},{p},{p},{p})',
            (str(uuid4()), actor_id, action, target, datetime.now(timezone.utc).isoformat()))

    def assign(self, state, target, actor):
        if not self.active(actor) or actor['role'] != 'admin':
            raise AccessRevoked('Administrator required')
        if not self.active(target):
            raise ValueError('Choose an activated account')
        p = self.store.param
        with self.store.storage.connect() as db:
            db.execute(f'INSERT INTO {self.store.prefix}run_owners VALUES ({p},{p}) ON CONFLICT(run_id) DO UPDATE SET user_id=excluded.user_id',
                (str(state.run_id), target['id']))
            # An ownership transfer never silently transfers sharing permissions.
            db.execute(f'DELETE FROM {self.store.prefix}run_shares WHERE run_id={p}', (str(state.run_id),))
            self.audit(db, actor['id'], 'assign_owner', str(state.run_id)+':'+target['id'])

    def grant(self, state, target, actor, enabled):
        self.manage(state, actor)
        if enabled and (not self.active(target) or not self.reviewer(target)):
            raise ValueError('Choose an activated professional or final reviewer')
        if not self.owner(state):
            raise ValueError('Assign an owner before sharing this historical run')
        p = self.store.param
        with self.store.storage.connect() as db:
            if enabled:
                db.execute(f'INSERT INTO {self.store.prefix}run_shares VALUES ({p},{p},{p}) ON CONFLICT(run_id,user_id) DO NOTHING',
                    (str(state.run_id), target['id'], actor['id']))
            else:
                db.execute(f'DELETE FROM {self.store.prefix}run_shares WHERE run_id={p} AND user_id={p}',
                    (str(state.run_id), target['id']))
            self.audit(db, actor['id'], 'grant_share' if enabled else 'revoke_share', str(state.run_id)+':'+target['id'])

    def shares(self, state):
        return [r[0] for r in self.store.execute(f'SELECT user_id FROM {self.store.prefix}run_shares WHERE run_id=?',
            (str(state.run_id),), all_rows=True)]

    def visible_state(self, state, actor):
        """Source provenance may reference another run; its ledger remains private."""
        state = state.model_copy(deep=True)

        def allowed(run_id):
            parent = self.store.storage.get_run_state(str(run_id))
            return bool(parent and self.can_read(parent, actor))

        def visit(value):
            if isinstance(value, dict):
                inherited = value.get('inherited_research_costs')
                if inherited and not allowed(inherited.get('run_id')):
                    value['inherited_research_costs'] = None
                if value.get('source_run_id') and not allowed(value['source_run_id']):
                    value.pop('original_cost', None)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(state.research_snapshot)
        return state


def authorize_job(job, storage):
    """Sessions may end during work; activation and sharing remain authoritative."""
    store = getattr(storage, 'account_store', None)
    if not job.reviewer_id and not store:
        return  # Trusted account-free local Python/CLI invocation.
    state = storage.get_run_state(str(job.run_id))
    if not state:
        raise AccessRevoked('Run is unavailable')
    if not job.reviewer_id:
        raise AccessRevoked('An authenticated actor is required')
    if not store:
        raise AccessRevoked('Account authorization is unavailable')
    actor = store.user('id', job.reviewer_id)
    RunAccess(store).require(state, actor)
    if job.kind in {'selection', 'quality_checks', 'ai_visibility', 'evaluation', 'evaluation_images'}:
        RunAccess(store).drafting(state, actor)
    if job.kind in {'research_refresh', 'research_repair'} and state.parent_run_id:
        parent = storage.get_run_state(str(state.parent_run_id))
        if not parent:
            raise AccessRevoked('Parent research is unavailable')
        RunAccess(store).require(parent, actor)
    event = job.payload.get('event')
    if event and (event.get('reviewer_id') != actor['id'] or event.get('reviewer_name') != actor['name']):
        raise AccessRevoked('The queued review identity has changed')
