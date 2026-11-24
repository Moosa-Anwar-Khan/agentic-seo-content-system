"""Findings about inspected HTML; these do not establish ranking causes."""
from urllib.parse import urlsplit


def findings(state):
    result=[];titles={}
    for page in state.analysis.page_evidence if state.analysis else []:
        def item(code,reason,evidence,action):
            result.append({'code':code,'url':page.url,'reason':reason,'evidence':evidence,
                'next_action':action,'retrieved_at':str(page.retrieved_at) if page.retrieved_at else None,
                'content_hash':page.content_hash,'ranking_cause_confirmed':False})
        if page.indexable is False:
            item('noindex','The inspected page contains a noindex directive.',{'indexable':False},
                'Confirm whether exclusion is intentional before proposing a live change.')
        if (page.meta_description is not None or page.retrieved_at is not None and page.meta_title is not None) and not (page.meta_description or '').strip():
            item('empty_description','The inspected HTML contains no usable meta description.',{'meta_description':page.meta_description},
                'Prepare an accurate description for review; Google may choose another snippet.')
        if page.canonical and urlsplit(page.canonical).hostname not in {None,urlsplit(page.url).hostname}:
            item('external_canonical','The inspected canonical points to a different host.',{'canonical':page.canonical},
                'Check the intended canonical with the website administrator; do not assume it is an error.')
        if page.meta_title:titles.setdefault(page.meta_title,[]).append(page.url)
    for title,urls in titles.items():
        if len(urls)>1:result.append({'code':'duplicate_title','url':urls[0],'reason':'Multiple inspected pages share this title.',
            'evidence':{'title':title,'urls':urls},'next_action':'Check whether these pages serve distinct needs. This is not proof of cannibalization.',
            'ranking_cause_confirmed':False})
    for failure in state.research_snapshot.get('website',{}).get('failures',[]):
        result.append({'code':failure.get('reason','unavailable'),'url':failure['url'],
            'reason':'Website collection could not inspect this URL.','evidence':{'http_status':failure.get('http_status')},
            'next_action':'Resolve the documented access or extraction problem before claiming a content gap.','ranking_cause_confirmed':False})
    return result
