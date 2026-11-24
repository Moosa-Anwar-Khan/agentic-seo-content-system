"""Positive, narrowly scoped evidence. No inference of missing articles or ranking causes."""
from src.schemas.decisions import SupportedChange


def scoped_coverage(pages,coverage,members):
    known={p['url']:p for p in pages}
    urls=[m['url'] for m in members]
    missing=[u for u in urls if u not in known]
    incomplete=[u for u in urls if u in known and known[u].get('content_truncated',True)]
    relevant_failures=[f for f in coverage.get('failures',[]) if any(
        word in f['url'].casefold() for word in ['immobil','vermiet','afa','anlage-v','kaufpreis'])]
    ready=bool(urls) and not missing and not incomplete and not relevant_failures
    return {'icp_1':{'complete':ready,'definition':'Vollständigkeit der verifizierten ICP-Seiten; keine Behauptung über alle möglichen Investorenthemen.',
                     'urls':urls,'missing':missing,'incomplete':incomplete,'unresolved_relevant_urls':relevant_failures},
            'site':{'complete':coverage.get('content_complete',coverage.get('inventory_complete',False)),
                    'definition':'Entdecktes Website-Inventar und sicher extrahierbare redaktionelle Inhalte.'}}


def contextual_changes(state,source_url):
    """Suggest a link only when its literal heading and a verified private-ICP destination match."""
    from src.services.seo_decisions import normalize,url_key
    pages={p.url:p for p in state.analysis.page_evidence}
    source=pages.get(source_url)
    if not source or source.indexable is not True or source.content_truncated or not source.links_complete:return []
    cohort_urls={m['url'] for m in state.research_snapshot.get('cohort',[])}
    aliases={url_key(o['url']):url_key(o['final_url']) for o in state.research_snapshot.get('website',{}).get('url_outcomes',[])
             if o.get('final_url')}
    links={aliases.get(url_key(u),url_key(u)) for u in source.internal_links}
    ignored={'steuerberater','steuerberatung','fuer','und','der','die','das','von','mit','in','online'}
    changes=[]
    for target in pages.values():
        if target.url==source.url or target.url not in cohort_urls or target.indexable is not True or target.content_truncated:continue
        if url_key(target.url) in links:continue
        # Later H1s can belong to repeated contact sections rather than the page subject.
        h1=next((h.text for h in target.headings if h.level==1),'')
        words=set(normalize(h1).split())-ignored
        if len(words)<2:continue
        section=next((s for s in source.sections if s.get('kind') in {'h2','h3','h4','h5','h6'}
            and not s.get('template') and len(s.get('text',''))<=120
            and words.issubset(set(normalize(s.get('text','')).split()))),None)
        if not section:continue
        changes.append(SupportedChange(kind='contextual_link',source_url=source.url,target_url=target.url,
            section_id=section['id'],evidence_quote=section['text'],anchor=section['text'],
            reasoning='Die vorhandene Überschrift benennt den Bedarf der verifizierten Zielseite. Im vollständig geprüften Hauptinhalt fehlt diese Verknüpfung. Ein kontextueller Link erleichtert die Orientierung; eine Rankingwirkung ist nicht nachgewiesen.'))
    return changes[:3]
