"""Bounded observations of saved HTML; never infer qualifications or AI placement."""
import json
import re
import copy
from urllib.parse import urljoin,urlsplit
from src.security.sanitize import sanitize_text

TYPES={'Organization','LocalBusiness','AccountingService','ProfessionalService','Person',
       'Article','BlogPosting','WebSite','WebPage','BreadcrumbList','FAQPage'}
ORGANIZATIONS={'Organization','LocalBusiness','AccountingService','ProfessionalService'}
OFFICIAL={'gesetze-im-internet.de','bundesfinanzministerium.de','bundesfinanzhof.de','bundessteuerberaterkammer.de'}


def restore_saved_signals(state,saved):
    """Independent audit metadata survives graph saves only for unchanged evidence."""
    if not state.analysis or not saved.analysis:return
    previous={p.url:p for p in saved.analysis.page_evidence}
    for page in state.analysis.page_evidence:
        old=previous.get(page.url)
        if not old or not page.content_hash or old.content_hash!=page.content_hash:continue
        candidate=old.visibility_signals
        if candidate.get('collected') and (not page.visibility_signals.get('collected') or
                candidate.get('retrieved_at','')>page.visibility_signals.get('retrieved_at','')):
            page.visibility_signals=copy.deepcopy(candidate)


def html_signals(soup,url):
    """Keep flags/counts only, not JSON-LD bodies, author names or arbitrary values."""
    scripts=soup.find_all('script',attrs={'type':'application/ld+json'})
    visible=' '.join(soup.get_text(' ',strip=True).casefold().split())
    types=set();names=[];invalid=0;limited=len(scripts)>10;attribution=False
    for script in scripts[:10]:
        raw=script.string or script.get_text()
        if len(raw)>64000:limited=True;continue
        try:data=json.loads(raw)
        except (ValueError,RecursionError):invalid+=1;continue
        pending=[(data,0)];count=0
        while pending and count<256:
            node,depth=pending.pop();count+=1
            if depth>8:limited=True;continue
            if isinstance(node,list):pending.extend((v,depth+1) for v in node[:256]);limited|=len(node)>256
            elif isinstance(node,dict):
                kind=node.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind if isinstance(kind,list) else []
                known={k.rsplit('/',1)[-1] for k in kind if isinstance(k,str)}&TYPES;types|=known
                if known&ORGANIZATIONS and isinstance(node.get('name'),str):names.append(node['name'][:500])
                attribution|='Person' in known or bool(node.get('author'))
                pending.extend((v,depth+1) for v in list(node.values())[:256] if isinstance(v,(list,dict)))
                limited|=len(node)>256
        limited|=bool(pending)
    links=set()
    for anchor in soup.find_all('a',href=True):
        try:
            target=urlsplit(urljoin(url,anchor['href']));host=(target.hostname or '').casefold()
            if target.scheme=='https' and not target.username and not target.password and not target.query and any(host==h or host.endswith('.'+h) for h in OFFICIAL):
                links.add(sanitize_text(target._replace(fragment='').geturl(),2000))
        except ValueError:continue
    return {'collected':True,'structured_data_present':bool(scripts),'structured_types':sorted(types),
        'invalid_json_ld_blocks':invalid,'bounded_parse_incomplete':limited,
        'organization_names_checked':len(names),
        'organization_names_not_found_in_html_text':sum(' '.join(n.casefold().split()) not in visible for n in names),
        'attribution_candidate':attribution,'official_citation_links':sorted(links)[:50]}


def audit(state):
    pages=state.analysis.page_evidence if state.analysis else []
    observations=[]
    for page in pages:
        text=' '.join(s.get('text','') for s in page.sections) or page.content
        signals=page.visibility_signals
        observations.append({'url':page.url,'retrieved_at':str(page.retrieved_at) if page.retrieved_at else None,
            'content_hash':page.content_hash,'partial_content':page.content_truncated,
            'business_name_in_saved_text':bool(re.search(r'muster\s*(?:(?:&|und)\s*)?partner',text,re.I)),
            'question_headings':sum(h.level in {2,3} and (h.text.endswith('?') or bool(re.match(r'^(wie|was|wann|welche|wer|warum|kann)\b',h.text,re.I))) for h in page.headings),
            'structured_data':signals if signals.get('collected') else None,
            'professional_qualification_verified':False})
    return {'pages_inspected':len(pages),'business_identity_pages':sum(p['business_name_in_saved_text'] for p in observations),
        'question_heading_pages':sum(p['question_headings']>0 for p in observations),
        'structured_data_inspected_pages':sum(p['structured_data'] is not None for p in observations),
        'attribution_candidate_pages':sum(bool((p['structured_data'] or {}).get('attribution_candidate')) for p in observations),
        'official_link_pages':sum(bool((p['structured_data'] or {}).get('official_citation_links')) for p in observations),
        'structured_data_problem_pages':sum(bool((p['structured_data'] or {}).get('invalid_json_ld_blocks') or (p['structured_data'] or {}).get('organization_names_not_found_in_html_text') or (p['structured_data'] or {}).get('bounded_parse_incomplete')) for p in observations),
        'observations':observations,
        'limitations':[
            'These are saved-text and bounded JSON-LD observations, not ranking or citation predictions.',
            'Question headings are signposts; answer clarity still requires content review.',
            'A person or author field does not verify professional qualifications or permission to use an identity.',
            'Official links do not verify claims; original sources and professional review remain required.',
            'Markup, attribution and citation-link counts cover only inspected HTML pages.',
            'Name matching checks HTML text only, not rendered visibility or full schema correctness.',
            'Uncollected structured data is unknown. Partial pages cannot establish missing content.']}
