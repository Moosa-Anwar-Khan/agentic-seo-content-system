from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4
from pydantic import Field
from src.schemas.common import StrictModel

Platform = Literal["website", "linkedin", "google_business_profile"]
Decision = Literal["pending", "approved", "rejected"]

class SocialPost(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    source_revision: int
    platform: Literal["linkedin", "google_business_profile"]
    text: str = Field(min_length=30, max_length=1800)
    status: Decision = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_by: str | None = None
    reviewer_id: str | None = None
    link_target: Literal['enquiry','article'] = 'enquiry'
    link_url: str = ''

class ImageAsset(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    content_id: str
    source_revision: int
    icp_id: str
    platform: Platform
    prompt: str
    provider: str
    model: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(ge=1)
    status: Decision = "pending"
    file_path: str
    generation_cost: float | None = None
    badge_version: str = "ai-sparkle-v1"
    parent_image_id: UUID | None = None
    reviewed_by: str | None = None
    reviewer_id: str | None = None
    focus_keyword: str | None = None
    focus_keyword_fallback: bool = False
    seo_filename: str = ""
    original_format: str | None = None
    final_format: Literal["webp", "jpeg", "png"] = "jpeg"
    original_dimensions: tuple[int, int] | None = None
    final_dimensions: tuple[int, int] | None = None
    original_file_size: int | None = None
    optimized_file_size: int | None = None
    size_reduction_percent: float | None = None
    optimization_status: Literal["legacy", "processing", "failed", "completed"] = "legacy"
    optimization_error: str | None = None
    png_requested: bool = False
    master_id: UUID | None = None


class ImageCreative(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    source_revision: int
    content_ids: dict[Platform, str]
    version: int = Field(ge=1)
    parent_master_id: UUID | None = None
    prompt: str
    provider: str
    model: str
    file_path: str
    original_format: str
    dimensions: tuple[int, int]
    file_size: int
    generation_cost: float | None = None
    fallback_used: bool = False
    png_requested: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
