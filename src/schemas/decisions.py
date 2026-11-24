"""Evidence and reviewer decisions for the optional planning gate."""

from datetime import date, datetime, timezone
from typing import Literal
from pydantic import Field, model_validator
from src.schemas.common import StrictModel
from src.security.sanitize import sanitize_text

Action = Literal['protect', 'optimize', 'create_cluster', 'create_new', 'manual_review', 'investigate']
PageType = Literal['landing_page', 'blog']


class PageHeading(StrictModel):
    level: Literal[1, 2, 3, 4, 5, 6]
    text: str = Field(max_length=500)


class PageEvidence(StrictModel):
    id: str
    url: str
    title: str
    headings: list[PageHeading] = Field(default_factory=list)
    content: str = Field(default='', max_length=12000)
    content_truncated: bool = False
    retrieved_at: datetime | None = None
    modified_at: str | None = None
    sections: list[dict] = Field(default_factory=list)
    meta_title: str | None = None
    meta_description: str | None = None
    canonical: str | None = None
    indexable: bool | None = None
    internal_links: list[str] = Field(default_factory=list)
    links_complete: bool = False
    content_exclusions: list[dict] = Field(default_factory=list)
    content_hash: str | None = None
    visibility_signals: dict = Field(default_factory=dict)


class ResearchCoverage(StrictModel):
    inventory_complete: bool = False
    content_complete: bool | None = None
    gsc_complete: bool = False
    pages_retrieved: int = 0
    reporting_start: date | None = None
    reporting_end: date | None = None
    limitations: list[str] = Field(default_factory=list)


class PageAssessment(StrictModel):
    topic_id: str
    page_url: str | None = None
    relation: Literal['dedicated', 'mention', 'different_intent', 'none', 'uncertain']
    evidence_quote: str = Field(default='', max_length=600)
    issue: str = Field(default='', max_length=800)
    distinct_need: str = Field(default='', max_length=800)
    reasoning: str = Field(max_length=1200)


class AssessmentBatch(StrictModel):
    assessments: list[PageAssessment] = Field(max_length=100)


class LinkSuggestion(StrictModel):
    source_url: str
    target_url: str
    anchor: str
    reasoning: str


class SupportedChange(StrictModel):
    kind: Literal['contextual_link']
    source_url: str
    target_url: str
    section_id: str
    evidence_quote: str
    anchor: str
    reasoning: str
    scope: Literal['links_only'] = 'links_only'


class SEORecommendation(StrictModel):
    id: str
    topic_id: str
    title: str
    keyword: str
    search_intent: str
    page_type: PageType
    action: Action
    draft_action: Literal['optimize', 'create_cluster', 'create_new'] = 'create_new'
    target_url: str | None = None
    parent_url: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    reasoning: str
    uncertainties: list[str] = Field(default_factory=list)
    internal_link_suggestions: list[LinkSuggestion] = Field(default_factory=list)
    demo: bool = False
    research_needed: bool = False
    drafting_eligible: bool = True
    priority: int = 0
    priority_reasons: list[str] = Field(default_factory=list)
    measured_keywords: list[str] = Field(default_factory=list)
    supported_changes: list[SupportedChange] = Field(default_factory=list)
    links_ready: bool = False
    readiness_blockers: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    evidence_status: Literal['evidence_backed','needs_research'] = 'needs_research'
    reason_en: str = ''
    next_action_en: str = ''
    audience_profile_id: str | None = None
    profile_ready: bool = True
    search_market: Literal['hamburg','national_online','unspecified'] = 'unspecified'
    service_segment: Literal['property_investors','specialist_tax','general_tax'] = 'general_tax'


class SelectionRequest(StrictModel):
    recommendation_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    mode: Literal['draft', 'links_only'] = 'draft'
    override_protection: bool = False
    acknowledge_uncertainty: bool = False
    notes: str = Field(default='', max_length=3000)
    professional_support: bool = False
    verified_parent_url: str | None = Field(None,max_length=2000)

    @model_validator(mode='after')
    def clean_notes(self):
        object.__setattr__(self, 'notes', sanitize_text(self.notes, 3000))
        if (self.override_protection or self.acknowledge_uncertainty) and len(self.notes) < 10:
            raise ValueError('An explicit decision requires a reason of at least 10 characters')
        if self.professional_support and len(self.notes.strip())<40:
            raise ValueError('A professional assessment needs at least 40 characters')
        return self


class SelectionEvent(SelectionRequest):
    reviewer_name: str
    reviewer_id: str | None = None
    event_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
