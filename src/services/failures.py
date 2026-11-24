"""Allowlisted, persistent failure evidence. Never store exception messages or inputs."""
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import logging
import re
import traceback

LABELS = {
 'ai_visibility':'AI Visibility', 'website_assessment':'Website Coverage Review',
 'seo_strategy_agent':'SEO Strategy', 'topic_map_agent':'Topic Planning',
 'briefing_agent':'Briefing', 'content_agent':'Article Writing',
 'brand_check_agent':'Brand Check', 'seo_check_agent':'SEO QA',
 'seo_qa_agent':'SEO QA', 'brand_agent':'Brand Check', 'seo_agent':'SEO QA', 'export_agent':'Local Export',
 'publisher_agent':'Local Export', 'seo_strategy':'SEO Strategy', 'topic_map':'Topic Planning',
 'briefing':'Briefing', 'content':'Article Writing', 'seo_qa':'SEO QA',
 'social_content':'Social Drafts', 'social':'Social Drafts', 'image_generation':'Image Generation',
 'gsc':'Google Search Console', 'website':'Website Evidence', 'dataforseo':'DataForSEO',
 'social-posts':'Social Drafts','image-sets':'Image Generation','images':'Image Generation','selection':'Topic Selection',
 'official_research':'Official Source Research', 'graphic':'Infographic Upload',
 'editorial':'Local Draft Editor', 'reviews':'Editorial Review', 'professional-reviews':'Professional Review',
 'publication':'Publication Record', 'outcomes':'Enquiry Measurement', 'bundle':'Local Export',
}
MISSING = {'graphic_input','preflight_blocked','input_size','input_privacy','competitor_evidence','research_budget',
 'run_budget','image_budget','configuration','official_research','validation','conflict','access_revoked',
 'permission','session','not_found'}
PROVIDER = {'authentication','rate_limit','provider_request_invalid','output_limit','unfinished_response',
 'empty_response','invalid_json','schema_validation','quality_repetition','source_auth','source_api_error',
 'source_http_error','source_invalid_response','wordpress_response','image_generation'}
ROOT = Path(__file__).resolve().parents[2]


def identifier(value):
    """Only short technical identifiers; never arbitrary provider/user prose."""
    return value if isinstance(value,str) and not value.startswith(('sk-','sk_')) and '://' not in value and len(value)<=160 and re.fullmatch(r'[A-Za-z0-9_.:/-]+',value) else None


@contextmanager
def boundary(component, operation):
    """Attach the innermost confirmed execution boundary, including direct API calls."""
    try:
        yield
    except Exception as exc:
        if not getattr(exc, '_failure_boundary', None):
            exc._failure_boundary = {'component': component, 'operation': operation}
        raise


def diagnostic_boundary(component, operation):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with boundary(component, operation):
                return function(*args, **kwargs)
        return invoke
    return decorate


def category(code):
    if code=='quality_failed':return 'content_corrections'
    if code=='uncertain_outcome':return 'uncertain_request'
    if code in MISSING:return 'missing_prerequisites'
    if code in PROVIDER:return 'provider_failure'
    if code=='quality_evidence':return 'invalid_review'
    return 'local_software_failure'


