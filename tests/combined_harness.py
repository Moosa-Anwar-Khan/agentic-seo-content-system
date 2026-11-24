"""Explicitly synthetic integrations for a new combined run, never live approval."""
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from src.orchestrator.workflow import Workflow
from src.schemas.content import Keyword
from src.integrations.dataforseo import DataForSEO
from src.combined.fetch import Fetcher
from tests.test_combined import install_reviewable_fixture, source
from tests.test_model_agnostic_images import MasterProvider


def install_combined_fakes(settings, monkeypatch):
    import litellm
    from src.combined.fetch import FetchStatus
    template=settings.model_copy(update={'data_dir':settings.data_dir/'template',
        'output_dir':settings.output_dir/'template','combined_mode':True,'demo_mode':True,'seo_decisions_enabled':True})
    with Workflow(template) as workflow:
        sample=install_reviewable_fixture(workflow,template,monkeypatch)
    settings.combined_mode=True;settings.seo_decisions_enabled=True;settings.demo_mode=False
    settings.llm_model_planning=settings.llm_model_drafting='deepseek/deepseek-flash'
    settings.deepseek_api_key='synthetic-no-network';settings.max_run_tokens=750000
    settings.dataforseo_login='synthetic-offline';settings.dataforseo_password='synthetic-offline'
    settings.gsc_access_token='synthetic-offline';settings.tavily_api_key='synthetic-offline'
    settings.tavily_request_max_usd=.01
    settings.ai_model_capabilities=json.dumps({'deepseek/deepseek-flash':{'json_output':True,'context_tokens':1000000}})
    settings.research_price_rates=json.dumps({'deepseek/deepseek-flash':{'input':.01,'output':.02,'verified_at':date.today().isoformat()}})
    settings.official_discovery_enabled=True
    calls=[];sources={name:0 for name in ['gsc','dataforseo','wordpress','competitor','official','discovery']}
    url=settings.wordpress_base_url+'/zielgruppen/immobilien/'
    excerpt='Immobiliensteuerberater begleiten Ihre Vermietung. Unterlagen werden im persönlichen Gespräch behandelt.'
    def gsc(self,seed):
        sources['gsc']+=1;self.coverage={'gsc_complete':True,'limitations':[]}
        return [Keyword(keyword=seed,page=url,position=12,impressions=150,clicks=2,ctr=2/150,
            source='Synthetische GSC-Testdaten',reporting_start=date(2026,7,1),reporting_end=date(2026,9,28))]
    def paid(self,seed):
        sources['dataforseo']+=1
        return [Keyword(keyword=seed,volume=120,source='Synthetische DataForSEO-Testdaten')]
    def pages(self):
        sources['wordpress']+=1;self.coverage={'inventory_complete':True,'pages_retrieved':3,'limitations':[]}
        return [{'url':p,'title':'Immobiliensteuerberater','excerpt':excerpt,
            'headings':[{'level':1,'text':'Immobiliensteuerberater'}],'content_truncated':False}
            for p in [url,settings.wordpress_base_url+'/blog/test-eins/',settings.wordpress_base_url+'/blog/test-zwei/']]
    def fetch(self,url,policy):
        sources['competitor']+=1
        return SimpleNamespace(status=FetchStatus.READ,text='Sie ordnen Ihre Fragen für die Beratung. Synthetische öffentliche Wettbewerberseite.',
            fetched_at=datetime.now(timezone.utc),note='Synthetischer Offline-Test.',truncated=False,extraction=SimpleNamespace(headings=[]))
    def retrieve(*args,**kwargs):sources['official']+=1;return [source().model_dump(mode='json')],[]
    def discovery(*args,**kwargs):sources['discovery']+=1;return [],{'mode':'synthetic-offline'}
    monkeypatch.setattr('src.integrations.google_search_console.GoogleSearchConsole.keywords',gsc)
    def query_window(self,start,end,dimensions,country='all',urls=None):
        sources['gsc']+=1
        keys=['immobiliensteuerberater',url] if dimensions==['query','page'] else [start.isoformat()] if dimensions==['date'] else [url] if dimensions else []
        return {'available':True,'complete':True,'rows':[{'keys':keys,'clicks':2,'impressions':150,'position':12}],
            'aggregation':'byProperty','retrieved_at':datetime.now(timezone.utc).isoformat()}
    def scan(self,extra_urls=()):
        from src.integrations.website_scan import extract_page
        sources['wordpress']+=1
        result=[extract_page('<h1>Immobiliensteuerberater</h1><p>'+excerpt+'</p>',p) for p in [url,settings.wordpress_base_url+'/blog/test-eins/',settings.wordpress_base_url+'/blog/test-zwei/']]
        return result,{'inventory_complete':True,'content_complete':True,'pages_retrieved':3,'limitations':[]}
    def research(self,shortlist,seeds,**kwargs):
        sources['dataforseo']+=1
        return [Keyword(keyword='immobiliensteuerberater',volume=120,source='DataForSEO synthetic',measurement_context={'location_code':2276,'language_code':'de'})],[],[{'retrieved_at':datetime.now(timezone.utc).isoformat()}]
    monkeypatch.setattr('src.integrations.google_search_console.GoogleSearchConsole.query_window',query_window)
    monkeypatch.setattr('src.integrations.website_scan.WebsiteScan.collect',scan)
    monkeypatch.setattr(DataForSEO,'research',research)
    monkeypatch.setattr(DataForSEO,'keywords',paid)
    monkeypatch.setattr(DataForSEO,'_lookup',lambda *args:[{'type':'organic','url':'https://example.org/synthetic-competitor'}])
    monkeypatch.setattr('src.integrations.wordpress.WordPressReadOnly.pages',pages)
    monkeypatch.setattr(Fetcher,'fetch',fetch)
    monkeypatch.setattr('src.combined.research.retrieve',retrieve)
    monkeypatch.setattr('src.integrations.official_research.discover',discovery)
    def completion(**kwargs):
        schema=json.loads(kwargs['messages'][0]['content'].split('JSON Schema: ',1)[1])['title']
        payload=json.loads(kwargs['messages'][1]['content']);calls.append(schema)
        if schema=='SEOInterpretation':value={'opportunities':['Synthetische Prüfung der vorhandenen Seite.'],'gaps':[],'limitations':['Nur Offline-Testdaten.']}
        elif schema=='CompetitorSummary':value={'topics':['Vorbereitung'],'questions':['Welche Unterlagen benötigen Sie?'],'summary':'Synthetischer Wettbewerberüberblick.','limitations':['Keine Rechtsquelle.']}
        elif schema=='TopicMap':value=json.loads((settings.input_dir/'fixtures/topic-map.json').read_text())
        elif schema=='AssessmentBatch':value={'assessments':[{'topic_id':'immobiliensteuerberater','page_url':url,
            'relation':'dedicated','evidence_quote':excerpt,'issue':excerpt,'reasoning':'Synthetische Zuordnung für die vollständige Prüfung.'}]}
        elif schema=='CoverageAssessment':
            topic=payload['topics'][0]
            value={'assessments':[{'topic_id':topic['id'],'page_url':url,'relation':'dedicated','evidence_quote':excerpt,'issue':excerpt,'reasoning':'Synthetische Zuordnung mit Textbeleg.'}]} if topic['keyword'].casefold()=='immobiliensteuerberater' else {'assessments':[{'topic_id':topic['id'],'relation':'none','reasoning':'Kein passender Inhalt in dieser synthetischen Gruppe.'}]}
        elif schema=='SelectedBriefing':value=sample.briefing.model_dump(mode='json')
        elif schema=='EditorialOutput':value=sample.editorial
        elif schema in {'QAUnitResult','PageQAUnitResult','ModuleQAUnitResult','ClaimQAUnitResult','GuidedPageQAUnitResult','GuidedModuleQAUnitResult','GuidedClaimQAUnitResult'}:value={'unit_id':payload['unit_id'],'assessments':[{'criterion':c,'complete':True} for c in payload.get('expected_criteria',[])],'coverage_complete':True,'passed':True,'findings':[],'warnings':[]}
        elif schema=='SocialDrafts':value={'linkedin':'Sie bereiten Ihre Fragen und Unterlagen für das persönliche Gespräch vor.',
             'google_business_profile':'Sie klären Ihre Fragen zu vermieteten Immobilien im persönlichen Gespräch.'}
        else:raise AssertionError('Unexpected synthetic contract: '+schema)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(value,ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=100,completion_tokens=100,total_tokens=200),_hidden_params={'response_cost':.00001})
    monkeypatch.setattr(litellm,'completion',completion)
    monkeypatch.setattr(litellm,'token_counter',lambda **kwargs:100)
    master=MasterProvider()
    monkeypatch.setattr('src.services.images.OpenAIImageProvider.generate',lambda self,prompt,size:master.generate(prompt,size))
    return {'calls':calls,'sources':sources,'master':master,'sample':sample}
