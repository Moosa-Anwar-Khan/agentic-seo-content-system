"""Closed value sets from the build prompt (§8, §12, §13, §16, §17)."""

from enum import StrEnum


class Mode(StrEnum):
    """Operating mode, fixed per workspace (ADR-0008)."""

    DEMO = "demo"
    LIVE = "live"


class PageRole(StrEnum):
    EDITORIAL_HUB = "editorial_hub"
    SUPPORTING = "supporting"
    CONVERSION = "conversion"


class PageType(StrEnum):
    BLOG = "blog"
    LANDING_SERVICE = "landing:service"
    LANDING_INDUSTRY = "landing:industry"
    LANDING_TOOL = "landing:tool"
    LANDING_LEGAL_FORM = "landing:legal_form"
    LANDING_PRICE = "landing:price"
    LANDING_LOCATION = "landing:location"

    @property
    def is_landing(self) -> bool:
        return self.value.startswith("landing:")


class Intent(StrEnum):
    INFORMATIONAL = "informational"
    COMMERCIAL_INVESTIGATION = "commercial_investigation"
    TRANSACTIONAL = "transactional"
    LOCAL = "local"


class Funnel(StrEnum):
    AWARENESS = "awareness"
    CONSIDERATION = "consideration"
    CONVERSION = "conversion"


class PageAction(StrEnum):
    NEW_PAGE = "NEW_PAGE"
    UPDATE_EXISTING = "UPDATE_EXISTING"
    EXPAND_SECTION = "EXPAND_SECTION"
    KEEP = "KEEP"
    DO_NOT_CREATE = "DO_NOT_CREATE"
    # plan v4: a missing service page (conversion target) and merging competing pages
    ADD_SERVICE_PAGE = "ADD_SERVICE_PAGE"
    MERGE_PAGES = "MERGE_PAGES"

    @property
    def produces_content(self) -> bool:
        return self in (PageAction.NEW_PAGE, PageAction.UPDATE_EXISTING, PageAction.EXPAND_SECTION,
                        PageAction.ADD_SERVICE_PAGE, PageAction.MERGE_PAGES)

    @property
    def needs_existing_target(self) -> bool:
        return self in (PageAction.UPDATE_EXISTING, PageAction.EXPAND_SECTION, PageAction.KEEP,
                        PageAction.MERGE_PAGES)

    @property
    def creates_page(self) -> bool:
        """The page does not exist yet."""
        return self in (PageAction.NEW_PAGE, PageAction.ADD_SERVICE_PAGE)


class BlogCategory(StrEnum):
    """The only blog categories allowed by BLOG S01 (no new categories, see K6)."""

    BUCHHALTUNG = "Buchhaltung"
    EINKOMMENSTEUER = "Einkommensteuer"
    GMBH_STEUERSTRATEGIE = "Unternehmenssteuern"
    SELBSTAENDIGKEIT = "Selbständigkeit"


class OverlapBand(StrEnum):
    DISTINCT = "distinct"
    REVIEW = "review"
    STRONG_OVERLAP = "strong_overlap"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"


class PublicationStatus(StrEnum):
    """Observed publication state. Import, planning or export never prove publication."""

    PUBLISHED = "published"
    UNPUBLISHED = "unpublished"
    UNKNOWN = "unknown"


class FetchStatus(StrEnum):
    NOT_FETCHED = "not_fetched"
    READ = "read"
    PARTIAL = "partial"
    HEURISTIC = "heuristic"
    JS_SHELL = "js_shell"
    NON_HTML = "non_html"
    PDF = "pdf"
    BLOCKED = "blocked"
    FAILED = "failed"

    @property
    def has_content(self) -> bool:
        """Only these states may carry a content summary or analysis."""
        return self in (FetchStatus.READ, FetchStatus.PARTIAL, FetchStatus.HEURISTIC)


class InventoryCoverage(StrEnum):
    PARTIAL = "partial"
    COMPLETE = "complete"


class InventoryOrigin(StrEnum):
    IMPORT_JSON = "import_json"
    IMPORT_CSV = "import_csv"
    SITEMAP = "sitemap"
    SYNTHETIC = "synthetic"


class ResearchMode(StrEnum):
    ICP = "icp"
    TOPIC = "topic"


class MetricStatus(StrEnum):
    MEASURED = "measured"
    MISSING = "missing"
    ERROR = "error"


class WorkflowState(StrEnum):
    RESEARCHED = "RESEARCHED"
    STRATEGICALLY_APPROVED = "STRATEGICALLY_APPROVED"
    TOPIC_MAPPED = "TOPIC_MAPPED"
    BRIEFED = "BRIEFED"
    WRITTEN = "WRITTEN"
    BRAND_REVIEW = "BRAND_REVIEW"
    SEO_REVIEW = "SEO_REVIEW"
    FACT_REVIEW = "FACT_REVIEW"
    FINAL_QA = "FINAL_QA"
    APPROVED = "APPROVED"
    EXPORTED = "EXPORTED"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"


class HumanRole(StrEnum):
    STRATEGIST = "strategist"
    EDITOR = "editor"
    GRAPHIC_REVIEWER = "graphic_reviewer"
    TAX_ADVISOR = "tax_advisor"


class ActorKind(StrEnum):
    HUMAN = "human"
    AI_MODEL = "ai_model"
    DETERMINISTIC = "deterministic"
    FIXTURE = "fixture"
    SYSTEM = "system"


class ReviewType(StrEnum):
    BRAND = "brand"
    SEO = "seo"
    FACT = "fact"
    FINAL_QA_CHECKS = "final_qa_checks"
    FINAL_QA_REVIEW = "final_qa_review"


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"

    @property
    def blocks(self) -> bool:
        return self in (Severity.BLOCKER, Severity.MAJOR)


class FindingStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"


class ApprovalKind(StrEnum):
    GRAPHIC = "graphic"
    EXPERT = "expert"


class AssetKind(StrEnum):
    INFOGRAPHIC = "infographic"
    HEADER_IMAGE = "header_image"


class RequestStatus(StrEnum):
    OPEN = "open"
    IN_PRODUCTION = "in_production"
    DONE = "done"
    CANCELLED = "cancelled"


class LinkKind(StrEnum):
    """A link to a published target, or a future connection kept as a manual task."""

    PUBLISHED = "published"
    FUTURE = "future"


class DecisionKind(StrEnum):
    INVENTORY_UPDATED = "inventory_updated"
    PLAN_PROPOSED = "plan_proposed"
    PLAN_APPROVED = "plan_approved"
    OVERLAP_RESOLVED = "overlap_resolved"
    PROMOTED = "promoted"
    STATE_CHANGED = "state_changed"
    HUMAN_APPROVAL = "human_approval"
    EXPORTED = "exported"
    NOTE = "note"
