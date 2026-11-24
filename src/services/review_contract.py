"""Effective editorial policy shared by writing, review and preview exports."""
import hashlib
from src.services.link_policy import contract as link_contract

VERSION = 'content-review-4-optional-testimonials'


def sections(text):
    return [text[i:i+10000] for i in range(0, len(text), 10000)]


def contract_v1(state, settings):
    from src.combined.editorial import rules
    page_type = state.editorial.get('page_type') if state.editorial else state.briefing.page_type if state.briefing else state.selected_recommendation.page_type if state.selected_recommendation else 'landing_page'
    pack = rules(settings, page_type)
    approved = set(state.icp.approved_fact_sources)
    evidence = []
    for page in state.analysis.page_evidence if state.analysis else []:
        if page.url not in approved:
            continue
        # Ordered saved sections are authoritative when the preview text was shortened.
        original = ' '.join(s.get('text', '') for s in page.sections) if page.sections else page.content
        verified = bool(page.content_hash and hashlib.sha256(original.encode()).hexdigest()==page.content_hash)
        if page.content_hash and not verified:
            # Do not send changed evidence to a provider as an approved original.
            from src.services.quality import QualityReviewError
            raise QualityReviewError()
        text = '\n\n'.join(s.get('text', '') for s in page.sections) if page.sections else page.content
        evidence.append({'id': page.id, 'url': page.url, 'title': page.title,
                         'ordered_text_parts': sections(text),
                         'content_hash': page.content_hash,'hash_verified':verified,
                         'partial': page.content_truncated and not bool(page.sections)})
    result = {'version': VERSION, 'page_type': page_type,
            'precedence': pack['effective_policy'],
            'module_purposes': [{'id': m['id'], 'purpose': m['name'], 'rule': m.get('rule', '')}
                                for m in pack['modules']],
            'internal_link_policy': link_contract(state),
            'company_services': state.icp.services,
            'approved_reviews':pack['approved_reviews'].get('reviews',[]),
            'company_evidence': evidence,
            'testimonials': 'Optional. Without genuine approved reviews omit L12 and hero ratings; never invent testimonials or aggregate ratings.',
            'draft_review': 'Explicitly marked pending pricing, image, attribution and human approvals are release prerequisites, not prose defects. Do not request invented values or remove required modules.',
            'claims': 'SupportedClaim is for substantive tax-law assertions. Approved service descriptions and statements that no deadline is supplied do not require tax claims. Unsupported business assertions still block.',
            'landing_links': 'For landing pages L14 requires two to four verified guide links. L16 is the social-channel bar, NOT related reading.',
            'severity': 'Contradictions, unsupported assertions and applicable rule violations block. Minor style preferences are suggestions. Missing release inputs stay separate.'}
    from src.services.fee_presentation import context
    fee = context(state, settings)
    if fee is not None:
        result['fee_presentation'] = fee
    return result


def contract(state,settings):
    value=contract_v1(state,settings)
    value['version']='guided-material-qa-v1'
    value['materiality']='Minor presentation, capitalisation, emphasis and style are nonblocking suggestions. Material unsupported assertions, tax errors, contradictions and essential requirement violations block. Release prerequisites remain separate.'
    value['requirements_ownership']={'approved_forms':'deterministic','unique_destinations':'deterministic','provenance':'deterministic','page':'intent, coverage, metadata, cross-section consistency','module':'purpose, assertions, context, link relevance','claim':'official support, conditions and year'}
    return value
