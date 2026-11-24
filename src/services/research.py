"""Linked, bounded research. Measurements are retained; only semantic matching uses AI."""
import copy
from datetime import date,datetime,timedelta,timezone
from src.integrations.google_search_console import GoogleSearchConsole
from src.integrations.website_scan import WebsiteScan
from src.integrations.dataforseo import DataForSEO,SourceRequestError
from src.schemas.content import Keyword,SEOAnalysis,TopicMap
from src.schemas.decisions import ResearchCoverage
from src.services.seo_decisions import snapshot_pages,DecisionService,normalize,topics
from src.services.recovery import current_job
from src.services.export import LocalExporter

def cohort(pages):
    """Deterministic, inspectable ICP membership; no unrelated career/admin traffic."""
    result=[]
    for p in pages:
        if p.get('indexable') is False or any(w in p['url'].casefold() for w in ['karriere','impressum','datenschutz','danke','mandant-werden','immobilien-gmbh']):continue
        headings=' '.join(h['text'] for h in p.get('headings',[]) if h['level']==1).casefold()
        primary=p.get('title','').casefold()+' '+headings
        content=' '.join(s.get('text','') for s in p.get('sections',[])).casefold()
        if any(w in primary+content[:400] for w in ['projektentwickler','immobiliengesellschaften','immobilienunternehmen']) and not any(w in content[:400] for w in ['private vermieter','private immobilieninvestoren']):continue
        if any(w in primary for w in ['immobil','vermiet','anlage v','kaufpreisaufteilung']):
            result.append({'url':p['url'],'title':p['title'],'reason':'Expliziter Immobilien-/Vermietungsbezug in Titel oder H1; untergeordnete Erwähnungen reichen nicht.'})
    return result

