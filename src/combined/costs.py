"""Run-wide currency reservations shared by SEO and model calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from src.services.research_budget import micros

active_run = ContextVar('combined_paid_run', default=None)


@contextmanager
def paid_scope(settings, storage, run_id):
    token = active_run.set((settings, storage, run_id))
    try:
        yield
    finally:
        active_run.reset(token)


def seo_reservation(settings):
    from src.services.recovery import current_job
    current = current_job.get()
    if current:
        current.authorize()
    scope = active_run.get()
    if scope:
        _, storage, run_id = scope
    elif current:
        storage, run_id = current.storage, str(current.job.run_id)
    else:
        raise PermissionError('Paid SEO calls require a tracked run or durable job')
    ceiling = settings.max_run_usd
    if current and current.job.kind == 'research_refresh':
        ceiling = min(ceiling, current.job.payload.get('budget_usd', 1))
    reservation = storage.reserve_research_cost(run_id, micros('0.10'), micros(ceiling))
    return storage, reservation
