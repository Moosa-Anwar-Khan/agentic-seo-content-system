from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import Field
from src.schemas.common import StrictModel


class AICall(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: str | None = None
    attempt_id: str | None = None
    provider_request_id: str | None = None
    run_id: UUID
    task: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    image_calls: int = 0
    cost: float | None = None
    latency_ms: int
    fallback_used: bool = False
    status: str
    error_code: str | None = None
    finish_reason: str | None = None
    max_output_tokens: int | None = None
    reasoning_tokens: int | None = None
    validation_fields: list[str] = Field(default_factory=list)
    normalizations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
