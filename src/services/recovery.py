import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from src.schemas.jobs import Job
from src.storage.base import BaseStorage
from cryptography.fernet import Fernet


def encrypt_response(settings, content: str) -> str:
    """Keep final response text private; reasoning traces are never included."""
    return Fernet(settings.auth_encryption_key.get_secret_value().encode()).encrypt(content.encode('utf-8')).decode()


def decrypt_response(settings, content: str) -> str:
    return Fernet(settings.auth_encryption_key.get_secret_value().encode()).decrypt(content.encode()).decode('utf-8')


class UncertainOperation(RuntimeError):
    """A paid request may have completed without a stored result."""


@dataclass
class JobContext:
    job: Job
    storage: BaseStorage

    def step(self, name: str) -> None:
        self.authorize()
        # Each invocation starts without another request's diagnostic identity,
        # including recovery of the same graph node after a restart.
        self.job.current_step = name
        self.job.execution_context = {'component': name, 'operation': 'Execute workflow step'}
        self.storage.save_job(self.job)

    def details(self, **values):
        self.job.execution_context = {**self.job.execution_context, **values}
        self.storage.save_job(self.job)

    def authorize(self):
        from src.security.run_access import authorize_job
        authorize_job(self.job, self.storage)


current_job: ContextVar[JobContext | None] = ContextVar('current_job', default=None)


@contextmanager
def running_job(job: Job, storage: BaseStorage):
    handle = current_job.set(JobContext(job, storage))
    try:
        yield
    finally:
        current_job.reset(handle)


def operation_key(name: str, payload: Any) -> str | None:
    context = current_job.get()
    if context is None:
        return None
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                       default=str).encode('utf-8')).hexdigest()
    return f'{context.job.id}:{name}:{digest}'


def begin_paid(key: str | None, storage: BaseStorage, run_id: str) -> dict | None:
    context = current_job.get()
    if context:
        context.authorize()
    if key is None:
        return None
    entry = storage.begin_operation(key, run_id)
    if entry['status'] == 'pending':
        raise UncertainOperation('Paid request outcome is unknown')
    return entry


def private_result_path(root: Path, run_id: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    return root / 'runs' / run_id / 'paid-results' / (digest + '.raw')
