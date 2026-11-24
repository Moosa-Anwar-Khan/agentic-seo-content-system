from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4
from pydantic import Field
from src.schemas.common import StrictModel

JobKind = Literal['start', 'research_refresh', 'research_repair', 'selection', 'review', 'quality_checks', 'social', 'image_set', 'image_set_variation', 'single_image_variation', 'ai_visibility', 'evaluation', 'evaluation_images']
JobStatus = Literal['queued', 'running', 'completed', 'paused', 'failed']


class Job(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    kind: JobKind
    status: JobStatus = 'queued'
    idempotency_key: UUID
    request_fingerprint: str = ''
    reviewer: str
    reviewer_id: str | None = None
    payload: dict = Field(default_factory=dict)
    attempt_id: str | None = None
    execution_context: dict = Field(default_factory=dict)
    failure_summary: dict | None = None
    current_step: str = ''
    error: str | None = None
    error_code: str | None = None
    requires_paid_retry: bool = False
    source_revision: int | None = None
    result_revision: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobView(StrictModel):
    id: UUID
    run_id: UUID
    kind: JobKind
    status: JobStatus
    attempt_id: str | None = None
    failure_summary: dict | None = None
    current_step: str
    error: str | None
    error_code: str | None = None
    requires_paid_retry: bool = False
    source_revision: int | None = None
    result_revision: int | None = None
    error_info: dict | None = None
    created_at: datetime
    updated_at: datetime


def view(job: Job) -> JobView:
    from src.services.user_errors import guidance
    data = job.model_dump(exclude={'payload', 'reviewer', 'reviewer_id', 'idempotency_key', 'request_fingerprint', 'execution_context'})
    if job.error:
        from src.services.failures import error_info
        data['error_info'] = error_info(job.failure_summary) if job.failure_summary else guidance(job.error_code, str(job.id))
        if data['error_info']['code'] == 'processing' and job.kind != 'research_repair':
            data['error_info']['retry'] = 'may_charge'
    return JobView.model_validate(data)
