"""Shared dashboard/PDF view model. Reads snapshots only; never calls providers."""
from datetime import date,timedelta
import math

ACTION_LABELS={'protect':'Vorhandene Seite schützen','optimize':'Gezielt überarbeiten','investigate':'Sichtbarkeit untersuchen',
    'manual_review':'Recherchebedarf','create_cluster':'Vertiefenden Beitrag prüfen','create_new':'Neue Seite prüfen'}

def metric(row):
    if row is None:return {'clicks':None,'impressions':None,'ctr':None,'position':None}
    clicks=float(row['clicks']);impressions=float(row['impressions'])
    if not math.isfinite(clicks) or not math.isfinite(impressions) or clicks<0 or impressions<clicks:raise ValueError('Invalid observation')
    return {'clicks':int(clicks),'impressions':int(impressions),'ctr':clicks/impressions if impressions else None,
        'position':float(row['position']) if impressions and row.get('position') is not None else None}

def summary(dataset):
    rows=dataset.get('rows',[])
    if len(rows)>1:raise ValueError('Summary must use a GSC aggregate query')
    return metric(rows[0] if dataset.get('available') and rows else None)

def dashboard(state,storage,scope='icp_1',country='de'):
    if scope not in {'icp_1','site'} or country not in {'de','all'}:raise ValueError('Invalid report scope')
    snap=state.research_snapshot;periods=snap.get('performance',{}).get(scope+'_'+country,{})
    current=summary(periods.get('current',{}).get('summary',{}));previous=summary(periods.get('previous',{}).get('summary',{}))
    changes={}
    for name in current:
        a,b=current[name],previous[name]
        changes[name]={'absolute':a-b if a is not None and b is not None else None,
            'percent':(a-b)/b*100 if name in {'clicks','impressions'} and a is not None and b not in {None,0} else None}
    daily=[];windows=snap.get('windows',{})
    if windows:
        dates={p:{r['keys'][0]:metric(r) for r in periods.get(p,{}).get('daily',{}).get('rows',[])} for p in ['current','previous']}
        start,end=map(date.fromisoformat,windows['current']);prior=date.fromisoformat(windows['previous'][0])
        for i in range((end-start).days+1):
            day=(start+timedelta(days=i)).isoformat();old=(prior+timedelta(days=i)).isoformat()
            a=dates['current'].get(day,metric(None));b=dates['previous'].get(old,metric(None))
            daily.append({'date':day,'previous_date':old,'clicks':a['clicks'],'impressions':a['impressions'],
                'previous_clicks':b['clicks'],'previous_impressions':b['impressions']})
    titles={p.url:p.title for p in state.analysis.page_evidence} if state.analysis else {}
    page_data=periods.get('current',{}).get('pages',{})
    top=sorted([{'url':r['keys'][0],'title':titles.get(r['keys'][0],r['keys'][0]),**metric(r)} for r in page_data.get('rows',[])],
        key=lambda p:-(p['clicks'] or 0))[:5]
    recs=sorted(state.analysis.recommendations,key=lambda r:r.priority) if state.analysis else []
    def rec(r):
        metrics=[{'keyword':k.keyword,'source':k.source,'page':k.page,'clicks':k.clicks,'impressions':k.impressions,'position':k.position,
            'volume':k.volume,'difficulty':k.difficulty,'reporting_start':str(k.reporting_start) if k.reporting_start else None,
            'reporting_end':str(k.reporting_end) if k.reporting_end else None,'location_code':k.measurement_context.get('location_code'),
            'language_code':k.measurement_context.get('language_code'),'country':k.measurement_context.get('country')}
            for i,k in enumerate(state.analysis.keywords) if f'keyword-{i}' in r.evidence_refs]
        citations=[{'url':a.page_url,'quote':a.evidence_quote,'relationship':a.relation,
            'page_id':p.id,'retrieved_at':str(p.retrieved_at) if p.retrieved_at else None,'content_hash':p.content_hash}
            for a in state.analysis.page_assessments if a.topic_id==r.topic_id and a.page_url
            for p in state.analysis.page_evidence if p.url==a.page_url]
        return {**r.model_dump(mode='json'),'action_label':ACTION_LABELS[r.action],'metrics':metrics,'website_evidence':citations}
    pillar_ids={p.id for p in state.topic_map.pillars} if state.topic_map else set()
    costs=storage.research_costs(str(state.run_id));limit=snap.get('budget_microusd')
    warnings=list(dict.fromkeys([*(state.analysis.limitations if state.analysis else []),*snap.get('limitations',[]),
        'GSC-Positionen sind Durchschnittswerte; niedrigere Werte bedeuten bessere beobachtete Positionen.',
        'Website-Gesamtwerte und gefilterte Seitenwerte nutzen unterschiedliche Aggregation und sind nicht als Anteile addierbar.',
        'Die Recherche zeigt keine gemessenen Umsatz- oder Lead-Steigerungen und beweist keine Ursache für Veränderungen.']))
    if snap:warnings.append('DataForSEO-Kosten werden aus Anbieterantworten erfasst. KI-Kosten können konservative Preis-Schätzungen einschließlich Sicherheitsmarge sein; die Budgetanzeige ist keine Anbieterrechnung.')
    for p in periods.values():
        for data in p.values():
            if data.get('complete') is False:warnings.append('Mindestens eine GSC-Abfrage ist begrenzt oder enthält ausgeschlossene Suchanfragen.')
    pillars=[rec(r) for r in recs if r.topic_id in pillar_ids]
    scope_quality=snap.get('scope_coverage',{}).get(scope)
    readiness=snap.get('report_readiness','not_collected')
    if scope_quality is not None:
        readiness=('ready' if scope_quality['complete'] and snap.get('dataforseo',{}).get('status')=='usable'
                   and current['clicks'] is not None and previous['clicks'] is not None else 'partial')
    if snap.get('assessment_validation',{}).get('status')=='partial':
        readiness='partial'
    if state.topic_map:
        for p in state.topic_map.pillars:
            if not any(r['topic_id']==p.id for r in pillars):
                pillars.append({'id':p.id,'topic_id':p.id,'title':p.title,'action_label':'Recherchebedarf','research_needed':True,
                    'drafting_eligible':False,'target_url':None,'reasoning':'Geplanter Pillar; Prüfnachweise wurden noch nicht abgeschlossen.',
                    'uncertainties':['Keine abgeschlossene, belegte Empfehlung vorhanden.'],'metrics':[]})
    from src.services.search_audit import findings
    from src.services.visibility_audit import audit
    return {'content_visibility_audit':audit(state),'technical_findings':findings(state),'crawler_access':snap.get('website',{}).get('crawler_access',[]),
        'run_id':str(state.run_id),'parent_run_id':str(state.parent_run_id) if state.parent_run_id else None,
        'status':state.status,'scope':scope,'scope_label':'Immobilieninvestoren (ICP 1)' if scope=='icp_1' else 'Gesamte Website',
        'report_readiness':readiness,'overall_report_readiness':snap.get('report_readiness','not_collected'),
        'scope_coverage':scope_quality,'repair_available':bool(snap.get('completed')),
        'country':country,'country_label':'Deutschland' if country=='de' else 'Alle Länder',
        'property':snap.get('property'),'created_at':snap.get('created_at'),'windows':windows,
        'performance_available':current['clicks'] is not None,'current':current,'previous':previous,'changes':changes,
        'daily':daily,'top_pages':top,'cohort':snap.get('cohort',[]),'pillars':pillars,
        'recommendations':[rec(r) for r in recs],'research_scope':state.research_scope,'campaign':storage.campaign_costs(str(state.run_id)),
        'sources':{'gsc':{'available':bool(periods),'country':country,'aggregation':periods.get('current',{}).get('summary',{}).get('aggregation'),
            'retrieved_at':periods.get('current',{}).get('summary',{}).get('retrieved_at'),
            'query_rows':len(snap.get('stages',{}).get('gsc_queries',{}).get('rows',[]))},
            'website':snap.get('website',{}),'assessment_validation':snap.get('assessment_validation',{}),'dataforseo':{**{k:v for k,v in snap.get('dataforseo',{}).items() if k!='keywords'},
                'keyword_rows':len(snap.get('dataforseo',{}).get('keywords',[])),
                'unique_keywords':len({k['keyword'].casefold() for k in snap.get('dataforseo',{}).get('keywords',[])})}},
        'budget':{'limit_usd':limit/1000000 if limit is not None else None,'recorded_usd':costs['recorded_microusd']/1000000,
            'reserved_usd':costs['reserved_microusd']/1000000,
            'remaining_usd':max(0,(limit-costs['recorded_microusd']-costs['reserved_microusd'])/1000000) if limit is not None else None,
            'inherited_research_costs':snap.get('inherited_research_costs')},
        'limitations':list(dict.fromkeys(warnings))}
