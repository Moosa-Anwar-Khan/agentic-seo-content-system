from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4
from src.storage.base import BaseStorage
from src.storage.sqlite_storage import BudgetExceeded,ResearchBudgetExceeded
from src.orchestrator.state import RunState
from src.schemas.review import ReviewEvent
from src.schemas.assets import SocialPost, ImageAsset, ImageCreative
from src.schemas.ai import AICall
from src.schemas.jobs import Job

def secure_dsn(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    if conninfo_to_dict(dsn).get('sslmode') not in {'require','verify-full'}:
        raise ValueError('Postgres DSN must explicitly require TLS')
    # Keep application and checkpoint tables outside Supabase's default public API schema.
    return make_conninfo(dsn, options='-c search_path=agentic_seo')

class RunBusy(RuntimeError):
    pass

class PostgresStorage(BaseStorage):
    """Optional Postgres/Supabase adapter using a trusted server-side TLS DSN."""
    def __init__(self, dsn: str):
        if not dsn: raise ValueError('DATABASE_URL required for Postgres')
        if 'sslmode=require' not in dsn and 'sslmode=verify-full' not in dsn:
            raise ValueError('Postgres DSN must explicitly require TLS')
        import psycopg
        from psycopg.types.json import Jsonb
        self.driver,self.Jsonb,self.dsn=psycopg,Jsonb,secure_dsn(dsn)
        # Schema initialization is explicit; this adapter does not create tables at import.
        with self.connect() as db:
            db.execute('CREATE SCHEMA IF NOT EXISTS agentic_seo')
            db.execute('REVOKE ALL ON SCHEMA agentic_seo FROM PUBLIC')
            db.execute('CREATE TABLE IF NOT EXISTS seo_failure_events (id TEXT PRIMARY KEY, run_id TEXT, job_id TEXT, data JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            db.execute('CREATE INDEX IF NOT EXISTS seo_failure_events_job ON seo_failure_events(job_id)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_runs (run_id TEXT PRIMARY KEY,state JSONB NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            db.execute('CREATE TABLE IF NOT EXISTS seo_reviews (event_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,event JSONB NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_tokens (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,amount BIGINT NOT NULL,settled BOOLEAN NOT NULL DEFAULT FALSE)')
            db.execute('CREATE INDEX IF NOT EXISTS seo_tokens_run ON seo_tokens(run_id)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_assets (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,kind TEXT NOT NULL,data JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            db.execute('CREATE INDEX IF NOT EXISTS seo_assets_run ON seo_assets(run_id,kind)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_image_attempts (id TEXT PRIMARY KEY,run_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_ai_calls (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,data JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            db.execute('CREATE INDEX IF NOT EXISTS seo_ai_calls_run ON seo_ai_calls(run_id)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_jobs (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL,data JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            db.execute('CREATE INDEX IF NOT EXISTS seo_jobs_run ON seo_jobs(run_id)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_operations (key TEXT PRIMARY KEY,run_id TEXT NOT NULL,status TEXT NOT NULL,data JSONB)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_reviewer_sessions (digest TEXT PRIMARY KEY,reviewer TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS seo_research_costs (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,amount BIGINT NOT NULL,settled BOOLEAN NOT NULL DEFAULT FALSE)')
            db.execute("ALTER TABLE seo_research_costs ADD COLUMN IF NOT EXISTS period TEXT NOT NULL DEFAULT ''")
        self.monthly_ceiling = None

    def connect(self): return self.driver.connect(self.dsn,connect_timeout=10)

    def reserve_research_cost(self, run_id, amount, ceiling):
        if type(amount) is not int or amount <= 0 or not 0 < ceiling <= 5000000: raise ValueError('Invalid research cost reservation')
        reservation=str(uuid4())
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)',(self.key(run_id+':research-cost'),))
            db.execute('SELECT pg_advisory_xact_lock(%s)', (self.key('monthly-cost-ledger'),))
            row = db.execute('SELECT state FROM seo_runs WHERE run_id=%s', (run_id,)).fetchone()
            state = row[0] if row else {}
            if not row and ceiling>1000000:raise ValueError('An explicit persisted run allowance is required above $1')
            limit = state.get('run_budget_usd')
            if row and limit is None: limit = 1
            if limit is not None:
                from src.services.research_budget import micros
                ceiling = min(ceiling, micros(limit))
            period = datetime.now(timezone.utc).strftime('%Y-%m')
            month_used = db.execute("SELECT COALESCE(SUM(amount),0) FROM seo_research_costs WHERE period IN (%s, '')", (period,)).fetchone()[0]
            if self.monthly_ceiling is not None and month_used + amount > self.monthly_ceiling:
                raise ResearchBudgetExceeded('Monthly USD allowance reached; request not sent', required=amount, available=max(0,self.monthly_ceiling-month_used), scope='monthly')
            used=db.execute('SELECT COALESCE(SUM(amount),0) FROM seo_research_costs WHERE run_id=%s',(run_id,)).fetchone()[0]
            if used+amount>ceiling: raise ResearchBudgetExceeded('Research USD allowance reached; request not sent', required=amount, available=max(0,ceiling-used), scope='run')
            if state.get('campaign_id'):
                # The monthly advisory lock also serializes campaign reservations across runs.
                campaign_used=db.execute("SELECT COALESCE(SUM(c.amount),0) FROM seo_research_costs c JOIN seo_runs r ON r.run_id=c.run_id WHERE r.state->>'campaign_id'=%s",(state['campaign_id'],)).fetchone()[0]
                campaign_limit=int(state['campaign_budget_usd']*1000000)
                if campaign_used+amount>campaign_limit:
                    raise ResearchBudgetExceeded('Campaign USD allowance reached; request not sent',required=amount,available=max(0,campaign_limit-campaign_used),scope='campaign')
            db.execute('INSERT INTO seo_research_costs(id,run_id,amount,period) VALUES (%s,%s,%s,%s)',(reservation,run_id,amount,period))
        return reservation

    def campaign_costs(self,run_id):
        state=self.get_run_state(run_id)
        if not state or not state.campaign_id:return None
        with self.connect() as db:
            rows=db.execute("SELECT c.amount,c.settled FROM seo_research_costs c JOIN seo_runs r ON r.run_id=c.run_id WHERE r.state->>'campaign_id'=%s",(str(state.campaign_id),)).fetchall()
        return {'campaign_id':str(state.campaign_id),'limit_microusd':int(state.campaign_budget_usd*1000000),
            'recorded_microusd':sum(a for a,s in rows if s),'reserved_microusd':sum(a for a,s in rows if not s),
            'checkpoint_reached':sum(a for a,s in rows)>=1000000}

    def monthly_costs(self):
        period = datetime.now(timezone.utc).strftime('%Y-%m')
        with self.connect() as db:
            rows=db.execute("SELECT amount,settled FROM seo_research_costs WHERE period IN (%s, '')",(period,)).fetchall()
        return {'period': period, 'limit_microusd': self.monthly_ceiling,
                'recorded_microusd':sum(a for a,s in rows if s),
                'reserved_microusd':sum(a for a,s in rows if not s)}

    def settle_research_cost(self, reservation, actual):
        if type(actual) is not int or actual<0: raise ValueError('Invalid research charge')
        with self.connect() as db:
            db.execute('UPDATE seo_research_costs SET amount=%s,settled=TRUE WHERE id=%s AND settled=FALSE',(actual,reservation))

    def research_costs(self, run_id):
        with self.connect() as db:
            rows=db.execute('SELECT amount,settled FROM seo_research_costs WHERE run_id=%s',(run_id,)).fetchall()
        return {'recorded_microusd':sum(a for a,s in rows if s),'reserved_microusd':sum(a for a,s in rows if not s)}

    def list_operations(self, run_id):
        with self.connect() as db:
            rows=db.execute('SELECT key,status,data FROM seo_operations WHERE run_id=%s',(run_id,)).fetchall()
        return [{'key':k,'status':s,'value':v} for k,s,v in rows]

    @staticmethod
    def key(value: str) -> int:
        return int.from_bytes(sha256(value.encode()).digest()[:8],byteorder='big',signed=True)

    def save_run_state(self,state: RunState) -> None:
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)',(self.key(str(state.run_id)+':state'),))
            previous=db.execute('SELECT state FROM seo_runs WHERE run_id=%s',(str(state.run_id),)).fetchone()
            if previous and previous[0].get('run_budget_usd')!=state.run_budget_usd:
                raise ValueError('Run spending allowance is immutable after creation')
            if previous and previous[0].get('internal_link_policy','strict')!=state.internal_link_policy:
                raise ValueError('Internal link policy is immutable after creation')
            if previous:
                before=RunState.model_validate(previous[0])
                if any(getattr(before,key)!=getattr(state,key) for key in ('campaign_id','campaign_budget_usd','research_scope')):
                    raise ValueError('Campaign and research scope are immutable after creation')
            db.execute('INSERT INTO seo_runs(run_id,state) VALUES (%s,%s) ON CONFLICT(run_id) DO UPDATE SET state=excluded.state,updated_at=now()', (str(state.run_id),self.Jsonb(state.model_dump(mode='json'))))

    def get_run_state(self,run_id: str) -> RunState | None:
        with self.connect() as db:
            row=db.execute('SELECT state FROM seo_runs WHERE run_id=%s',(run_id,)).fetchone()
        return RunState.model_validate(row[0]) if row else None

    def list_runs(self) -> list[RunState]:
        with self.connect() as db:
            rows=db.execute('SELECT state FROM seo_runs ORDER BY updated_at DESC LIMIT 100').fetchall()
        return [RunState.model_validate(r[0]) for r in rows]

    def log_human_feedback(self,run_id: str,event: ReviewEvent) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO seo_reviews VALUES (%s,%s,%s) ON CONFLICT(event_id) DO NOTHING',(event.event_id,run_id,self.Jsonb(event.model_dump(mode='json'))))

    def reserve_tokens(self,run_id: str,amount: int,ceiling: int) -> str:
        if amount<=0: raise ValueError('Token reservation must be positive')
        reservation=str(uuid4())
        with self.connect() as db:
            # Transaction advisory lock makes SUM + INSERT atomic for each run.
            db.execute('SELECT pg_advisory_xact_lock(%s)',(self.key(run_id+':tokens'),))
            used=db.execute('SELECT COALESCE(SUM(amount),0) FROM seo_tokens WHERE run_id=%s',(run_id,)).fetchone()[0]
            if used+amount>ceiling: raise BudgetExceeded('Run token ceiling reached; request was not sent')
            db.execute('INSERT INTO seo_tokens(id,run_id,amount) VALUES (%s,%s,%s)',(reservation,run_id,amount))
        return reservation

    def settle_tokens(self,reservation: str,actual: int) -> None:
        if actual<0: raise ValueError('Token usage cannot be negative')
        with self.connect() as db:
            db.execute('UPDATE seo_tokens SET amount=%s,settled=TRUE WHERE id=%s AND settled=FALSE',(actual,reservation))

    def tokens_used(self,run_id: str) -> int:
        with self.connect() as db:
            return int(db.execute('SELECT COALESCE(SUM(amount),0) FROM seo_tokens WHERE run_id=%s',(run_id,)).fetchone()[0])

    @contextmanager
    def run_lock(self,run_id: str):
        run_id=str(UUID(run_id))
        # Requires a direct connection or session pooler, not transaction pooling.
        with self.driver.connect(self.dsn,autocommit=True,connect_timeout=10) as db:
            key=self.key(run_id+':workflow')
            if not db.execute('SELECT pg_try_advisory_lock(%s)',(key,)).fetchone()[0]:
                raise RunBusy('Run already executing')
            try: yield
            finally: db.execute('SELECT pg_advisory_unlock(%s)',(key,))

    def save_asset(self, asset: SocialPost | ImageAsset | ImageCreative) -> None:
        kind = 'post' if isinstance(asset, SocialPost) else 'master' if isinstance(asset, ImageCreative) else 'image'
        with self.connect() as db:
            db.execute('INSERT INTO seo_assets(id,run_id,kind,data) VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (str(asset.id), str(asset.run_id), kind, self.Jsonb(asset.model_dump(mode='json'))))

    def get_asset(self, asset_id: str, kind: str) -> SocialPost | ImageAsset | None:
        with self.connect() as db:
            row = db.execute('SELECT data FROM seo_assets WHERE id=%s AND kind=%s', (asset_id, kind)).fetchone()
        return {'post': SocialPost, 'image': ImageAsset, 'master': ImageCreative}[kind].model_validate(row[0]) if row else None

    def list_assets(self, run_id: str, kind: str) -> list[SocialPost] | list[ImageAsset]:
        with self.connect() as db:
            rows = db.execute('SELECT data FROM seo_assets WHERE run_id=%s AND kind=%s ORDER BY created_at,id', (run_id, kind)).fetchall()
        model = {'post': SocialPost, 'image': ImageAsset, 'master': ImageCreative}[kind]
        return [model.model_validate(row[0]) for row in rows]

    def reserve_image_attempt(self, run_id: str, limit: int) -> None:
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (self.key(run_id+':images'),))
            count = db.execute('SELECT COUNT(*) FROM seo_image_attempts WHERE run_id=%s', (run_id,)).fetchone()[0]
            if count >= limit:
                raise BudgetExceeded('Image attempt limit reached')
            db.execute('INSERT INTO seo_image_attempts VALUES (%s,%s)', (str(uuid4()),run_id))

    def log_ai_call(self, call: AICall) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO seo_ai_calls(id,run_id,data) VALUES (%s,%s,%s) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (str(call.id), str(call.run_id), self.Jsonb(call.model_dump(mode='json'))))

    def list_ai_calls(self, run_id: str) -> list[AICall]:
        with self.connect() as db:
            rows = db.execute('SELECT data FROM seo_ai_calls WHERE run_id=%s ORDER BY created_at,id', (run_id,)).fetchall()
        return [AICall.model_validate(row[0]) for row in rows]

    def create_job(self, job: Job, initial_state: RunState | None = None) -> Job:
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s)', (self.key(str(job.idempotency_key)),))
            row = db.execute('SELECT data FROM seo_jobs WHERE idempotency_key=%s', (str(job.idempotency_key),)).fetchone()
            if row:
                return Job.model_validate(row[0])
            if job.kind=='ai_visibility':
                db.execute('SELECT pg_advisory_xact_lock(%s)',(self.key(str(job.run_id)+':visibility-create'),))
                if db.execute("SELECT 1 FROM seo_jobs WHERE run_id=%s AND status IN ('queued','running','paused')",(str(job.run_id),)).fetchone():
                    raise ValueError('Inspect the active or uncertain task before another collection')
            if initial_state:
                if job.kind in {'research_refresh','research_repair'}:
                    parent=str(initial_state.parent_run_id)
                    db.execute('SELECT pg_advisory_xact_lock(%s)',(self.key(parent+':research-create'),))
                    active=db.execute("SELECT data FROM seo_jobs WHERE status IN ('queued','running','paused')").fetchall()
                    if any(r[0].get('payload',{}).get('parent_run_id')==parent for r in active):raise ValueError('Linked research is already active')
                db.execute('INSERT INTO seo_runs(run_id,state) VALUES (%s,%s)',
                           (str(initial_state.run_id), self.Jsonb(initial_state.model_dump(mode='json'))))
            db.execute('INSERT INTO seo_jobs(id,run_id,idempotency_key,status,data) VALUES (%s,%s,%s,%s,%s)',
                       (str(job.id), str(job.run_id), str(job.idempotency_key), job.status,
                        self.Jsonb(job.model_dump(mode='json'))))
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self.connect() as db:
            row = db.execute('SELECT data FROM seo_jobs WHERE id=%s', (job_id,)).fetchone()
        return Job.model_validate(row[0]) if row else None

    def get_job_by_idempotency(self, key: str) -> Job | None:
        with self.connect() as db:
            row = db.execute('SELECT data FROM seo_jobs WHERE idempotency_key=%s', (key,)).fetchone()
        return Job.model_validate(row[0]) if row else None

    def list_jobs(self, run_id: str | None = None) -> list[Job]:
        with self.connect() as db:
            rows = db.execute('SELECT data FROM seo_jobs ' + ('WHERE run_id=%s ' if run_id else '') +
                              'ORDER BY created_at DESC,id DESC LIMIT 100', (run_id,) if run_id else ()).fetchall()
        return [Job.model_validate(row[0]) for row in rows]

    def claim_job(self) -> Job | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM seo_jobs WHERE status='queued' ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not row:
                return None
            job = Job.model_validate(row[0])
            job.status = 'running'
            job.updated_at = datetime.now(timezone.utc)
            db.execute('UPDATE seo_jobs SET status=%s,data=%s WHERE id=%s',
                       (job.status, self.Jsonb(job.model_dump(mode='json')), str(job.id)))
        return job

    def save_job(self, job: Job) -> None:
        job.updated_at = datetime.now(timezone.utc)
        with self.connect() as db:
            db.execute('UPDATE seo_jobs SET status=%s,data=%s WHERE id=%s',
                       (job.status, self.Jsonb(job.model_dump(mode='json')), str(job.id)))

    def recover_jobs(self) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM seo_jobs WHERE status='running' FOR UPDATE").fetchall()
            for row in rows:
                job = Job.model_validate(row[0])
                job.status = 'queued'
                db.execute('UPDATE seo_jobs SET status=%s,data=%s WHERE id=%s',
                           ('queued', self.Jsonb(job.model_dump(mode='json')), str(job.id)))

    def get_operation(self, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT status,data FROM seo_operations WHERE key=%s', (key,)).fetchone()
        return {'status': row[0], 'value': row[1]} if row else None

    def begin_operation(self, key: str, run_id: str) -> dict:
        with self.connect() as db:
            row = db.execute('INSERT INTO seo_operations VALUES (%s,%s,%s,NULL) ON CONFLICT(key) DO NOTHING RETURNING key',
                             (key, run_id, 'pending')).fetchone()
            if row:
                return {'status': 'new', 'value': None}
            existing = db.execute('SELECT status,data FROM seo_operations WHERE key=%s', (key,)).fetchone()
            if existing[0] in {'failed', 'retry_authorized'}:
                db.execute('UPDATE seo_operations SET status=%s,data=NULL WHERE key=%s', ('pending', key))
                return {'status': 'new', 'value': None}
        return {'status': existing[0], 'value': existing[1]}

    def finish_operation(self, key: str, value: dict) -> None:
        with self.connect() as db:
            db.execute('UPDATE seo_operations SET status=%s,data=%s WHERE key=%s',
                       ('completed', self.Jsonb(value), key))

    def save_operation_response(self, key: str, value: dict) -> None:
        with self.connect() as db:
            row = db.execute('SELECT run_id FROM seo_operations WHERE key=%s', (key,)).fetchone()
            if not row:
                raise ValueError('Operation not found')
            response_id = value.get('call', {}).get('id') or str(uuid4())
            db.execute('INSERT INTO seo_operations(key,run_id,status,data) VALUES (%s,%s,%s,%s) ON CONFLICT(key) DO UPDATE SET data=excluded.data',
                       (key + ':response:' + response_id, row[0], 'archived_response', self.Jsonb(value)))
            db.execute('UPDATE seo_operations SET status=%s,data=%s WHERE key=%s',
                       ('received', self.Jsonb(value), key))

    def retry_job(self, job_id: str) -> Job:
        with self.connect() as db:
            row = db.execute('SELECT data FROM seo_jobs WHERE id=%s FOR UPDATE', (job_id,)).fetchone()
            if not row:
                raise ValueError('Job not found')
            job = Job.model_validate(row[0])
            if job.kind=='evaluation':
                raise ValueError('Evaluation requires a fresh verified plan, not a generic paid retry')
            if job.status not in {'paused', 'failed'}:
                raise ValueError('Job is not retryable')
            db.execute("UPDATE seo_operations SET status='retry_authorized' WHERE key LIKE %s AND status IN ('pending','failed')",
                       (job_id + ':%',))
            job.status, job.error, job.error_code = 'queued', None, None
            job.requires_paid_retry = False
            job.attempt_id = str(uuid4())
            job.execution_context = {}
            job.failure_summary = None
            job.updated_at = datetime.now(timezone.utc)
            db.execute('UPDATE seo_jobs SET status=%s,data=%s WHERE id=%s', ('queued', self.Jsonb(job.model_dump(mode='json')), job_id))
        return job

    def fail_operation(self, key: str) -> None:
        with self.connect() as db:
            db.execute('UPDATE seo_operations SET status=%s WHERE key=%s', ('failed', key))

    def reset_pending_operations(self, job_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE seo_operations SET status='failed' WHERE key LIKE %s AND status='pending'", (job_id + ':%',))

    def create_session(self, digest: str, reviewer: str, expires_at: str) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO seo_reviewer_sessions VALUES (%s,%s,%s)', (digest, reviewer, expires_at))

    def get_session(self, digest: str) -> tuple[str, str] | None:
        with self.connect() as db:
            row = db.execute('SELECT reviewer,expires_at FROM seo_reviewer_sessions WHERE digest=%s', (digest,)).fetchone()
        return (row[0], row[1].isoformat()) if row else None

    def delete_session(self, digest: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM seo_reviewer_sessions WHERE digest=%s', (digest,))

    def save_failure(self, event: dict) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO seo_failure_events(id,run_id,job_id,data) VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                (event['id'],event['run_id'],event['job_id'],self.Jsonb(event)))

    def list_failures(self, job_id: str) -> list[dict]:
        with self.connect() as db:
            rows=db.execute('SELECT data FROM seo_failure_events WHERE job_id=%s ORDER BY created_at,id', (job_id,)).fetchall()
        return [row[0] for row in rows]

    def list_run_failures(self, run_id: str) -> list[dict]:
        with self.connect() as db:
            rows=db.execute('SELECT data FROM seo_failure_events WHERE run_id=%s ORDER BY created_at DESC,id DESC LIMIT 100', (run_id,)).fetchall()
        return [row[0] for row in rows]
