"""One effective linking contract for prompts, checks, QA and exports."""
from urllib.parse import urlsplit

VERSION = 'internal-links-1'


def contract(state):
    starter = state.internal_link_policy == 'starter_library'
    return {'page_type': (state.editorial.get('page_type') if state.editorial else state.briefing.page_type if state.briefing else None), 'landing_guide_module': 'L14', 'landing_guide_minimum': 2, 'landing_guide_maximum': 4, 'social_module': 'L16', 'version': VERSION, 'policy': state.internal_link_policy,
            'contextual_minimum': 1 if starter else 3,
            'related_minimum': 1 if starter else 6, 'related_maximum': 8,
            'service_minimum': 1, 'service_maximum': 3,
            'instruction': (
                'For blog articles use at least one relevant verified guide link in S08 and '
                'one to eight distinct existing relevant guide destinations in S15. Prefer '
                'additional suitable targets. The original three contextual and six to eight '
                'related links are expansion follow-ups, not release blockers under this '
                'explicit starter-library policy. Never add unrelated links to fill quotas.'
                if starter else
                'For blog articles require at least three relevant verified contextual guide '
                'links in S08 and six to eight distinct existing relevant guide destinations in S15.'),
            'unchanged_requirements': 'Verified provenance, semantic relevance, required modules and one to three appropriate service links remain mandatory. Landing-page rules remain unchanged.'}


def followups(state):
    if state.internal_link_policy != 'starter_library' or not state.editorial or state.editorial.get('page_type') != 'blog':
        return []
    from src.combined.markdown import parse
    links=parse('\n\n'.join('<!-- '+m['id']+' -->\n'+m['markdown'] for m in state.editorial['modules'])).links
    related={link.url.split('#')[0].split('?')[0].rstrip('/') for link in links if link.module=='S15'}
    contextual={link.url.split('#')[0].split('?')[0].rstrip('/') for link in links if link.module=='S08'} & related
    return ([{'code':'expand_guide_library','category':'editorial_followup','blocking':False,
              'message':f'Expand relevant guide links as the library grows: {len(contextual)} contextual and {len(related)} related destinations are present; the original targets are 3 and 6–8.',
              'action':'Add only relevant, verified destinations when those articles exist. This follow-up does not replace semantic or professional review.'}]
            if len(contextual)<3 or len(related)<6 else [])


def guide_candidates(state, keyword=''):
    """Conservative inventory candidates, not an assertion of semantic suitability."""
    import re
    pages=state.analysis.page_evidence if state.analysis else []
    candidates=[]
    for page in pages:
        path=urlsplit(page.url).path.casefold()
        if page.indexable is False or any(part in path for part in ('karriere','danke','impressum','datenschutz','sitemap','mandant-werden','kontakt')):
            continue
        title=page.title.casefold()
        # A commercial service directory is not automatically a guide library.
        if not re.search(r'ratgeber|rechner|berechn|tipps|anleitung|\bso\b|\bguide\b|\bblog\b',title+' '+path):
            continue
        candidates.append({'url':page.url,'title':page.title,'relevance':'requires_semantic_review'})
    candidates.extend(approved_guides(pages,{c['url'] for c in candidates}))
    return candidates


def approved_guides(pages, taken):
    """Owner-approved existing pages (inputs/config/guide-pages.json); only those present in the saved inventory."""
    import json
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'inputs/config/guide-pages.json'
    if not path.is_file():
        return []
    approved=json.loads(path.read_text(encoding='utf-8')).get('pages',[])
    return [{'url':page.url,'title':page.title,'relevance':'owner_approved_requires_semantic_review'}
            for page in pages if page.url in approved and page.url not in taken]
