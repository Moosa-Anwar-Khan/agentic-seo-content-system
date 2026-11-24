"""Typed research, claim and editorial contracts for the combined workflow."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field
from src.schemas.common import StrictModel

class SourceEvidence(StrictModel):
    id: str
    url: str
    kind: Literal['statute','administrative','judgment','competitor','example']
    text: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str
    truncated: bool = False
    status: Literal['read','partial','unavailable'] = 'read'
    note: str = ''
    extraction_method: Literal['direct','firecrawl'] = 'direct'

class SupportedClaim(StrictModel):
    id: str
    text: str
    source_id: str
    quote: str = Field(min_length=15)
    tax_year: int
    conditions: str = Field(min_length=5)

class Module(StrictModel):
    id: str = Field(pattern=r'^[SL]\d{2}[a-z]?$')
    markdown: str = Field(min_length=10)

class GraphicBrief(StrictModel):
    title: str
    facts: list[str] = Field(min_length=4,max_length=7)
    notebooklm_prompt: str
    alt: str
    caption: str
    filename: str = Field(pattern=r'^[a-z0-9-]+\.webp$')

class EditorialOutput(StrictModel):
    title: str
    meta_title: str = Field(min_length=20,max_length=60)
    meta_description: str = Field(min_length=140,max_length=155)
    slug: str = Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    page_type: Literal['blog','landing_page']
    landing_type: Literal['service','industry','tool','legal_form','price','location'] = 'industry'
    category: Literal['Buchhaltung','Einkommensteuer','Unternehmenssteuern','Selbständigkeit'] = 'Einkommensteuer'
    article_kind: Literal['guide','niche'] = 'guide'
    modules: list[Module] = Field(min_length=8)
    claims: list[SupportedClaim] = Field(default_factory=list)
    graphic: GraphicBrief
    signature_element: str = ''
    unresolved_questions: list[str] = Field(default_factory=list)

class CompetitorSummary(StrictModel):
    topics: list[str]
    questions: list[str]
    summary: str = Field(max_length=1500)
    limitations: list[str]