class ResearchService:
    def __init__(self,workflow):self.workflow=workflow;self.settings=workflow.settings;self.storage=workflow.storage

    def execute(self,run_id):
        with self.storage.run_lock(run_id):
            state=self.workflow.get(run_id)
            if state.status=='awaiting_selection' and state.research_snapshot.get('completed'):
                return self.finish(state)
            parent=self.workflow.get(str(state.parent_run_id)) if state.parent_run_id else None
            context=current_job.get()
            repair=bool(context and context.job.kind=='research_repair')
            if repair and (not parent or not parent.research_snapshot.get('completed')):
                raise ValueError('Evidence repair needs a completed parent research snapshot')
            def stage(name,fetch):
                if context:context.step(name)
                saved=state.research_snapshot.setdefault('stages',{})
                if name not in saved:
                    saved[name]=fetch();self.storage.save_run_state(state)
                return saved[name]
            if not state.research_snapshot:
                state.research_snapshot={'version':2,'created_at':datetime.now(timezone.utc).isoformat(),
                    'property':self.settings.gsc_site_url,'budget_microusd':0 if repair else int((state.run_budget_usd or 1)*1000000),'stages':{},'limitations':[],
                    'legacy_dataforseo_diagnosis':'Historical evidence is reused only where a saved response and its original date can be verified.'}
                self.storage.save_run_state(state)
            end=date.today()-timedelta(days=3);start=end-timedelta(days=89)
            if self.settings.dataforseo_location_code!=2276 or self.settings.dataforseo_language_code!='de':
                raise ValueError('Germany/German research market mismatch')
            windows={'current':(start,end),'previous':(start-timedelta(days=90),start-timedelta(days=1))}
            if 'windows' not in state.research_snapshot:
                state.research_snapshot['windows']={k:[a.isoformat(),b.isoformat()] for k,(a,b) in windows.items()}
            else:windows={k:tuple(date.fromisoformat(x) for x in v) for k,v in state.research_snapshot['windows'].items()}
            gsc=GoogleSearchConsole(self.settings)
            observed=stage('gsc_queries',lambda:gsc.query_window(*windows['current'],['query','page'],'de'))
            keywords=[]
            for row in observed['rows']:
                try:
                    from src.security.sanitize import sanitize_text
                    keywords.append(Keyword(keyword=sanitize_text(row['keys'][0],200),page=sanitize_text(row['keys'][1],1000),
                        clicks=int(row['clicks']),impressions=int(row['impressions']),ctr=row['clicks']/row['impressions'] if row['impressions'] else 0,position=float(row['position']),
                        source='GSC query/page',reporting_start=windows['current'][0],reporting_end=windows['current'][1],
                        measurement_context={'country':'de','search_type':'web','aggregation':observed.get('aggregation')}))
                except ValueError:state.research_snapshot['limitations'].append('Eine personenbezogene oder unsichere Suchanfrage wurde ausgeschlossen.')
            website=stage('website',lambda:dict(zip(['pages','coverage'],WebsiteScan(self.settings,self.storage,str(state.run_id)).collect(k.page for k in keywords if k.page))))
            pages=website['pages'];members=cohort(pages)
            state.research_snapshot['cohort']=members
            state.research_snapshot['website']=website['coverage']
            from src.services.website_evidence import scoped_coverage
            state.research_snapshot['scope_coverage']=scoped_coverage(pages,website['coverage'],members)
            # Whole-site research is driven by observed queries, not an inherited ICP-only plan.
            state.topic_map=copy.deepcopy(parent.topic_map) if parent and state.research_scope!='site' else None
            words=['immobil','vermiet','afa','abschreib','anlage v','kaufpreis','schuldzins']
            relevant=sorted((k for k in keywords if (state.research_scope=='site' and any(w in k.keyword.casefold() for w in ['steuer','buchhalt','jahresabsch','sevdesk','lexware',*words]) or state.research_scope!='site' and any(w in k.keyword.casefold() for w in words)) and 'muster' not in k.keyword.casefold()),key=lambda k:-(k.impressions or 0))
            relevant=list({k.keyword.casefold():k for k in reversed(relevant)}.values())
            relevant.sort(key=lambda k:-(k.impressions or 0))
            shortlist=list(dict.fromkeys([k.keyword for k in relevant]+[state.icp.seed_topic,'Steuerberater','Steuerberatung']))[:20]
            seeds=shortlist[:2]
            def paid():
                if repair:
                    saved=copy.deepcopy(parent.research_snapshot.get('dataforseo',{}))
                    if 'keywords' not in saved:raise ValueError('Saved DataForSEO evidence is unavailable')
                    dates=[d['retrieved_at'] for d in saved.get('diagnostics',[]) if d.get('retrieved_at')]
                    if not dates or any(not timedelta(0)<=datetime.now(timezone.utc)-datetime.fromisoformat(d)<=timedelta(days=30) for d in dates):
                        raise ValueError('Saved DataForSEO evidence is outside the 30-day reuse window')
                    saved['reused_from_run_id']=str(parent.run_id)
                    for diagnostic in saved.get('diagnostics',[]):
                        diagnostic.setdefault('original_cost',diagnostic.get('cost'))
                        diagnostic['cost']=0;diagnostic['reused']=True
                        diagnostic.setdefault('source_run_id',str(parent.run_id))
                    state.research_snapshot['inherited_research_costs']=copy.deepcopy(parent.research_snapshot.get('inherited_research_costs') or
                        {'run_id':str(parent.run_id),**self.storage.research_costs(str(parent.run_id)),'new_paid_calls':False})
                    return saved
                client=DataForSEO(self.settings)
                if not self.settings.demo_mode:
                    from src.services.model_routes import resolve_route
                    from src.services.research_budget import text_upper_price
                    text_upper_price(self.settings,resolve_route(self.settings,'seo_strategy'),1,1)
                try:
                    rows,serps,diagnostics=client.research(shortlist,seeds, bounded=True) if state.research_scope else client.research(shortlist,seeds)
                    return {'keywords':[k.model_dump(mode='json') for k in rows],'serps':serps,'diagnostics':diagnostics,
                        'usable':bool(rows),'status':'usable' if rows else 'empty'}
                except SourceRequestError as exc:
                    # A source failure produces an explicitly partial report, never fabricated demand.
                    return {'keywords':[k.model_dump(mode='json') for k in getattr(exc,'keywords',[])],
                        'serps':getattr(exc,'serps',[]),'diagnostics':client.diagnostics,'usable':bool(getattr(exc,'keywords',[])),
                        'complete':False,'status':exc.code}
            paid_result=stage('dataforseo',paid)
            state.research_snapshot['dataforseo']=paid_result
            keywords += [Keyword.model_validate(k) for k in paid_result['keywords']]
            limitations=list(dict.fromkeys([*state.research_snapshot['limitations'],*website['coverage']['limitations'],
                'GSC meldet nicht alle anonymisierten Suchanfragen. DataForSEO-Werte sind Schätzungen, keine tatsächlichen Website-Besuche.',
                'SERP-Auswertungen sind Momentaufnahmen, kein vollständiger Wettbewerber-Audit.']))
            if not paid_result['usable']:limitations.append('DataForSEO lieferte keine verwendbaren Nachfragenachweise; fehlende Werte sind unbekannt.')
            elif paid_result.get('status')!='usable':limitations.append('DataForSEO lieferte verwendbare Teilergebnisse; ein späterer Fehler stoppte die weitere kostenpflichtige Recherche.')
            state.analysis=SEOAnalysis(data_mode='demo' if self.settings.demo_mode else 'live',keywords=keywords,
                existing_pages=[p['url'] for p in pages],page_evidence=snapshot_pages(pages,self.settings.wordpress_base_url),
                opportunities=['Vorhandene passende Seiten anhand belegter Nachfrage und konkreter Textstellen prüfen.'],
                gaps=['Nicht bestätigte Inhalte bleiben Recherchebedarf; Auszüge beweisen keine fehlende Abdeckung.'],limitations=limitations,
                coverage=ResearchCoverage(inventory_complete=website['coverage']['inventory_complete'],gsc_complete=observed.get('complete',False),
                    content_complete=website['coverage'].get('content_complete'),
                    pages_retrieved=len(pages),reporting_start=windows['current'][0],reporting_end=windows['current'][1],limitations=limitations))
            self.storage.save_run_state(state)
            if paid_result.get('status') not in {'usable','empty'}:
                # A failed external request ends this paid attempt. Retain all
                # measurements; do not buy model assessment after the failure.
                state.status='failed'
                self.storage.save_run_state(state)
                raise SourceRequestError(paid_result['status'])
            performances={}
            for scope in ['site','icp_1']:
                for country in ['de','all']:
                    periods={}
                    for period,window in windows.items():
                        urls=[m['url'] for m in members] if scope=='icp_1' else None
                        periods[period]={}
                        for name,dimensions in [('summary',[]),('daily',['date']),('pages',['page'])]:
                            periods[period][name]=stage(f'gsc_{scope}_{country}_{period}_{name}',
                                lambda w=window,d=dimensions,c=country,u=urls:gsc.query_window(*w,d,c,u))
                    performances[scope+'_'+country]=periods
            state.research_snapshot['performance']=performances
            self.storage.save_run_state(state)
            if state.topic_map is None:
                def make_map():
                    if self.settings.demo_mode:
                        return TopicMap.model_validate_json((self.settings.input_dir/'fixtures/topic-map.json').read_text()).model_dump(mode='json')
                    from src.agents.topic_map_agent import TopicMapAgent
                    version=state.decision_workflow_version
                    try:
                        state.decision_workflow_version=0
                        TopicMapAgent(self.workflow.context).execute(state)
                        return state.topic_map.model_dump(mode='json')
                    finally:state.decision_workflow_version=version
                state.topic_map=TopicMap.model_validate(stage('topic_map_agent',make_map))
            if state.research_scope=='site':
                state.research_snapshot['observed_topics']=[{'id':'observed-'+str(i),'title':k.keyword,'keyword':k.keyword,
                    'intent':'informational' if any(w in k.keyword.casefold() for w in ['wie ','was ','wann ','berechnen','absetzen']) else 'commercial',
                    'page_type':'blog' if any(w in k.keyword.casefold() for w in ['wie ','was ','wann ','berechnen','absetzen']) else 'landing_page'}
                    for i,k in enumerate(relevant[:20])]
            def decisions():
                service=DecisionService(self.workflow.context)
                if repair:
                    from src.schemas.decisions import AssessmentBatch
                    kept=[]
                    for assessment in parent.analysis.page_assessments:
                        try:service.validate_assessments(topics(state),state.analysis.page_evidence,AssessmentBatch(assessments=[assessment]))
                        except ValueError:continue
                        # A saved positive relationship is retained; absence/inferred defects need fresh assessment.
                        if assessment.relation=='none':continue
                        safe=assessment.model_copy(deep=True);safe.issue='';safe.distinct_need=''
                        safe.reasoning='Gespeicherte Zuordnung mit unverändertem wörtlichem Beleg bestätigt; redaktioneller Änderungsbedarf bleibt separat zu prüfen.'
                        kept.append(safe)
                    batch=AssessmentBatch(assessments=kept)
                    state.research_snapshot['assessment_reuse']={'run_id':str(parent.run_id),'relationships_verified':len(kept),'new_paid_calls':False}
                    result=service.build(state,batch,quarantine_invalid=True)
                elif (not paid_result['usable'] or paid_result.get('status')!='usable') and not state.research_scope:
                    batch=self.local_assessments(state,service)
                    result=service.build(state,batch,quarantine_invalid=True)
                else:result=service.build(state,quarantine_invalid=True)
                return {'recommendations':[r.model_dump(mode='json') for r in result],
                    'assessments':[a.model_dump(mode='json') for a in state.analysis.page_assessments]}
            recommendations=stage('recommendations',decisions)
            from src.schemas.decisions import SEORecommendation,PageAssessment
            state.analysis.recommendations=[SEORecommendation.model_validate(r) for r in recommendations['recommendations']]
            state.analysis.page_assessments=[PageAssessment.model_validate(a) for a in recommendations['assessments']]
            state.analysis.recommendations.sort(key=lambda r:r.priority)
            if self.settings.combined_mode and state.research_scope:
                state.combined_version=1
                state.research_packet={'mode':'live','serp':paid_result.get('serps',[]),'limitations':limitations,'strategy_complete':True}
            elif self.settings.combined_mode:
                state.combined_version=1
                if repair:
                    state.research_packet=copy.deepcopy(parent.research_packet)
                else:
                    from src.combined.research import collect_strategy
                    from src.combined.costs import paid_scope
                    with paid_scope(self.settings,self.storage,str(state.run_id)):
                        collect_strategy(self.workflow.context,state)
            state.research_snapshot['completed']=True
            state.research_snapshot['report_readiness']='partial' if not website['coverage'].get('content_complete',website['coverage']['inventory_complete']) or paid_result.get('status')!='usable' or state.research_snapshot.get('assessment_validation',{}).get('status')=='partial' else 'ready'
            state.research_snapshot['limitations']=list(dict.fromkeys([*limitations,*state.analysis.limitations,*state.research_snapshot['limitations']]))
            state.status='awaiting_selection';self.storage.save_run_state(state)
            return self.finish(state)

    @staticmethod
    def local_assessments(state,service):
        from src.schemas.decisions import AssessmentBatch,PageAssessment
        from src.services.verified_recommendations import ALIASES
        batch=[]
        for topic in topics(state):
            allowed={normalize(topic['keyword']),*[normalize(w) for w in ALIASES.get(topic['keyword'].casefold(),[])]}
            observed=sorted((k for k in state.analysis.keywords if normalize(k.keyword) in allowed and k.page),key=lambda k:-(k.impressions or 0))
            page=next((p for k in observed for p in state.analysis.page_evidence if p.url==k.page),None)
            if page is None:page=next(iter(service._candidate_pages([topic],state.analysis.page_evidence)),None)
            if page and page.title:
                quote=next((s['text'] for s in page.sections if s.get('kind')=='p' and not s.get('template') and any(w in normalize(s['text']) for w in allowed)),page.title)
                batch.append(PageAssessment(topic_id=topic['id'],relation='uncertain',page_url=page.url,evidence_quote=quote[:600],
                    reasoning='Gespeicherte Suchanfrage-/Seitenbelege oder Textstellen zeigen einen Prüfpfad; die Inhaltszuordnung ist nicht fachlich bestätigt.'))
        return AssessmentBatch(assessments=batch)

    def finish(self,state):
        run_id=str(state.run_id)
        if self.workflow.graph.get_state(self.workflow._config(run_id)).next!=('planning_selection',):
            self.workflow.graph.update_state(self.workflow._config(run_id),{'run':state.model_dump(mode='json')},as_node='topic_map_agent')
            self.workflow.graph.invoke(None,self.workflow._config(run_id),durability='sync')
        state=self.workflow.get(run_id)
        state.exported_paths=LocalExporter(self.settings.output_dir,self.settings).write_planning_bundle(state)
        self.storage.save_run_state(state)
        return state
