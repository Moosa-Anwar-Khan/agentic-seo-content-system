from typing import Literal
from pydantic import Field, model_validator
from src.schemas.common import StrictModel
from src.schemas.decisions import PageEvidence, ResearchCoverage, SEORecommendation, PageType, PageAssessment
from datetime import date

DISCLAIMER = "Diese Inhalte ersetzen keine individuelle Steuerberatung. Bitte vereinbaren Sie ein persönliches Gespräch, damit wir Ihren konkreten Fall individuell prüfen können."

class Keyword(StrictModel):
    keyword: str
    volume: int | None = Field(None, ge=0)
    difficulty: float | None = Field(None, ge=0, le=100)
    intent: str | None = None
    position: float | None = Field(None, ge=0)
    clicks: int | None = Field(None, ge=0)
    impressions: int | None = Field(None, ge=0)
    ctr: float | None = Field(None, ge=0, le=1)
    page: str | None = None
    source: str
    reporting_start: date | None = None
    reporting_end: date | None = None
    measurement_context: dict = Field(default_factory=dict)

class SEOInterpretation(StrictModel):
    """The model interprets evidence; application code retains measured records."""
    opportunities: list[str] = Field(max_length=8)
    gaps: list[str] = Field(max_length=8)
    limitations: list[str] = Field(max_length=8)


class SEOAnalysis(StrictModel):
    data_mode: Literal["demo", "live"]
    keywords: list[Keyword]
    existing_pages: list[str]
    opportunities: list[str]
    gaps: list[str]
    limitations: list[str]
    page_evidence: list[PageEvidence] = Field(default_factory=list)
    coverage: ResearchCoverage = Field(default_factory=ResearchCoverage)
    recommendations: list[SEORecommendation] = Field(default_factory=list)
    page_assessments: list[PageAssessment] = Field(default_factory=list)

class Cluster(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    keyword: str
    kind: Literal["cluster", "blog"]
    intent: str

class Pillar(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    keyword: str
    intent: str
    clusters: list[Cluster] = Field(min_length=2)

class InternalLink(StrictModel):
    source: str
    target: str
    anchor: str
    reason: str

class TopicMap(StrictModel):
    seed_id: str = "immobiliensteuerberater"
    seed_title: str = "Immobiliensteuerberater"
    pillars: list[Pillar] = Field(min_length=6, max_length=8)
    links: list[InternalLink]
    assumptions: list[str]

    @model_validator(mode="after")
    def valid_graph(self):
        ids = [self.seed_id] + [p.id for p in self.pillars] + [c.id for p in self.pillars for c in p.clusters]
        if len(ids) != len(set(ids)):
            raise ValueError("Topic IDs must be unique")
        edges = {(e.source, e.target) for e in self.links}
        for edge in self.links:
            if edge.source not in ids or edge.target not in ids or edge.source == edge.target:
                raise ValueError("Every link must join two known topic IDs")
        for pillar in self.pillars:
            if (self.seed_id, pillar.id) not in edges or (pillar.id, self.seed_id) not in edges:
                raise ValueError("Each pillar needs links to and from the seed")
            for cluster in pillar.clusters:
                if (pillar.id, cluster.id) not in edges or (cluster.id, pillar.id) not in edges:
                    raise ValueError("Each cluster needs links to and from its pillar")
        return self

class Heading(StrictModel):
    h2: str
    h3: list[str]
    guidance: str

class Briefing(StrictModel):
    title: str
    primary_keyword: str
    secondary_keywords: list[str]
    target_audience: str
    search_intent: str
    reader_outcome: str
    suggested_words: int = Field(1400, ge=500, le=4000)
    headings: list[Heading] = Field(min_length=4)
    internal_link_ids: list[str] = Field(min_length=3)
    cta: str
    faq_questions: list[str] = Field(min_length=3)
    source_urls: list[str]
    legal_review_notes: list[str]
    editorial_notes: list[str]
    page_type: PageType = 'landing_page'
    target_url: str | None = None

class Subsection(StrictModel):
    heading: str
    paragraphs: list[str] = Field(min_length=1)
    bullets: list[str] = Field(default_factory=list)

class Section(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    heading: str
    paragraphs: list[str] = Field(min_length=1)
    subsections: list[Subsection] = Field(default_factory=list)

class FAQ(StrictModel):
    question: str
    answer: str

class Article(StrictModel):
    title: str
    meta_title: str = Field(min_length=20, max_length=65)
    meta_description: str = Field(min_length=100, max_length=165)
    introduction: list[str] = Field(min_length=2)
    sections: list[Section] = Field(min_length=4)
    internal_link_ids: list[str] = Field(default_factory=list)
    cta_heading: str
    cta_text: str
    faqs: list[FAQ] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER
    source_urls: list[str]
    editorial_notes: list[str]
    page_type: PageType = 'landing_page'
    primary_keyword: str = ''
    target_url: str | None = None

    @model_validator(mode="after")
    def fixed_disclaimer_and_ids(self):
        if self.disclaimer != DISCLAIMER:
            raise ValueError("The mandatory disclaimer must match exactly")
        ids = [s.id for s in self.sections] + ["faq", "beratung", "weiterlesen", "quellen"]
        if len(set(ids)) != len(ids):
            raise ValueError("Article section IDs must be unique")
        return self

class SourceQuotation(StrictModel):
    evidence_ref: str = Field(min_length=1)
    quote: str = Field(min_length=3)


class ReviewFinding(StrictModel):
    module_id: str
    claim_id: str | None = None
    quote: str
    rule: str = "editorial"
    category: Literal["content_defect", "suggestion", "release_prerequisite"] = "content_defect"
    severity: Literal["blocking", "suggestion", "release"] = "blocking"
    message_en: str = ""
    action_en: str = ""
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    source_quotes: list[SourceQuotation] = Field(default_factory=list, max_length=8)


class CheckResult(StrictModel):
    findings: list[ReviewFinding] = Field(default_factory=list)
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    target: Literal["briefing_agent", "content_agent"] = "content_agent"


class SelectedBriefing(Briefing):
    """New selected drafts require page type; stored legacy briefings keep defaults."""
    page_type: PageType


class SelectedArticle(Article):
    """Require selection fields at generation without changing historical records."""
    primary_keyword: str = Field(min_length=1)
    page_type: PageType
