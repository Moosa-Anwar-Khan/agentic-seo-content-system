"""Resumable, relevant coverage units using complete ordered page sections."""
import hashlib
import json
from urllib.parse import urlsplit
from pydantic import Field
from src.schemas.common import StrictModel
from src.schemas.decisions import AssessmentBatch,PageAssessment
from src.security.sanitize import InputSizeError


class CoverageAssessment(StrictModel):
    assessments: list[PageAssessment] = Field(min_length=1,max_length=2)


def assess(service,state,candidates,pages):
    from src.services.seo_decisions import ASSESSMENT_PROMPT,normalize
    from src.services.recovery import current_job
    from src.services.model_routes import resolve_route
    context=current_job.get()
    if context:context.step('website_assessment')
    excluded=[];eligible=[]
    for page in pages:
        path=urlsplit(page.url).path.casefold()
        if any(word in path for word in ['/datenschutz','/impressum','/karriere','/kontakt','/danke','/bewerbung','/cookie']):
            excluded.append({'url':page.url,'reason':'Administrative, privacy or recruitment page; retained in the original website inventory.'})
        else:eligible.append(page)
    rows=state.analysis.keywords if state.analysis else []
    measured={normalize(k.keyword) for k in rows if (k.impressions or 0)>=service.context.settings.seo_min_impressions or (k.volume or 0)>0} if state.analysis else {normalize(t['keyword']) for t in candidates}
    active=[t for t in candidates if normalize(t['keyword']) in measured][:5]
    deferred=[t for t in candidates if t not in active]
    result=[PageAssessment(topic_id=t['id'],relation='uncertain',reasoning='Die begrenzte Recherche priorisiert gemessene Themen. Für dieses Thema wurde noch keine vollständige semantische Seitenprüfung gekauft.') for t in deferred]
    plans={};scopes={}
    for topic in active:
        direct={k.page for k in rows if normalize(k.keyword)==normalize(topic['keyword']) and k.page}
        words=set(normalize(topic['keyword']).split())-{'steuerberater','steuerberatung','fuer','in','der','die','das','und','bei'}
        if not words:words=set(normalize(topic['keyword']).split())
        def score(page):
            text=page.title+' '+' '.join(h.text for h in page.headings)
            return len(words.intersection(normalize(text).split()))
        selected=sorted((p for p in eligible if p.url in direct or score(p)),key=lambda p:(p.url not in direct,-score(p)))[:3]
        scopes[topic['id']]={'urls':[p.url for p in selected],'includes_all_eligible_pages':len(selected)==len(eligible),
            'limitations':'Full original text of selected relevant pages; unreviewed pages cannot establish site-wide absence.'}
        groups=[];group=[];size=0
        for page in selected:
            sections=page.sections or [{'id':'saved-content','text':page.content}]
            document={'url':page.url,'title':page.title,'headings':[h.model_dump() for h in page.headings],
                'sections':sections,'content_truncated':page.content_truncated,'meta_title':page.meta_title,
                'meta_description':page.meta_description,'indexable':page.indexable}
            length=len(json.dumps(document,ensure_ascii=False))
            if length>250000:
                raise InputSizeError('A relevant full page exceeds the safe review unit; original source sections remain saved')
            if group and size+length>60000:groups.append(group);group=[];size=0
            group.append(document);size+=length
        if group:groups.append(group)
        plans[topic['id']]=groups
    store=service.context.storage;completed=0;finished=[];total=sum(map(len,plans.values()))
    def progress():
        state.research_snapshot['coverage_review']={'total':total,'completed':completed,'complete':completed==total,
            'policy':'relevant-complete-sections-v2','deferred_topic_ids':[t['id'] for t in deferred],
            'completed_topic_ids':finished.copy(),'topic_scopes':scopes,'excluded_pages':excluded}
        store.save_run_state(state)
    progress()
    for topic in active:
        matches=[]
        for group in plans[topic['id']]:
            route=resolve_route(service.context.settings,'seo_strategy')
            payload={'topics':[topic],'pages':group,'policy':'relevant-complete-sections-v2',
                'linking_policy':state.internal_link_policy,'model_signature':route.litellm_model,
                'review_scope':scopes[topic['id']],'supplied_source_urls':[p['url'] for p in group]}
            fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            key=f'coverage:{state.run_id}:{state.revision}:{fingerprint}'
            saved=store.get_operation(key)
            if context:context.step('website_assessment')
            if saved and saved['status']=='completed':batch=CoverageAssessment.model_validate(saved['value'])
            else:
                batch=service.context.llm.generate(str(state.run_id),CoverageAssessment,
                    ASSESSMENT_PROMPT+' Review every supplied ordered section. Return at most two relationships for this one topic. '
                    'An editorial issue must contain its literal evidence quote. No invented metrics or defects. '
                    'For none state a distinct reader need only when supported; never invent a service. '
                    'Incomplete pages cannot establish complete dedicated coverage. '
                    'None applies only to this supplied group; never claim site-wide absence.',payload,model_role='seo_strategy')
                service.validate_assessments([topic],[p for p in pages if p.url in payload['supplied_source_urls']],AssessmentBatch(assessments=batch.assessments))
                store.begin_operation(key,str(state.run_id));store.finish_operation(key,batch.model_dump(mode='json'))
            for item in batch.assessments:
                page=next((p for p in pages if p.url==item.page_url),None)
                if item.relation=='dedicated' and page and page.content_truncated:
                    item.relation='uncertain';item.issue=''
            completed+=1;matches.extend(batch.assessments);progress()
        positives=[a for a in matches if a.relation!='none'];unique={}
        for item in sorted(positives,key=lambda a:{'dedicated':0,'mention':1,'different_intent':2,'uncertain':3}[a.relation]):unique.setdefault(item.page_url,item)
        result.extend(list(unique.values())[:2] or matches[:1] or [PageAssessment(topic_id=topic['id'],relation='uncertain',reasoning='Keine geeigneten vollständigen Seiten für diese begrenzte Prüfung ausgewählt; fehlende Inhalte sind nicht bewiesen.')])
        finished.append(topic['id']);progress()
    return AssessmentBatch(assessments=result)
