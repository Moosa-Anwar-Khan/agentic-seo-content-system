from datetime import date, datetime, timezone
from uuid import UUID
from typing import Literal
from pydantic import Field, model_validator
from src.schemas.common import StrictModel
from src.security.sanitize import sanitize_text
from src.combined.models import EditorialOutput


class FeePolicyChange(StrictModel):
    mode: Literal['numeric_prices', 'approved_wording_only']
    confirmed: Literal[True]


class FeePolicyRecord(StrictModel):
    mode: Literal['numeric_prices', 'approved_wording_only']
    version: Literal['fee-presentation-v1'] = 'fee-presentation-v1'
    revision: int
    actor_id: str | None = None
    source: Literal['authenticated_editor', 'local_editorial_repair']
    reason: str
    price_pack_sha256: str
    approved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EditorialEditRequest(StrictModel):
    edit_id: UUID
    expected_revision: int = Field(ge=0)
    editorial: EditorialOutput
    notes: str = Field(min_length=10, max_length=2000)
    fee_presentation: FeePolicyChange | None = None

class ReviewRequest(StrictModel):
    action: Literal["approved", "needs_revision", "rejected"]
    feedback_target_agent: Literal["briefing_agent", "content_agent"] = "content_agent"
    feedback_notes: str = Field(default="", max_length=3000)
    expected_revision: int = Field(ge=0)
    factual_review_confirmed: bool = False
    graphic_review_confirmed: bool = False
    manual_upload_confirmed: bool = False

    @model_validator(mode="after")
    def check_notes(self):
        cleaned = sanitize_text(self.feedback_notes, limit=3000)
        # Assignment through object avoids recursive assignment validation.
        object.__setattr__(self, "feedback_notes", cleaned)
        if self.action == "needs_revision" and len(cleaned) < 10:
            raise ValueError("Provide specific revision notes of at least 10 characters")
        return self

class ReviewEvent(ReviewRequest):
    reviewer_name: str
    reviewer_id: str | None = None
    event_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProfessionalReviewRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    factual_review_confirmed: Literal[True]
    graphic_review_confirmed: Literal[True]
    notes: str = Field(min_length=10, max_length=2000)


class ProfessionalReviewRecord(StrictModel):
    revision: int
    reviewer_id: str
    reviewer_name: str
    fingerprint: str
    notes: str
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublicationRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    url: str = Field(min_length=10, max_length=2048)
    confirmed_live: Literal[True]


class OutcomeRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    period_start: date
    period_end: date
    qualified_enquiries: int = Field(ge=0, le=100000)
    notes: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def valid_window(self):
        if self.period_start > self.period_end or self.period_end > date.today():
            raise ValueError('Outcome dates must describe a completed measurement period')
        return self
