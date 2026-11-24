"""Ground blocking editorial findings in the draft instead of research context."""
import re
import unicodedata


class QualityReviewError(RuntimeError):
    code = 'quality_evidence'

    def __init__(self, reason=None):
        fields={'incomplete_review_coverage':'assessments','minor_detail_misclassified':'findings.severity','missing_material_evidence':'findings.material_impact','missing_contradiction_pair':'findings.conflicting_passages','unchanged_review_blocked':'review_plan', 'unknown_evidence_reference':'findings.evidence_refs',
            'release_input_misclassified':'findings.severity','changed_link_evidence':'verified_link_targets.content_hash',
            'approved_wording_rejected':'findings.quote','review_scope_conflict':'findings.rule',
            'deterministic_review_conflict':'findings.rule',
            'missing_link_source_quote':'findings.source_quotes','invalid_link_source_quote':'findings.source_quotes',
            'heading_only_link_source_quote':'findings.source_quotes'}
        self.reason = reason if reason in fields else None
        self.validation_fields = [fields[reason]] if self.reason else []
        super().__init__('Blocking quality findings are not grounded in the reviewed draft')


class QualityCheckFailed(RuntimeError):
    code = 'quality_failed'


def validate_brand_findings(result, public_text: str) -> None:
    """An unsupported model review must stop; it cannot authorize paid rewrites."""
    if result.passed:
        if result.errors:
            raise QualityReviewError()
        return
    if not result.errors:
        raise QualityReviewError()
    def normalized(value):
        return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    text = normalized(public_text)
    for error in result.errors:
        quotes = re.findall(r'[\u201e\u201c"]([^\u201e\u201c\u201d"]+)[\u201c\u201d"]', error)
        if not quotes or any(not normalized(quote) or normalized(quote) not in text for quote in quotes):
            raise QualityReviewError()
