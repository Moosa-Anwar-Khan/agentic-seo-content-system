from src.services.failures import diagnostic_boundary
from datetime import date, timedelta,datetime,timezone
from urllib.parse import quote
import httpx
from src.config.settings import Settings
from src.schemas.content import Keyword
from src.integrations.base import SearchDataSource
from src.security.sanitize import sanitize_text

class GoogleSearchConsole(SearchDataSource):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.coverage = {}

    @diagnostic_boundary('Google Search Console', 'Read search performance')
    def query_window(self,start,end,dimensions,country='all',urls=None):
        import re
        filters=[]
        if country=='de':filters.append({'dimension':'country','operator':'equals','expression':'deu'})
        if urls is not None:
            if not urls:return {'rows':[],'available':False,'reason':'Keine bestätigten ICP-Seiten.'}
            expression='^(?:'+'|'.join(re.escape(u) for u in urls)+')$'
            if len(expression)>4096:return {'rows':[],'available':False,'reason':'ICP-Filter überschreitet das sichere Abfragelimit.'}
            filters.append({'dimension':'page','operator':'includingRegex','expression':expression})
        query={'startDate':start.isoformat(),'endDate':end.isoformat(),'dimensions':dimensions,
            'type':'web','dataState':'final','aggregationType':'auto' if urls is not None or 'page' in dimensions else 'byProperty','rowLimit':25000}
        if filters:query['dimensionFilterGroups']=[{'groupType':'and','filters':filters}]
        url='https://www.googleapis.com/webmasters/v3/sites/'+quote(self.settings.gsc_site_url,safe='')+'/searchAnalytics/query'
        rows=[];complete=False;aggregation=None
        with httpx.Client(timeout=45,follow_redirects=False,headers={'Authorization':'Bearer '+self.access_token()}) as client:
            for offset in [0,25000]:
                response=client.post(url,json={**query,'startRow':offset});response.raise_for_status();body=response.json()
                batch=body.get('rows',[])
                if not isinstance(batch,list):raise ValueError('Invalid GSC row collection')
                rows.extend(batch);aggregation=body.get('responseAggregationType',aggregation)
                if len(batch)<25000:complete=True;break
        import math
        safe_rows=[];excluded=0
        for row in rows:
            if not isinstance(row,dict) or len(row.get('keys',[]))!=len(dimensions):raise ValueError('Invalid GSC dimensions')
            for field in ['clicks','impressions','position']:
                value=row.get(field)
                if type(value) not in {int,float} or not math.isfinite(value) or value<0:raise ValueError('Invalid GSC measurement')
            if row['clicks']>row['impressions']:raise ValueError('Invalid GSC click count')
            try:
                row={**row,'keys':[sanitize_text(str(k),1000) for k in row.get('keys',[])]}
            except ValueError:excluded+=1;continue
            safe_rows.append(row)
        return {'rows':safe_rows,'available':True,'complete':complete and not excluded,'excluded_rows':excluded,'aggregation':aggregation,
            'country':country,'dimensions':dimensions,'start':start.isoformat(),'end':end.isoformat(),
            'search_type':'web','data_state':'final','filters':filters,
            'retrieved_at':datetime.now(timezone.utc).isoformat()}

    def access_token(self) -> str:
        token = self.settings.gsc_access_token.get_secret_value()
        if token:
            return token
        if not self.settings.gsc_service_account_file:
            raise ValueError("Configure GSC_ACCESS_TOKEN or GSC_SERVICE_ACCOUNT_FILE")
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        creds = service_account.Credentials.from_service_account_file(str(self.settings.gsc_service_account_file), scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
        creds.refresh(Request())
        return str(creds.token)

    @diagnostic_boundary('Google Search Console', 'Read query evidence')
    def keywords(self, seed: str) -> list[Keyword]:
        end = date.today() - timedelta(days=3)
        start = end - timedelta(days=self.settings.gsc_days - 1)
        url = "https://www.googleapis.com/webmasters/v3/sites/" + quote(self.settings.gsc_site_url, safe="") + "/searchAnalytics/query"
        rows = []
        with httpx.Client(timeout=30, follow_redirects=False, headers={"Authorization": "Bearer " + self.access_token()}) as client:
            # Bounded pagination: API rows represent aggregate query/page statistics.
            for offset in range(0, 5000, 1000):
                response = client.post(url, json={"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["query", "page"], "rowLimit": 1000, "startRow": offset})
                response.raise_for_status()
                batch = response.json().get("rows", [])
                rows.extend(batch)
                if len(batch) < 1000:
                    break
        complete = len(batch) < 1000
        self.coverage = {'gsc_complete': complete, 'reporting_start': start.isoformat(), 'reporting_end': end.isoformat(),
                         'limitations': ['GSC liefert aggregierte Suchanfrage-/URL-Daten; anonymisierte oder nicht gemeldete Suchanfragen fehlen.'] +
                         ([] if complete else ['GSC-Abfrage auf 5.000 Zeilen begrenzt; Kennzahlen sind unvollständig.'])}
        result = []
        for row in rows:
            try:
                query = sanitize_text(row["keys"][0], 200)
                page = sanitize_text(row["keys"][1], 500)
            except ValueError:
                # Search query text may contain PII: drop suspect rows before prompts.
                continue
            result.append(Keyword(keyword=query, page=page, clicks=int(row["clicks"]), impressions=int(row["impressions"]), ctr=float(row["ctr"]), position=float(row["position"]), source=f"Aggregierte GSC-Daten {start}/{end}", reporting_start=start, reporting_end=end))
        return result