def _capture(storage, *, code, exc=None, job=None, state=None, component=None, operation=None,
            reference=None, http_status=None):
    """Persist an immutable event; failure of diagnostics must not mask the original error."""
    from src.services.user_errors import guidance, console_release_blockers, _CONSOLE_FINDINGS
    reference=reference or str(uuid4())
    context=dict(job.execution_context) if job else {}
    context.update(getattr(exc,'_failure_boundary',{}) if exc else {})
    component=context.get('component') or component or (job.current_step if job else None) or 'Workflow'
    operation=context.get('operation') or operation or 'Execute saved workflow step'
    # A failing quality gate is a content result, not an exception in the last model used.
    rule_codes=[]
    if code=='image_processing':
        component='Image Processing';operation='Process saved channel variant';context={}
    if code=='quality_failed' and state:
        for name,check in [('Brand Check',state.brand_check),('SEO QA',state.seo_check)]:
            if check and not check.passed:
                component=name;operation='Validate saved draft';context={}
                for finding in check.errors:
                    # Translate recognized rules only. Never echo arbitrary draft quotations.
                    if finding in _CONSOLE_FINDINGS:
                        rule_codes.append(console_release_blockers([finding])[0])
                break
    label=LABELS.get(component,component if component in {'Workflow','DataForSEO','Google Search Console',
        'Website Evidence','Official Source Research','Image Generation','Image Processing','Local Export',
        'Infographic Upload','Brand Check','SEO QA','Article Writing','Briefing','Topic Planning','SEO Strategy',
        'Social Drafts','Local Draft Editor','Editorial Review','Professional Review','Publication Record',
        'Enquiry Measurement','Portal API'} else 'Workflow')
    info=guidance(code,reference)
    reason='Unexpected software error — cause not yet confirmed.' if code=='processing' else info['message']
    qa_reasons={'incomplete_review_coverage':'The reviewer did not complete every assigned criterion. The draft is unchanged and QA remains pending.', 'minor_detail_misclassified':'The reviewer incorrectly made a minor editorial preference blocking. The draft was not rejected.', 'missing_material_evidence':'The reviewer supplied no applicable requirement and material supporting evidence. The review remains unaccepted.', 'missing_contradiction_pair':'The reviewer did not supply two valid conflicting passages. The draft remains unchanged.', 'unchanged_review_blocked':'Another identical paid attempt was blocked before submission. A verified contract or context repair is required.'}
    if code=='quality_evidence' and getattr(exc,'reason',None) in qa_reasons:reason=qa_reasons[exc.reason]
    if code=='quality_evidence' and getattr(exc,'reason',None)=='unknown_evidence_reference':
        reason='The AI review used an unsupported evidence reference. Only supplied source IDs are accepted; section names and policy paths are not source IDs. The review was rejected, and the saved draft is unchanged.'
    if code=='quality_evidence' and getattr(exc,'reason',None)=='release_input_misclassified':
        reason='The AI reviewer classified a declared pending release input as a content defect. The review was rejected; the placeholder and mandatory release approval remain unchanged.'
    if code=='quality_evidence' and getattr(exc,'reason',None)=='changed_link_evidence':
        reason='Saved link evidence no longer matches its recorded content hash. Restore or recollect the source before another review; no paid request was sent.'
    review_reasons={
        'approved_wording_rejected':'The reviewer rejected an exact configured approved statement or short form. This unsupported review was rejected; it did not pass QA or change the draft.',
        'review_scope_conflict':'The page reviewer returned a module-level judgment outside its assigned scope. The response was retained but cannot pass QA.',
        'deterministic_review_conflict':'The semantic reviewer attempted a link count or provenance judgment reserved for deterministic checks. The response cannot pass QA.',
        'missing_link_source_quote':'The link-relevance finding supplied no quotation from the saved target source. The unsupported review cannot pass QA.',
        'heading_only_link_source_quote':'The link-relevance finding cited only a saved title or heading. Supporting source body text is required; this review cannot pass QA.',
        'invalid_link_source_quote':'A link-relevance quotation does not match its cited saved target source. The unsupported review cannot pass QA.'}
    if code=='quality_evidence' and getattr(exc,'reason',None) in review_reasons:
        reason=review_reasons[exc.reason]
    if code=='processing' and isinstance(exc,OSError) and type(exc.errno) is int:
        reason=f'Local file or storage access failed with operating-system error {exc.errno}. Inspect storage permissions and available space.'
    call=None
    call_id=context.get('call_id')
    if job and call_id:
        # Never guess using the most recent call in the run.
        call=next((c for c in storage.list_ai_calls(str(job.run_id))
            if str(c.id)==call_id and c.job_id==str(job.id) and c.attempt_id==job.attempt_id),None)
    if code=='quality_failed' and rule_codes: reason=' '.join(rule_codes[:5])
    if code=='output_limit' and call and type(call.max_output_tokens) is int:
        reason=f'The provider ended the response with finish reason {identifier(call.finish_reason) or "unknown"}; the configured output limit was {call.max_output_tokens} tokens. The incomplete result was rejected.'
    paths=(getattr(exc,'validation_fields',[]) or (call.validation_fields if call else [])) or []
    paths=[p for p in paths if identifier(p)][:10]
    if code=='schema_validation' and paths:reason+=' Affected fields: '+', '.join(paths)+'.'
    if code=='preflight_blocked' and state:
        report=state.agent_outputs.get('preflight',{})
        allowed={'budget','next_request_budget','guide_inventory','official_evidence','model_configuration_briefing','model_configuration_content','model_configuration_seo_qa'}
        messages=[item['message'] for item in report.get('findings',[]) if item.get('code') in allowed and item.get('blocking')]
        if messages:reason=' '.join(messages[:5])
    if code=='research_budget':
        reason='The next request could not fit within the run or shared monthly allowance. It was not sent.'
    required=getattr(exc,'required',None);available=getattr(exc,'available',None)
    if code=='research_budget' and type(required) is int and type(available) is int:
        scope='monthly' if getattr(exc,'scope',None)=='monthly' else 'run'
        reason=f'The next request needs a ${required/1000000:.4f} reservation; the {scope} allowance has ${available/1000000:.4f} available. The request was not sent.'
    public={'reference':reference,'component':label,'operation':operation,
      'category':category(code),'reason':reason,
      'saved_work':'Earlier saved results remain available; this failed operation did not approve or publish content.',
      'next_action':info['next_steps'][0],'retry':info['retry'],
      'run_id':str(job.run_id) if job else str(state.run_id) if state else None,
      'job_id':str(job.id) if job else None,'attempt_id':job.attempt_id if job else None,
      'revision':state.revision if state else job.source_revision if job else None,
      'source_revision':job.source_revision if job else None,
      'unit_id':identifier(context.get('unit_id')),'asset_id':identifier(str(getattr(exc,'image_id','')) or context.get('asset_id')),
      'occurred_at':datetime.now(timezone.utc).isoformat(),'code':info['code']}
    route=getattr(exc,'route',None)
    provider=getattr(exc,'provider',None) or getattr(route,'provider',None)
    model=getattr(exc,'model',None) or getattr(route,'model',None)
    technical={'exception_type':identifier(type(exc).__name__) if exc else None,
      'provider':identifier(provider or context.get('provider')),'model':identifier(model or context.get('model')),
      'call_id':str(call.id) if call else None,'http_status':http_status,
      'stack_locations':[]}
    technical['error_code']=identifier(code)
    if code=='quality_evidence':
        technical['review_reason']=identifier(getattr(exc,'reason',None))
        technical['validation_fields']=paths
    if call:
        technical.update({k:getattr(call,k) for k in ['input_tokens','output_tokens','max_output_tokens','reasoning_tokens']})
        technical.update(provider=identifier(call.provider),model=identifier(call.model),
            finish_reason=identifier(call.finish_reason),validation_fields=paths,
            provider_request_id=identifier(call.provider_request_id))
    if exc:
        # HTTP status is a number, never an error body or URL.
        status=getattr(exc,'status_code',None)
        if status is None:status=getattr(getattr(exc,'response',None),'status_code',None)
        if type(status) is int and 100<=status<=599:
            technical['provider_http_status']=status
            if code in PROVIDER:
                public['reason']=f'{label} rejected the request with HTTP {status}. '+('Check credentials and access permissions.' if status in {401,403} else 'The provider refused the request because of a usage limit.' if status==429 else 'Inspect the saved request and provider status before retrying.')
        for frame in traceback.extract_tb(exc.__traceback__):
            try:relative=Path(frame.filename).resolve().relative_to(ROOT)
            except ValueError:continue
            if str(relative).startswith('src/'):
                technical['stack_locations'].append({'file':str(relative),'line':frame.lineno,'function':identifier(frame.name)})
        technical['stack_locations']=technical['stack_locations'][-12:]
    if state:
        try:technical['ledger']=storage.research_costs(str(state.run_id))
        except Exception:pass
    event={'id':reference,'run_id':public['run_id'],'job_id':public['job_id'],
           'attempt_id':public['attempt_id'],'occurred_at':public['occurred_at'],
           'summary':public,'technical':technical}
    try:storage.save_failure(event)
    except Exception:
        logging.getLogger(__name__).error('Failure diagnostics could not be saved; reference %s, code %s',reference,info['code'])
    try:
        from src.services.failure_checklist import record
        record(event,state)
    except Exception:
        # The checklist is documentation; it never changes the recorded failure.
        logging.getLogger(__name__).warning('Failure checklist not updated; reference %s',reference)
    if job:job.failure_summary=public
    return event


def error_info(summary):
    from src.services.user_errors import guidance
    info=guidance(summary['code'],summary['reference'])
    info['failure']=summary
    return info


def capture(storage, **kwargs):
    # Diagnostics are subordinate to the operation's original recovery behavior.
    reference=kwargs.setdefault('reference', str(uuid4()))
    try:return _capture(storage, **kwargs)
    except Exception:
        from src.services.user_errors import guidance
        info=guidance(kwargs.get('code'),reference)
        logging.getLogger(__name__).error('Failure diagnostic assembly failed; reference %s, code %s', reference, info['code'])
        summary={'reference':reference,'component':'Workflow','operation':'Inspect saved task',
            'category':category(info['code']),'reason':'Unexpected software error — cause not yet confirmed.',
            'saved_work':'Previously saved results remain available.', 'next_action':info['next_steps'][0],
            'retry':info['retry'],'code':info['code']}
        if kwargs.get('job'):kwargs['job'].failure_summary=summary
        return {'id':reference,'summary':summary,'technical':{'diagnostics_unavailable':True}}
