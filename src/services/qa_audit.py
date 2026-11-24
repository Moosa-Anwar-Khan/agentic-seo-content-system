"""Deterministic observations, deliberately separate from semantic relevance."""
import re
import unicodedata

VERSION = 'qa-responsibilities-v1'


def exact_form(text):
    # Presentation marks do not alter a standalone approved statement. Do not
    # remove words: negations, guarantees and qualifiers still need review.
    text = re.sub(r'^\s*[-*+]\s+', '', text)
    return ' '.join(unicodedata.normalize('NFKC', text.replace('**', '')).casefold().split()).strip(' .:!')


def audit(state, settings, module_ids=None):
    from src.combined.editorial import rules, link_errors
    from src.combined.markdown import parse
    from src.services.qa_context import module_context
    if not state.editorial:
        return None
    modules = state.editorial['modules']
    selected = [m for m in modules if module_ids is None or m['id'] in module_ids]
    pack = rules(settings, state.editorial['page_type'])
    matches = []
    links = []
    pages = {p.url: p for p in state.analysis.page_evidence} if state.analysis else {}
    for module in selected:
        facts = module_context(pack, modules, [module['id']]).get('approved_brand_facts', [])
        for line in module['markdown'].splitlines():
            for fact in facts:
                for variant in [fact['wording'], *fact.get('short_forms', [])]:
                    if exact_form(line) == exact_form(variant):
                        matches.append({'module_id': module['id'], 'fact_id': fact['id'],
                            'quote': line.strip(), 'approved_form': variant,
                            'variant': 'full' if variant == fact['wording'] else 'short',
                            'content_hash': fact['content_hash']})
                        break
        for link in parse('<!-- '+module['id']+' -->\n'+module['markdown']).links:
            url = link.url.split('#')[0].split('?')[0]
            page = pages.get(url)
            links.append({'module_id': module['id'], 'url': url, 'verified': bool(page),
                'evidence_id': page.id if page else None})
    counts = [{'module_id': m['id'], 'unique_verified_destinations': len({
        link['url'].rstrip('/') for link in links if link['module_id'] == m['id'] and link['verified']})}
        for m in selected]
    return {'version': VERSION, 'approved_matches': matches, 'links': links,
        'link_counts': counts, 'link_errors': link_errors(state, settings),
        'limitations': 'Exact approved forms and verified destinations only. Contextual contradictions, unsupported assertions and audience relevance still require semantic review.'}
