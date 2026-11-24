from src.services.failures import diagnostic_boundary
import httpx
import json
from src.config.settings import Settings
from src.integrations.base import SearchDataSource
from src.schemas.content import Keyword
from src.security.sanitize import sanitize_text
from src.services.recovery import (current_job, operation_key, begin_paid,
                                   encrypt_response, decrypt_response, UncertainOperation)
from datetime import datetime, timezone,timedelta
from src.services.research_budget import reserve, settle


class SourceRequestError(RuntimeError):
    def __init__(self, code: str):
        super().__init__('DataForSEO request failed (' + code + ')')
        self.code = code

class DataForSEO(SearchDataSource):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.diagnostics = []

    @diagnostic_boundary('DataForSEO', 'Retrieve keyword or search evidence')
    def _lookup(self, route: str, task: dict) -> list[dict]:
        context=current_job.get()
        if context and context.job.kind=='research_repair':
            raise PermissionError('Website evidence repair must reuse saved DataForSEO evidence')
        if route not in {'keyword_overview','related_keywords','serp','ai_gemini'}:
            raise PermissionError('Unsupported analytics endpoint')
        if not self.settings.dataforseo_login.get_secret_value() or not self.settings.dataforseo_password.get_secret_value():
            raise SourceRequestError('source_auth')
        context = current_job.get()
        key = operation_key('dataforseo-' + route, task)
        prior = context.storage.get_operation(key) if key else None
        if not prior and context and context.job.kind=='research_refresh':
            state=context.storage.get_run_state(str(context.job.run_id))
            if state and state.parent_run_id:
                suffix=key.split(':',1)[1]
                for candidate in context.storage.list_operations(str(state.parent_run_id)):
                    saved=candidate.get('value') or {}
                    if candidate['status']!='completed' or not candidate['key'].endswith(':'+suffix) or not saved.get('retrieved_at'):continue
                    age=datetime.now(timezone.utc)-datetime.fromisoformat(saved['retrieved_at'])
                    if timedelta(0)<=age<=timedelta(days=30) and (saved.get('body') or saved.get('encrypted_content')):prior=candidate;break
        if prior and prior['status'] == 'completed':
            saved=prior['value']
            body = saved.get('body') or json.loads(decrypt_response(self.settings,saved['encrypted_content']))
        else:
            if prior and prior['status'] == 'pending':
                raise UncertainOperation('Paid SEO request outcome is unknown')
            saved = (prior or {}).get('value') or {}
            if prior and prior['status']=='failed' and not saved.get('encrypted_content'):
                raise SourceRequestError('source_http_error')
            if prior and prior['status'] in {'received', 'failed'} and saved.get('encrypted_content'):
                raw = decrypt_response(self.settings, saved['encrypted_content'])
                cost_reservation=saved.get('cost_reservation')
            else:
                if key:
                    encrypt_response(self.settings, '')  # Check encryption before charging.
                if self.settings.combined_mode:
                    from src.combined.costs import seo_reservation
                    charge_storage,cost_reservation=seo_reservation(self.settings)
                else:
                    charge_storage=context.storage if context else None
                    cost_reservation = reserve(context.storage, str(context.job.run_id), '0.10') if context else None
                if key:
                    begin_paid(key, context.storage, str(context.job.run_id))
                try:
                    with httpx.Client(timeout=60,follow_redirects=False,auth=(self.settings.dataforseo_login.get_secret_value(),self.settings.dataforseo_password.get_secret_value())) as client:
                        endpoint = ('ai_optimization/gemini/llm_responses/live' if route=='ai_gemini' else 'serp/google/organic/live/advanced' if route == 'serp' else 'dataforseo_labs/google/'+route+'/live')
                        response=client.post('https://api.dataforseo.com/v3/'+endpoint,json=[task])
                        response.raise_for_status()
                        raw=response.text
                except (httpx.TimeoutException, httpx.NetworkError):
                    raise UncertainOperation('Paid SEO request outcome is unknown') from None
                except httpx.HTTPStatusError as exc:
                    status=exc.response.status_code
                    if status >= 500:
                        raise UncertainOperation('Paid SEO request outcome is unknown') from None
                    if key:
                        context.storage.fail_operation(key)
                    if status in {400,401,403,429}: settle(charge_storage,cost_reservation,0)
                    error=SourceRequestError('source_auth' if status in {401,403} else 'source_http_error')
                    error.status_code=status
                    raise error from None
                if key:
                    context.storage.save_operation_response(key, {'encrypted_content': encrypt_response(self.settings, raw),'cost_reservation':cost_reservation,
                        'retrieved_at':datetime.now(timezone.utc).isoformat()})
            try:
                body=json.loads(raw)
            except (ValueError, TypeError):
                if key:
                    context.storage.fail_operation(key)
                raise SourceRequestError('source_invalid_response') from None
            if (not isinstance(body, dict) or not isinstance(body.get('tasks'), list) or
                    not body['tasks'] or not all(isinstance(t, dict) for t in body['tasks'])):
                if key:
                    context.storage.fail_operation(key)
                raise SourceRequestError('source_invalid_response')
            if 'cost_reservation' in locals() and cost_reservation:
                settle(charge_storage if 'charge_storage' in locals() else context.storage,cost_reservation,body.get('cost'))
            # Save each successful endpoint before parsing metrics or requesting the next one.
            if body.get('status_code') == 20000 and all(t.get('status_code') == 20000 for t in body.get('tasks', [])):
                if key:
                    context.storage.finish_operation(key, {'encrypted_content':encrypt_response(self.settings,raw), 'cost': body.get('cost'),
                                                           'retrieved_at':datetime.now(timezone.utc).isoformat(),
                                                           'task_ids': [t.get('id') for t in body.get('tasks', [])]})
        if body.get('status_code')!=20000 or any(t.get('status_code')!=20000 for t in body.get('tasks',[])):
            self.diagnostics.append({'endpoint':route,'status':'provider_error','requested_keywords':task.get('keywords',[task.get('keyword')]),
                'returned_items':0,'cost':body.get('cost'),'provider_status':body.get('status_code'),
                'task_statuses':[t.get('status_code') for t in body.get('tasks',[])],
                'task_ids':[t.get('id') for t in body.get('tasks',[])],
                'retrieved_at':((prior or {}).get('value') or {}).get('retrieved_at') or datetime.now(timezone.utc).isoformat()})
            raise SourceRequestError('source_api_error')
        results=[]
        for response_task in body.get('tasks',[]):
            if response_task.get('status_code')!=20000: raise SourceRequestError('source_api_error')
            if response_task.get('result') is not None and not isinstance(response_task['result'],list):
                raise SourceRequestError('source_invalid_response')
            for group in response_task.get('result') or []:
                if not isinstance(group, dict) or not isinstance(group.get('items') or [], list):
                    raise SourceRequestError('source_invalid_response')
                items=group.get('items') or []
                if not all(isinstance(item, dict) for item in items):
                    raise SourceRequestError('source_invalid_response')
                results.extend(items)
        returned=[(r.get('keyword_data') or r).get('keyword') for r in results] if route!='serp' else []
        self.diagnostics.append({'endpoint':route,'status':'usable' if results else 'empty',
            'requested_keywords':task.get('keywords',[task.get('keyword')]),'returned_items':len(results),
            'returned_keywords':returned,'missing_keywords':[k for k in task.get('keywords',[]) if k not in returned],
            'location_code':task.get('location_code'),'language_code':task.get('language_code'),
            'cost':0 if prior and prior.get('status')=='completed' else body.get('cost'),
            'original_cost':body.get('cost'),'task_ids':[t.get('id') for t in body['tasks']],
            'reused':bool(prior and prior.get('status')=='completed'),
            'retrieved_at':((prior or {}).get('value') or {}).get('retrieved_at') or datetime.now(timezone.utc).isoformat()})
        return results

    @staticmethod
    def parse_keywords(items, source, common):
        output=[]
        for raw in items:
            item=raw.get('keyword_data') or raw
            if not isinstance(item,dict) or not isinstance(item.get('keyword'),str):
                raise SourceRequestError('source_invalid_response')
            info=item.get('keyword_info') or {}; props=item.get('keyword_properties') or {}; intent=item.get('search_intent_info') or {}
            output.append(Keyword(keyword=sanitize_text(item['keyword'],200),volume=info.get('search_volume'),
                difficulty=props.get('keyword_difficulty'),intent=intent.get('main_intent'),source='DataForSEO '+source+'/live',
                measurement_context={**common,'keyword_info':info,'keyword_properties':props,'search_intent_info':intent}))
        return output

    def research(self, shortlist, seeds, *, bounded=False):
        common={'location_code':self.settings.dataforseo_location_code,'language_code':self.settings.dataforseo_language_code}
        shortlist=list(dict.fromkeys(sanitize_text(k,200).casefold() for k in shortlist))[:20 if bounded else 50]
        seeds=list(dict.fromkeys(sanitize_text(k,200).casefold() for k in seeds))[:2 if bounded else 6]
        if any(':' in k for k in shortlist+seeds): raise ValueError('Search operators are not permitted')
        rows=self.parse_keywords(self._lookup('keyword_overview',{**common,'keywords':shortlist}),'keyword_overview',common)
        # Stop paid expansion on a valid empty overview, rather than buying blind guesses.
        if not rows: return rows,[],self.diagnostics
        serps=[]
        try:
            for seed in ([] if bounded else seeds):
                rows+=self.parse_keywords(self._lookup('related_keywords',{**common,'keyword':seed,'depth':1,'limit':20,'include_seed_keyword':True}),'related_keywords',common)
            for seed in seeds:
                items=self._lookup('serp',{**common,'keyword':seed,'depth':10,'device':'desktop','os':'windows'})
                serps.append({'keyword':seed,'items':[{k:i.get(k) for k in ['type','rank_group','rank_absolute','url','title','description','domain']} for i in items if i.get('type')=='organic']})
        except SourceRequestError as exc:
            exc.keywords=rows;exc.serps=serps
            raise
        return rows,serps,self.diagnostics

    def keywords(self, seed: str) -> list[Keyword]:
        seed=sanitize_text(seed,200)
        common={'location_code':self.settings.dataforseo_location_code,'language_code':self.settings.dataforseo_language_code}
        # Two bounded analytics calls; provider charges apply in live mode.
        groups=[('keyword_overview',self._lookup('keyword_overview',{**common,'keywords':[seed]})),
                ('related_keywords',self._lookup('related_keywords',{**common,'keyword':seed,'depth':2,'limit':50,'include_seed_keyword':True}))]
        output=[]
        seen=set()
        for source,items in groups:
            for raw in items:
                item=raw.get('keyword_data') or raw
                word=sanitize_text(item['keyword'],200)
                if word in seen: continue
                seen.add(word)
                info=item.get('keyword_info') or {}
                props=item.get('keyword_properties') or {}
                intent=item.get('search_intent_info') or {}
                output.append(Keyword(keyword=word,volume=info.get('search_volume'),difficulty=props.get('keyword_difficulty'),intent=intent.get('main_intent'),source='DataForSEO '+source+'/live',
                    measurement_context={'location_code':common['location_code'],'language_code':common['language_code'],
                                         'keyword_info':info,'keyword_properties':props,'search_intent_info':intent}))
        return output
