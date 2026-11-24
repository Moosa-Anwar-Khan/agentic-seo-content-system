from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4
from pydantic import Field, JsonValue
from src.schemas.common import StrictModel
from src.schemas.icp import ICP
from src.schemas.content import SEOAnalysis, TopicMap, Briefing, Article, CheckResult
from src.schemas.review import ReviewEvent, ProfessionalReviewRecord, FeePolicyRecord
from src.schemas.decisions import SEORecommendation, SelectionEvent

Status = Literal["new", "data_loaded", "seo_analysis_done", "topic_map_done", "awaiting_selection", "recommendations_only", "briefing_done", "draft_created", "brand_check_passed", "seo_check_passed", "needs_revision", "approved", "rejected", "exported", "failed"]
class RunState(StrictModel):
    run_id: UUID = Field(default_factory=uuid4)
    owner_user_id: str | None = None
    run_budget_usd: float | None = Field(default=None, gt=0, le=5)
    campaign_id: UUID | None = None
    campaign_budget_usd: float | None = Field(default=None, gt=0, le=5)
    research_scope: Literal['site', 'icp_1'] | None = None
    visibility_observations: list[dict[str, JsonValue]] = Field(default_factory=list)
    internal_link_policy: Literal['strict', 'starter_library'] = 'strict'
    fee_presentation: FeePolicyRecord | None = None
    icp: ICP
    status: Status = "new"
    revision: int = 0
    auto_revisions: int = 0
    human_revisions: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    analysis: SEOAnalysis | None = None
    topic_map: TopicMap | None = None
    briefing: Briefing | None = None
    article: Article | None = None
    html: str = ""
    combined_version: int = 0
    research_packet: dict[str, JsonValue] = Field(default_factory=dict)
    editorial: dict[str, JsonValue] = Field(default_factory=dict)
    release_blockers: list[str] = Field(default_factory=list)
    graphic_file: str = ''
    graphic_sha256: str = ''
    graphic_revision: int = -1
    professional_review: ProfessionalReviewRecord | None = None
    internal_workflow: dict[str, JsonValue] = Field(default_factory=dict)
    publication: dict[str, JsonValue] = Field(default_factory=dict)
    outcomes: list[dict[str, JsonValue]] = Field(default_factory=list)
    brand_check: CheckResult | None = None
    seo_check: CheckResult | None = None
    review_history: list[ReviewEvent] = Field(default_factory=list)
    human_feedback: str = ""
    feedback_target_agent: Literal["briefing_agent", "content_agent"] = "content_agent"
    feedback_resolutions: list[str] = Field(default_factory=list)
    executed_agents: list[str] = Field(default_factory=list)
    agent_outputs: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    tokens_used: int = 0
    error: str | None = None
    quality_checks_only: bool = False
    qa_manifest: dict[str, JsonValue] = Field(default_factory=dict)
    quality_repair_fingerprints: list[str] = Field(default_factory=list)
    editorial_change_history: list[dict[str, JsonValue]] = Field(default_factory=list)
    exported_paths: list[str] = Field(default_factory=list)
    # Missing version means a historical run; its existing checkpoint path is preserved.
    decision_workflow_version: int = Field(default=0, ge=0, le=1)
    selected_recommendation: SEORecommendation | None = None
    selection_history: list[SelectionEvent] = Field(default_factory=list)
    parent_run_id: UUID | None = None
    research_snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    selected_evidence: dict[str, JsonValue] = Field(default_factory=dict)
