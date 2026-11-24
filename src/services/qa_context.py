"""Complete saved link text and configured business facts for bounded QA."""
import hashlib

from src.services.quality import QualityReviewError
from src.services.review_contract import sections


def saved_page_text(page):
    original=' '.join(item.get('text','') for item in page.sections) if page.sections else page.content
    if page.content_hash and hashlib.sha256(original.encode()).hexdigest()!=page.content_hash:
        raise QualityReviewError('changed_link_evidence')
    return original


def link_target(page, *, metadata_only=False):
    metadata={'id':page.id,'url':page.url,'title':page.title,
        'headings':[h.model_dump() for h in page.headings]}
    if metadata_only:
        # The configured enquiry destination is validated as a CTA by existing
        # deterministic checks; it is not a proposed topic/related-reading page.
        return metadata
    original=saved_page_text(page)
    return {**metadata,
        'ordered_text_parts':sections(original),'content_hash':page.content_hash,
        'partial':not bool(original) or page.content_truncated and not bool(page.sections),
        'evidence_policy':'Read the complete saved text. Title wording alone cannot establish an irrelevant link. Partial evidence cannot establish absence.'}


def module_context(pack, modules, module_ids):
    relevant=[m for m in pack['modules'] if m['id'] in module_ids]
    required={fact_id for module in relevant for fact_id in module.get('brand_fact_ids',[])}
    facts={f['id']:f for f in pack['approved_facts']}
    if required-set(facts):
        raise ValueError('A configured module refers to missing approved brand facts')
    context={}
    if required:
        context['approved_brand_facts']=[{
            **facts[fact_id],'id':'brand-fact:'+fact_id,'fact_id':fact_id,
            'source':'inputs/brand-guidelines/facts.json',
            'content_hash':hashlib.sha256(facts[fact_id]['wording'].encode()).hexdigest(),
            'evidence_policy':'Configured business wording, not a tax-law authority or professional attestation.'
        } for fact_id in sorted(required)]
    placeholders=[]
    bodies={m['id']:m['markdown'] for m in modules}
    for module in relevant:
        template=module.get('draft_placeholder')
        if template and bodies.get(module['id'],'').strip()==template.strip():
            placeholders.append({'module_id':module['id'],'text':template,
                'requirement':'approved_attributed_statement','severity':'release',
                'policy':'This exact review-only placeholder is allowed in an unapproved draft. The approved statement remains mandatory before release. Do not invent or attribute a quotation.'})
    if placeholders:
        context['draft_placeholders']=placeholders
    return context
