import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from filelock import FileLock
from src.orchestrator.state import RunState
from src.schemas.review import ReviewEvent
from src.schemas.assets import SocialPost, ImageAsset, ImageCreative
from src.schemas.ai import AICall
from src.schemas.jobs import Job
from src.storage.base import BaseStorage

class BudgetExceeded(RuntimeError):
    pass

class ResearchBudgetExceeded(BudgetExceeded):
    def __init__(self, message, *, required=None, available=None, scope=None):
        super().__init__(message)
        self.required, self.available, self.scope = required, available, scope

class SQLiteStorage(BaseStorage):
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.locks = path.parent / "locks"
        self.locks.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS failure_events (id TEXT PRIMARY KEY, run_id TEXT, job_id TEXT, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS failure_events_job ON failure_events(job_id);
            CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reviews (event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, amount INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS tokens_run ON tokens(run_id);
            CREATE TABLE IF NOT EXISTS editorial_assets (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS editorial_assets_run ON editorial_assets(run_id, kind);
            CREATE TABLE IF NOT EXISTS image_attempts (id TEXT PRIMARY KEY, run_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_calls (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ai_calls_run ON ai_calls(run_id);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_run ON jobs(run_id);
            CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT);
            CREATE TABLE IF NOT EXISTS reviewer_sessions (digest TEXT PRIMARY KEY, reviewer TEXT NOT NULL, expires_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_costs (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, amount INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0);
            """)
            columns = {row[1] for row in db.execute('PRAGMA table_info(research_costs)')}
            if 'period' not in columns:
                db.execute("ALTER TABLE research_costs ADD COLUMN period TEXT NOT NULL DEFAULT ''")
        self.monthly_ceiling = None

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def save_run_state(self, state: RunState) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT state FROM runs WHERE run_id=?',(str(state.run_id),)).fetchone()
            if previous and json.loads(previous[0]).get('run_budget_usd')!=state.run_budget_usd:
                raise ValueError('Run spending allowance is immutable after creation')
            if previous and json.loads(previous[0]).get('internal_link_policy','strict')!=state.internal_link_policy:
                raise ValueError('Internal link policy is immutable after creation')
            if previous:
                before = RunState.model_validate_json(previous[0])
                if any(getattr(before, key) != getattr(state, key) for key in ('campaign_id','campaign_budget_usd','research_scope')):
                    raise ValueError('Campaign and research scope are immutable after creation')
            db.execute("INSERT INTO runs VALUES (?, ?) ON CONFLICT(run_id) DO UPDATE SET state=excluded.state", (str(state.run_id), state.model_dump_json()))

    def reserve_research_cost(self, run_id, amount, ceiling):
        if type(amount) is not int or amount <= 0 or not 0 < ceiling <= 5000000:
            raise ValueError('Invalid research cost reservation')
        reservation = str(uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM runs WHERE run_id=?', (run_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if not row and ceiling>1000000:raise ValueError('An explicit persisted run allowance is required above $1')
            limit = state.get('run_budget_usd')
            # Historical runs without an explicit allowance retain the old $1 ceiling.
            if row and limit is None: limit = 1
            if limit is not None:
                from src.services.research_budget import micros
                ceiling = min(ceiling, micros(limit))
            period = datetime.now(timezone.utc).strftime('%Y-%m')
            month_used = db.execute("SELECT COALESCE(SUM(amount),0) FROM research_costs WHERE period IN (?, '')", (period,)).fetchone()[0]
            if self.monthly_ceiling is not None and month_used + amount > self.monthly_ceiling:
                raise ResearchBudgetExceeded('Monthly USD allowance reached; request not sent', required=amount, available=max(0,self.monthly_ceiling-month_used), scope='monthly')
            used = db.execute('SELECT COALESCE(SUM(amount),0) FROM research_costs WHERE run_id=?', (run_id,)).fetchone()[0]
            if used + amount > ceiling: raise ResearchBudgetExceeded('Research USD allowance reached; request not sent', required=amount, available=max(0,ceiling-used), scope='run')
            if state.get('campaign_id'):
                campaign_used = db.execute("SELECT COALESCE(SUM(c.amount),0) FROM research_costs c JOIN runs r ON r.run_id=c.run_id WHERE json_extract(r.state,'$.campaign_id')=?", (state['campaign_id'],)).fetchone()[0]
                campaign_limit = int(state['campaign_budget_usd'] * 1000000)
                if campaign_used + amount > campaign_limit:
                    raise ResearchBudgetExceeded('Campaign USD allowance reached; request not sent', required=amount, available=max(0,campaign_limit-campaign_used), scope='campaign')
            db.execute('INSERT INTO research_costs(id,run_id,amount,settled,period) VALUES (?,?,?,0,?)', (reservation,run_id,amount,period))
        return reservation

    def campaign_costs(self, run_id):
        state = self.get_run_state(run_id)
        if not state or not state.campaign_id: return None
        with self.connect() as db:
            rows=db.execute("SELECT c.amount,c.settled FROM research_costs c JOIN runs r ON r.run_id=c.run_id WHERE json_extract(r.state,'$.campaign_id')=?",(str(state.campaign_id),)).fetchall()
        return {'campaign_id':str(state.campaign_id),'limit_microusd':int(state.campaign_budget_usd*1000000),
                'recorded_microusd':sum(a for a,s in rows if s),'reserved_microusd':sum(a for a,s in rows if not s),
                'checkpoint_reached':sum(a for a,s in rows)>=1000000}

    def monthly_costs(self):
        period = datetime.now(timezone.utc).strftime('%Y-%m')
        with self.connect() as db:
            rows = db.execute("SELECT amount,settled FROM research_costs WHERE period IN (?, '')", (period,)).fetchall()
        return {'period': period, 'limit_microusd': self.monthly_ceiling,
                'recorded_microusd': sum(a for a,s in rows if s),
                'reserved_microusd': sum(a for a,s in rows if not s)}

    def settle_research_cost(self, reservation, actual):
        if type(actual) is not int or actual < 0: raise ValueError('Invalid research charge')
        with self.connect() as db:
            db.execute('UPDATE research_costs SET amount=?,settled=1 WHERE id=? AND settled=0', (actual,reservation))

    def research_costs(self, run_id):
        with self.connect() as db:
            rows = db.execute('SELECT amount,settled FROM research_costs WHERE run_id=?', (run_id,)).fetchall()
        return {'recorded_microusd':sum(a for a,s in rows if s), 'reserved_microusd':sum(a for a,s in rows if not s)}

    def list_operations(self, run_id):
        with self.connect() as db:
            rows=db.execute('SELECT key,status,data FROM operations WHERE run_id=?',(run_id,)).fetchall()
        return [{'key':k,'status':s,'value':json.loads(v) if v else None} for k,s,v in rows]

    def get_run_state(self, run_id: str) -> RunState | None:
        with self.connect() as db:
            row = db.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return RunState.model_validate_json(row[0]) if row else None

    def list_runs(self) -> list[RunState]:
        with self.connect() as db:
            rows = db.execute("SELECT state FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
        return [RunState.model_validate_json(row[0]) for row in rows]

    def log_human_feedback(self, run_id: str, event: ReviewEvent) -> None:
        # Replay after a graph interruption cannot duplicate the same event.
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO reviews VALUES (?, ?, ?)", (event.event_id, run_id, event.model_dump_json()))

    def reserve_tokens(self, run_id: str, amount: int, ceiling: int) -> str:
        if amount <= 0:
            raise ValueError("Token reservation must be positive")
        reservation = str(uuid4())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT COALESCE(SUM(amount), 0) FROM tokens WHERE run_id=?", (run_id,)).fetchone()[0]
            if used + amount > ceiling:
                raise BudgetExceeded("Run token ceiling reached; request was not sent")
            db.execute("INSERT INTO tokens(id, run_id, amount) VALUES (?, ?, ?)", (reservation, run_id, amount))
        return reservation

    def settle_tokens(self, reservation: str, actual: int) -> None:
        if actual < 0:
            raise ValueError("Token usage cannot be negative")
        with self.connect() as db:
            db.execute("UPDATE tokens SET amount=?, settled=1 WHERE id=? AND settled=0", (actual, reservation))

    def tokens_used(self, run_id: str) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COALESCE(SUM(amount), 0) FROM tokens WHERE run_id=?", (run_id,)).fetchone()[0])

    def run_lock(self, run_id: str):
        # UUID validation prevents lock-file path injection.
        from uuid import UUID
        return FileLock(self.locks / (str(UUID(run_id)) + ".lock"), timeout=2)

    def save_asset(self, asset: SocialPost | ImageAsset | ImageCreative) -> None:
        kind = 'post' if isinstance(asset, SocialPost) else 'master' if isinstance(asset, ImageCreative) else 'image'
        with self.connect() as db:
            db.execute('INSERT INTO editorial_assets VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (str(asset.id), str(asset.run_id), kind, asset.model_dump_json()))

    def get_asset(self, asset_id: str, kind: str) -> SocialPost | ImageAsset | None:
        with self.connect() as db:
            row = db.execute('SELECT data FROM editorial_assets WHERE id=? AND kind=?', (asset_id, kind)).fetchone()
        return {'post': SocialPost, 'image': ImageAsset, 'master': ImageCreative}[kind].model_validate_json(row[0]) if row else None

    def list_assets(self, run_id: str, kind: str) -> list[SocialPost] | list[ImageAsset]:
        with self.connect() as db:
            rows = db.execute('SELECT data FROM editorial_assets WHERE run_id=? AND kind=? ORDER BY rowid', (run_id, kind)).fetchall()
        model = {'post': SocialPost, 'image': ImageAsset, 'master': ImageCreative}[kind]
        return [model.model_validate_json(row[0]) for row in rows]

    def reserve_image_attempt(self, run_id: str, limit: int) -> None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT COUNT(*) FROM image_attempts WHERE run_id=?', (run_id,)).fetchone()[0]
            if count >= limit:
                raise BudgetExceeded('Image attempt limit reached')
            db.execute('INSERT INTO image_attempts VALUES (?, ?)', (str(uuid4()), run_id))

    def log_ai_call(self, call: AICall) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO ai_calls VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (str(call.id), str(call.run_id), call.model_dump_json()))

    def list_ai_calls(self, run_id: str) -> list[AICall]:
        with self.connect() as db:
            rows = db.execute('SELECT data FROM ai_calls WHERE run_id=? ORDER BY rowid', (run_id,)).fetchall()
        return [AICall.model_validate_json(row[0]) for row in rows]

    def create_job(self, job: Job, initial_state: RunState | None = None) -> Job:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM jobs WHERE idempotency_key=?', (str(job.idempotency_key),)).fetchone()
            if row:
                return Job.model_validate_json(row[0])
            if job.kind=='ai_visibility' and db.execute("SELECT 1 FROM jobs WHERE run_id=? AND status IN ('queued','running','paused')",(str(job.run_id),)).fetchone():
                raise ValueError('Inspect the active or uncertain task before starting another collection')
            if initial_state:
                if job.kind in {'research_refresh','research_repair'}:
                    active=db.execute("SELECT data FROM jobs WHERE status IN ('queued','running','paused')").fetchall()
                    if any(json.loads(r[0]).get('payload',{}).get('parent_run_id')==job.payload.get('parent_run_id') for r in active):
                        raise ValueError('Linked research is already active')
                db.execute('INSERT INTO runs VALUES (?, ?)', (str(initial_state.run_id), initial_state.model_dump_json()))
            db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?)',
                       (str(job.id), str(job.run_id), str(job.idempotency_key), job.status, job.model_dump_json()))
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self.connect() as db:
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def get_job_by_idempotency(self, key: str) -> Job | None:
        with self.connect() as db:
            row = db.execute('SELECT data FROM jobs WHERE idempotency_key=?', (key,)).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def list_jobs(self, run_id: str | None = None) -> list[Job]:
        with self.connect() as db:
            rows = db.execute('SELECT data FROM jobs ' + ('WHERE run_id=? ' if run_id else '') +
                              'ORDER BY rowid DESC LIMIT 100', (run_id,) if run_id else ()).fetchall()
        return [Job.model_validate_json(row[0]) for row in rows]

    def claim_job(self) -> Job | None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT data FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            job = Job.model_validate_json(row[0])
            job.status = 'running'
            job.updated_at = datetime.now(timezone.utc)
            db.execute('UPDATE jobs SET status=?, data=? WHERE id=?', (job.status, job.model_dump_json(), str(job.id)))
        return job

    def save_job(self, job: Job) -> None:
        job.updated_at = datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?, data=? WHERE id=?', (job.status, job.model_dump_json(), str(job.id)))

    def recover_jobs(self) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                job = Job.model_validate_json(row[0])
                job.status = 'queued'
                db.execute('UPDATE jobs SET status=?, data=? WHERE id=?', ('queued', job.model_dump_json(), str(job.id)))

    def retry_job(self, job_id: str) -> Job:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Job not found')
            job = Job.model_validate_json(row[0])
            if job.kind=='evaluation':
                raise ValueError('Evaluation requires a fresh verified plan, not a generic paid retry')
            if job.status not in {'paused', 'failed'}:
                raise ValueError('Job is not retryable')
            # Explicit reviewer retry permits a new call; completed caches remain intact.
            db.execute("UPDATE operations SET status='retry_authorized' WHERE key LIKE ? AND status IN ('pending','failed')",
                       (job_id + ':%',))
            job.status, job.error, job.error_code = 'queued', None, None
            job.requires_paid_retry = False
            job.attempt_id = str(uuid4())
            job.execution_context = {}
            job.failure_summary = None
            job.updated_at = datetime.now(timezone.utc)
            db.execute('UPDATE jobs SET status=?, data=? WHERE id=?', ('queued', job.model_dump_json(), job_id))
        return job

    def get_operation(self, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT status, data FROM operations WHERE key=?', (key,)).fetchone()
        return {'status': row[0], 'value': json.loads(row[1]) if row[1] else None} if row else None

    def begin_operation(self, key: str, run_id: str) -> dict:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status, data FROM operations WHERE key=?', (key,)).fetchone()
            if row:
                if row[0] in {'failed', 'retry_authorized'}:
                    db.execute('UPDATE operations SET status=?, data=NULL WHERE key=?', ('pending', key))
                    return {'status': 'new', 'value': None}
                return {'status': row[0], 'value': json.loads(row[1]) if row[1] else None}
            db.execute('INSERT INTO operations VALUES (?, ?, ?, NULL)', (key, run_id, 'pending'))
        return {'status': 'new', 'value': None}

    def finish_operation(self, key: str, value: dict) -> None:
        with self.connect() as db:
            db.execute('UPDATE operations SET status=?, data=? WHERE key=?', ('completed', json.dumps(value), key))

    def save_operation_response(self, key: str, value: dict) -> None:
        with self.connect() as db:
            row = db.execute('SELECT run_id FROM operations WHERE key=?', (key,)).fetchone()
            if not row:
                raise ValueError('Operation not found')
            response_id = value.get('call', {}).get('id') or str(uuid4())
            # Keep each encrypted reply after explicit retry overwrites the active operation.
            db.execute('INSERT INTO operations VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data',
                       (key + ':response:' + response_id, row[0], 'archived_response', json.dumps(value)))
            db.execute('UPDATE operations SET status=?, data=? WHERE key=?', ('received', json.dumps(value), key))

    def fail_operation(self, key: str) -> None:
        with self.connect() as db:
            db.execute('UPDATE operations SET status=? WHERE key=?', ('failed', key))

    def reset_pending_operations(self, job_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE operations SET status='failed' WHERE key LIKE ? AND status='pending'", (job_id + ':%',))

    def create_session(self, digest: str, reviewer: str, expires_at: str) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO reviewer_sessions VALUES (?, ?, ?)', (digest, reviewer, expires_at))

    def get_session(self, digest: str) -> tuple[str, str] | None:
        with self.connect() as db:
            row = db.execute('SELECT reviewer, expires_at FROM reviewer_sessions WHERE digest=?', (digest,)).fetchone()
        return (row[0], row[1]) if row else None

    def delete_session(self, digest: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM reviewer_sessions WHERE digest=?', (digest,))

    def save_failure(self, event: dict) -> None:
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO failure_events VALUES (?, ?, ?, ?)',
                (event['id'], event['run_id'], event['job_id'], json.dumps(event)))

    def list_failures(self, job_id: str) -> list[dict]:
        with self.connect() as db:
            rows=db.execute('SELECT data FROM failure_events WHERE job_id=? ORDER BY rowid', (job_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_run_failures(self, run_id: str) -> list[dict]:
        with self.connect() as db:
            rows=db.execute('SELECT data FROM failure_events WHERE run_id=? ORDER BY rowid DESC LIMIT 100', (run_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]
