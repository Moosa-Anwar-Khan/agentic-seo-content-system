"""Revision-bound semantic review units, independent of a job's lifetime.

Every module and claim is covered. Original sources are split into ordered strings,
not clipped. Only complete, grounded units are cached; no partial response can pass.
"""
from src.services.link_policy import contract as link_contract
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator
from src.schemas.common import StrictModel
from src.schemas.content import CheckResult, ReviewFinding
from src.services.quality import QualityReviewError

POLICY = 'bounded-qa-5-explicit-link-and-reference-context'
# Parsing compatibility is separate from review policy: accepting complete prose
# must not invalidate unchanged, grounded review units or discard paid work.
RESPONSE_PARSER = 'qa-explanations-without-character-limits-v1'
CONTEXT_POLICY = 'complete-link-and-business-evidence-v1'
SCOPED_CONTRACT = 'separate-qa-contracts-v1'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split())


class QAFinding(ReviewFinding):
    quote: str = Field(min_length=3, max_length=200)
    message_en: str = Field(min_length=5)
    action_en: str = Field(min_length=5)
    evidence_refs: list[str] = Field(default_factory=list,max_length=8,description='Source IDs from allowed_evidence_refs only. For policy or missing-input findings use an empty list; section IDs and JSON paths are not source IDs.')
    rule: Literal['editorial', 'intent', 'contradiction', 'unsupported_business', 'tax_claim', 'links', 'link_count', 'link_provenance', 'required_module', 'pending_input'] = 'editorial'
    problem: str = Field(min_length=5)
    correction: str = Field(min_length=5)


class QAUnitResult(StrictModel):
    unit_id: str = Field(max_length=150)
    coverage_complete: Literal[True]
    passed: bool
    findings: list[QAFinding] = Field(max_length=6)
    warnings: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def consistent(self):
        blocking = any(f.severity == 'blocking' for f in self.findings)
        if self.passed == blocking:
            raise ValueError('A pass cannot contain findings; a failure needs findings')
        for finding in self.findings:
            expected = {'blocking':'content_defect', 'suggestion':'suggestion', 'release':'release_prerequisite'}[finding.severity]
            if finding.category != expected:
                raise ValueError('Finding category and severity disagree')
            if finding.rule == 'pending_input' and finding.severity != 'release':
                raise ValueError('Pending release inputs cannot fail draft quality')
        return self


class PageFinding(QAFinding):
    rule: Literal['editorial', 'intent', 'contradiction', 'required_module']


class ModuleFinding(QAFinding):
    rule: Literal['editorial', 'intent', 'contradiction', 'unsupported_business', 'tax_claim', 'links', 'required_module', 'pending_input']


class ClaimFinding(QAFinding):
    rule: Literal['tax_claim', 'contradiction']


class PageQAUnitResult(QAUnitResult):
    findings: list[PageFinding] = Field(max_length=6)


class ModuleQAUnitResult(QAUnitResult):
    findings: list[ModuleFinding] = Field(max_length=6)


class ClaimQAUnitResult(QAUnitResult):
    findings: list[ClaimFinding] = Field(max_length=6)


def schema_v1(kind):
    return {'page': PageQAUnitResult, 'module': ModuleQAUnitResult, 'claim': ClaimQAUnitResult}[kind]


def prompt_v1(kind):
    common=(
        'Review ONLY the supplied unit. Return JSON matching the supplied schema and exact unit_id. '
        'Inspect the entire supplied context before coverage_complete=true. Return at most six distinct findings; '
        'if that is insufficient for complete coverage, return coverage_complete=false, never omit defects to pass. '
        'Every finding needs a literal DRAFT quotation, allowed module_id, applicable claim_id, concise German problem/correction '
        'and specific English message_en/action_en. Use only supplied evidence IDs; policy findings use evidence_refs=[]. '
        'Do not duplicate findings. Minor style preferences are suggestions. Only genuine blocking defects make passed=false. '
        'Do not invent business facts or missing inputs. Human approvals remain separate. German text uses formal Sie. ')
    specific={
        'page': (
            'Your scope is page intent, coverage, metadata and cross-section consistency. Read ALL article_modules and metadata; '
            'do not infer absence from headings. Use company evidence and approved facts for consistency. '
            'Editorial findings in this unit may quote metadata only. Leave individual service assertions, tax-source accuracy '
            'and link relevance to their module/claim reviewers. Deterministic observations are established inputs, not tasks to repeat. '
            'Declared pending business inputs are allowed in an unapproved draft; the application tracks their release requirements. '
            'Never report a pending placeholder as missing content. Do not demand identical title/H1/description wording.'),
        'module': (
            'Your scope is this complete section, its purpose, relevant referenced sections, unsupported assertions and contextual link relevance. '
            'Identify substantive tax assertions absent from registered_claims; original-source legal accuracy belongs to claim review. '
            'Ordinary approved service descriptions need no tax claim. Configured approved wording and short forms are valid; '
            'quote the conflicting surrounding words when reporting a real contradiction. '
            'Deterministic checks own exact approved matches, unique destination counts and provenance; never re-count or fill quotas. '
            'Two links to the same destination remain one unique destination. L14 is landing guide links, L16 is social; '
            'apply the supplied page type and linking policy. '
            'For each links finding cite supporting saved target BODY text in source_quotes and its ID in evidence_refs. '
            'Read the entire saved target. A title/heading alone cannot support relevance rejection; partial evidence cannot establish absence. '
            'A conditional software-guide recommendation is not itself a promise that the firm provides that software service. '
            'Assess the actual reader need, anchor and conditional wording instead. '
            'A declared pending placeholder is a release prerequisite (pending_input/release), never a prose defect. '
            'A real section cross-reference is valid; saying no deadline is supplied is not a tax assertion.'),
        'claim': (
            'Your scope is the registered substantive tax claim against the entire original official_source and supplied draft context. '
            'Check precise meaning, conditions, exceptions, jurisdiction and year. A literal match does not prove applicability. '
            'Return tax_claim or contradiction findings only. Do not review service wording, link placement, page style or business release inputs.')}
    return common+specific[kind]+' This is automated QA, not professional approval.'


def repeated_findings(content):
    """Detect repeated complete finding objects even in a truncated JSON array."""
    import re
    start = re.search(r'"findings"\s*:\s*\[', content)
    if not start:
        return False
    at = start.end(); seen=set(); decoder=json.JSONDecoder()
    while at < len(content):
        while at < len(content) and content[at] in ', \r\n\t': at+=1
        try: item, at = decoder.raw_decode(content, at)
        except ValueError: break
        if not isinstance(item,dict): break
        identity = normalized(json.dumps(item,sort_keys=True,ensure_ascii=False))
        if identity in seen: return True
        seen.add(identity)
    return False


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


LEGACY_COMPATIBLE_SIGNATURES={kind:{'version':SCOPED_CONTRACT,
    'prompt_hash':digest(prompt_v1(kind)),'schema_hash':digest(schema_v1(kind).model_json_schema())}
    for kind in ('module','claim')}


def plan_v1(state, settings, *, legacy=False):
    from src.services.model_routes import resolve_route, text_request_options
    from src.services.review_contract import contract_v1 as contract, sections
    from src.services.qa_context import link_target, module_context
    from src.combined.editorial import rules, same_page
    effective = contract(state, settings)
    draft=state.editorial
    route=resolve_route(settings,'seo_qa')
    source_records=state.research_packet.get('official_sources',[])
    identity={'contract':effective,'policy':POLICY,'revision':state.revision,'draft':draft,
              'briefing':state.briefing.model_dump(mode='json'),'internal_link_policy':state.internal_link_policy,
              'sources':source_records,'link_evidence':[p.model_dump(mode='json') for p in (state.analysis.page_evidence if state.analysis else [])],
              'route':route.litellm_model,
              'options':text_request_options(settings,route,'QAUnitResult')}
    fingerprint=digest(identity)
    modules=draft['modules'];claims=draft.get('claims',[])
    pack=rules(settings,draft['page_type'])
    def target_context(page):
        return link_target(page,metadata_only=same_page(page.url,settings.wordpress_base_url+'/mandant-werden/'))
    overview={'page_type':draft['page_type'],'title':draft['title'],'meta_title':draft['meta_title'],
              'meta_description':draft['meta_description'],
              'headings':[{'id':m['id'],'headings':[line for line in m['markdown'].splitlines() if line.startswith('#')]} for m in modules],
              'primary_keyword':state.briefing.primary_keyword,
              'search_intent':state.briefing.search_intent}
    article_context=[{'id':m['id'],'ordered_text_parts':sections(m['markdown'])} for m in modules]
    units=[{'id':'page','kind':'page','payload':{**overview, 'article_modules':article_context},'module_ids':[m['id'] for m in modules]}]
    for module in modules:
        from src.combined.markdown import parse
        links={link.url.split('#')[0].split('?')[0] for link in parse('<!-- '+module['id']+' -->\n'+module['markdown']).links}
        units.append({'id':'module:'+module['id'],'kind':'module','module_ids':[module['id']],
          'payload':{'module':{'id':module['id'],'ordered_text_parts':sections(module['markdown'])},'page':overview,
                     'referenced_modules':[item for item in article_context if item['id'] != module['id'] and
                         (item['id'] in module['markdown'] or (item['id']=='L04a' and 'Kompass' in module['markdown']))],
                     'verified_link_targets':[target_context(p)
                        for p in (state.analysis.page_evidence if state.analysis else []) if p.url in links],
                     'registered_claims':[c for c in claims if c['text'] in module['markdown']]}})
    sources={s['id']:s for s in source_records}
    for claim in claims:
        source=sources.get(claim['source_id'])
        from src.combined.evidence import source_issue
        if not source or source_issue(source,settings.tax_year):
            raise QualityReviewError()
        related=[m for m in modules if claim['text'] in m['markdown']]
        units.append({'id':'claim:'+claim['id'],'kind':'claim','module_ids':[m['id'] for m in related],
          'payload':{'claim':claim,'modules':related,'tax_year':settings.tax_year,
                     'official_source':{**{k:v for k,v in source.items() if k!='text'},
                       'ordered_text_parts':[source['text'][at:at+10000] for at in range(0,len(source['text']),10000)]}}})
    # Page-level link judgments need the same verified inventory as module checks.
    from src.combined.markdown import parse
    from src.combined.editorial import link_errors
    inventory=[]
    pages={p.url:p for p in (state.analysis.page_evidence if state.analysis else [])}
    for module in modules:
        for link in parse('<!-- '+module['id']+' -->\n'+module['markdown']).links:
            url=link.url.split('#')[0].split('?')[0]
            target=pages.get(url)
            inventory.append({'module_id':module['id'],'url':url,'verified':bool(target),
                'evidence_id':target.id if target else None,'title':target.title if target else None})
    units[0]['payload']['link_inventory']=inventory
    units[0]['payload']['verified_link_targets']=[target_context(p)
        for p in pages.values() if any(link['url']==p.url for link in inventory)]
    units[0]['payload']['deterministic_link_errors']=link_errors(state,settings)
    for unit in units:
        unit['payload'].update(module_context(pack,modules,unit['module_ids']))
        # Change only the dependencies whose responsibilities/evidence changed;
        # unrelated completed units remain reusable across this repair.
        if (unit['kind']=='page' or unit['payload'].get('approved_brand_facts') or
                any('ordered_text_parts' in p for p in unit['payload'].get('verified_link_targets',[]))):
            from src.services.qa_audit import audit, VERSION
            unit['payload']['responsibility_policy']=VERSION
            unit['payload']['deterministic_audit']=audit(state,settings,unit['module_ids'])
            unit['payload']['semantic_responsibilities']=(
                'Page intent, coverage and cross-section consistency only. Module-level service wording and link judgments belong to module review.'
                if unit['kind']=='page' else
                'Contextual contradictions, unsupported assertions and audience relevance. Exact wording, approved short forms, link quantities and provenance are deterministic. For any links finding supply source_quotes from the saved target text supporting the reason; titles alone are insufficient.')
        if (unit['payload'].get('approved_brand_facts') or unit['payload'].get('draft_placeholders') or
            any('ordered_text_parts' in p for p in unit['payload'].get('verified_link_targets',[]))):
            unit['payload']['context_policy']=CONTEXT_POLICY
        unit['payload']['review_contract'] = effective
        unit['payload']['pending_release_inputs'] = draft.get('unresolved_questions', [])
        urls={p['url'] for p in effective['company_evidence']} | {p['url'] for p in unit['payload'].get('verified_link_targets',[])}
        if unit['kind']=='claim': urls.add(unit['payload']['official_source']['url'])
        evidence_ids={p['id'] for p in effective['company_evidence']} | {p['id'] for p in unit['payload'].get('verified_link_targets',[])}
        evidence_ids.update(f['id'] for f in unit['payload'].get('approved_brand_facts',[]))
        if unit['kind']=='claim': evidence_ids.add(unit['payload']['official_source']['id'])
        unit['request']={'allowed_evidence_refs':sorted(evidence_ids),'unit_id':unit['id'],'review_kind':unit['kind'],**unit['payload'],
                         'internal_link_policy':link_contract(state),'allowed_module_ids':unit['module_ids'],
                         'supplied_source_urls':sorted(urls)}
        # Hash exactly what the provider receives, including allowed evidence URLs.
        unit['dependency'] = digest({'policy':POLICY,'payload':unit['request'],'route':identity['route'],'options':identity['options']})
        if not legacy:
            # The old exact payload remains available only for explicit cache
            # compatibility checks, never as instructions for a new request.
            from copy import deepcopy
            from src.services.llm import SYSTEM
            unit['legacy_dependency']=unit['dependency']
            unit['legacy_request']=deepcopy(unit['request'])
            if unit['kind']=='page':
                unit['request'].pop('verified_link_targets',None)
                allowed={p['id'] for p in effective['company_evidence']}
                allowed.update(f['id'] for f in unit['request'].get('approved_brand_facts',[]))
                unit['request']['allowed_evidence_refs']=sorted(allowed)
                unit['request']['supplied_source_urls']=sorted(p['url'] for p in effective['company_evidence'])
                unit['request']['semantic_responsibilities']='Page intent, coverage, metadata and cross-section consistency only.'
            schema=schema_v1(unit['kind'])
            unit['prompt']=prompt_v1(unit['kind'])
            unit['request']['_qa_contract']={'version':SCOPED_CONTRACT,
                'prompt_hash':digest(unit['prompt']),'schema_hash':digest(schema.model_json_schema()),'system_hash':digest(SYSTEM)}
            options=text_request_options(settings,route,schema.__name__)
            unit['dependency']=digest({'policy':POLICY,'payload':unit['request'],'route':identity['route'],'options':options})
    # External configuration and exact unit context must also identify a manifest;
    # unchanged individual dependencies remain reusable across manifest/revision changes.
    return digest({'article':fingerprint,'unit_dependencies':[u['dependency'] for u in units]}),units


def manifest_key(state, fingerprint):
    return 'qa:'+str(state.run_id)+':'+fingerprint


def persist(storage, key, run_id, value):
    storage.begin_operation(key,str(run_id))
    storage.finish_operation(key,value)


def validate_v1(state, unit, result):
    """Apply the same grounding checks to fresh, cached and recovered results."""
    if result.unit_id != unit['id'] or not result.coverage_complete:
        raise QualityReviewError()
    texts={m['id']:m['markdown'] for m in state.editorial['modules']}
    if unit['kind']=='page':
        texts={k:state.editorial['title']+' '+state.editorial['meta_title']+' '+state.editorial['meta_description']+' '+v for k,v in texts.items()}
    identities=set()
    for finding in result.findings:
        if (unit['kind']=='page' and unit['request'].get('responsibility_policy') and
                finding.rule not in {'editorial','intent','contradiction','required_module'}):
            raise QualityReviewError('review_scope_conflict')
        if (finding.module_id not in unit['module_ids'] or not normalized(finding.quote) or
                normalized(finding.quote) not in normalized(texts[finding.module_id])):
            raise QualityReviewError()
        if finding.claim_id and finding.claim_id not in {c['id'] for c in state.editorial.get('claims',[])}:
            raise QualityReviewError()
        if finding.severity=='blocking' and finding.rule in {'intent','required_module','pending_input'}:
            for placeholder in unit['request'].get('draft_placeholders',[]):
                if (finding.module_id==placeholder['module_id'] and
                    normalized(finding.quote) in normalized(placeholder['text'])):
                    raise QualityReviewError('release_input_misclassified')
        allowed_refs=set(unit['request']['allowed_evidence_refs'])
        if any(ref not in allowed_refs for ref in finding.evidence_refs):
            raise QualityReviewError('unknown_evidence_reference')
        if unit['request'].get('responsibility_policy'):
            from src.services.qa_audit import exact_form
            if unit['kind']=='page' and finding.rule in {'unsupported_business','tax_claim','links','link_count','link_provenance'}:
                raise QualityReviewError('review_scope_conflict')
            if finding.rule in {'link_count','link_provenance'}:
                raise QualityReviewError('deterministic_review_conflict')
            if finding.severity=='blocking' and finding.rule=='unsupported_business':
                matches=unit['request']['deterministic_audit']['approved_matches']
                if any(m['module_id']==finding.module_id and exact_form(finding.quote)==exact_form(m['quote']) for m in matches):
                    raise QualityReviewError('approved_wording_rejected')
            if finding.rule=='links':
                if not finding.source_quotes:
                    raise QualityReviewError('missing_link_source_quote')
                targets={p['id']:p for p in unit['request'].get('verified_link_targets',[])}
                for quote in finding.source_quotes:
                    target=targets.get(quote.evidence_ref)
                    if (not target or not normalized(quote.quote) or quote.evidence_ref not in finding.evidence_refs or
                        normalized(quote.quote) not in normalized(''.join(target.get('ordered_text_parts',[])))):
                        raise QualityReviewError('invalid_link_source_quote')
                    headings=[target.get('title',''),*[h['text'] for h in target.get('headings',[])]]
                    if normalized(quote.quote).casefold().strip(' .:') in {
                            normalized(h).casefold().strip(' .:') for h in headings if h}:
                        raise QualityReviewError('heading_only_link_source_quote')
        if unit['request'].get('_qa_contract'):
            from typing import get_args
            finding_type={'page':PageFinding,'module':ModuleFinding,'claim':ClaimFinding}[unit['kind']]
            if finding.rule not in get_args(finding_type.model_fields['rule'].annotation):
                raise QualityReviewError('review_scope_conflict')
            if unit['kind']=='page' and finding.rule=='editorial':
                metadata=' '.join(state.editorial[k] for k in ('title','meta_title','meta_description'))
                if normalized(finding.quote) not in normalized(metadata):
                    raise QualityReviewError('review_scope_conflict')
        identity=digest(finding.model_dump())
        if identity in identities:
            raise QualityReviewError()
        identities.add(identity)
    return result


def cached_v1(storage, state, unit):
    """Read-only compatibility projection. Never accept a failed paid operation.

    A module's complete old context must be identical, and its accepted result
    must satisfy the current schema and grounding rules. Page scope changed.
    """
    from pydantic import ValidationError
    key='qa-unit:'+str(state.run_id)+':'+unit['dependency']
    entry=storage.get_operation(key)
    if entry and entry['status']=='completed':
        validate_v1(state,unit,schema_v1(unit['kind']).model_validate(entry['value']['result']))
        return entry
    if unit['kind']=='page' or not unit.get('legacy_dependency'):
        return None
    expected=LEGACY_COMPATIBLE_SIGNATURES.get(unit['kind'])
    actual=unit['request'].get('_qa_contract',{})
    if not expected or any(actual.get(k)!=v for k,v in expected.items()):return None
    request={k:v for k,v in unit['request'].items() if k!='_qa_contract'}
    if request!=unit['legacy_request']:
        return None
    source='qa-unit:'+str(state.run_id)+':'+unit['legacy_dependency']
    old=storage.get_operation(source)
    if not old or old['status']!='completed':return None
    try:
        result=schema_v1(unit['kind']).model_validate(old['value']['result'])
        validate_v1(state,unit,result)
    except (ValidationError,QualityReviewError,KeyError):return None
    return {**old,'value':{**old['value'],'compatibility':{
        'policy':SCOPED_CONTRACT,'source_operation':source,'source_dependency':unit['legacy_dependency'],
        'validation':'identical module context/options; current schema and grounding passed'}}}


def retain_compatible_units(storage, state, settings):
    """Explicit offline migration. Add provenance; leave originals and state intact."""
    fingerprint,units=plan(state,settings)
    records=[]
    for unit in units:
        entry=cached_unit(storage,state,unit)
        if not entry:continue
        value=entry['value']
        if value.get('compatibility'):
            key='qa-unit:'+str(state.run_id)+':'+unit['dependency']
            if not storage.get_operation(key):
                persist(storage,key,state.run_id,{**value,'dependency':unit['dependency']})
        records.append({'unit_id':unit['id'],'dependency':unit['dependency'],
            'reviewed_revision':value.get('reviewed_revision'),'reused':True,
            'compatibility':value.get('compatibility')})
    # Only review() may produce a complete manifest and aggregate a QA result.
    manifest={'policy':POLICY,'fingerprint':fingerprint,'revision':state.revision,
        'total':len(units),'completed':len(records),'complete':False,'unit_ids':[u['id'] for u in units],
        'unit_records':records,'current_unit':None,'updated_at':datetime.now(timezone.utc).isoformat()}
    if not storage.get_operation(manifest_key(state,fingerprint)):
        persist(storage,manifest_key(state,fingerprint),state.run_id,manifest)
    return manifest


def review(context, state):
    fingerprint,units=plan(state,context.settings)
    root=manifest_key(state,fingerprint)
    storage=context.storage
    saved=(storage.get_operation(root) or {}).get('value') or {}
    manifest={'policy':POLICY,'fingerprint':fingerprint,'revision':state.revision,
              'total':len(units),'completed':saved.get('completed',0),'unit_ids':[u['id'] for u in units],
              'complete':False,'unit_records':[],'current_unit':None,'updated_at':datetime.now(timezone.utc).isoformat()}
    results=[]
    # Validate ALL unit requests before paying for the first; no partial spending on a known oversized plan.
    estimates = estimate_units(context.settings, units)
    from src.services.guided_qa import token_readiness
    token_status=token_readiness(storage,state,context.settings,units,estimates)
    if not token_status['ready']:
        from src.storage.sqlite_storage import BudgetExceeded
        raise BudgetExceeded('Run token ceiling cannot fit a pending review request; no request was sent')
    manifest['estimate'] = estimates
    for unit in units:
        from src.services.recovery import current_job
        active=current_job.get()
        if active:
            active.authorize()
            active.details(component='seo_qa', operation='Review article unit', unit_id=unit['id'], call_id=None)
        key='qa-unit:'+str(state.run_id)+':'+unit['dependency']
        previous=cached_unit(storage,state,unit)
        manifest.update(current_unit=unit['id'],completed=len(results),updated_at=datetime.now(timezone.utc).isoformat())
        persist(storage,root,state.run_id,manifest)
        if previous and previous['status']=='completed':
            result=schema_for(unit['kind']).model_validate(previous['value']['result'])
        else:
            from src.services.guided_qa import guard_key
            if storage.get_operation(guard_key(state,unit)):
                raise QualityReviewError('unchanged_review_blocked')
            try:
                result=context.llm.generate(str(state.run_id),schema_for(unit['kind']),unit['prompt'],unit['request'],model_role='seo_qa')
                validate_unit(state,unit,result)
            except Exception as exc:
                if isinstance(exc,QualityReviewError) or getattr(exc,'code',None) in {'invalid_json','schema_validation','quality_repetition','output_limit','unfinished_response','empty_response'}:
                    persist(storage,guard_key(state,unit),state.run_id,{'unit_id':unit['id'],'revision':state.revision,'dependency':unit['dependency'],'reason':getattr(exc,'reason',None) or getattr(exc,'code','quality_evidence')})
                raise
        validate_unit(state,unit,result)
        if not storage.get_operation(key):
            persist(storage,key,state.run_id,{'result':result.model_dump(mode='json'),
                'reviewed_revision':previous['value'].get('reviewed_revision',state.revision) if previous else state.revision,
                'dependency':unit['dependency'],**({'compatibility':previous['value']['compatibility']} if previous and previous['value'].get('compatibility') else {})})
        manifest['unit_records'].append({'unit_id':unit['id'],'dependency':unit['dependency'],
            'reviewed_revision':previous['value'].get('reviewed_revision') if previous and previous['status']=='completed' else state.revision,
            'reused':bool(previous and previous['status']=='completed'),
            'compatibility':previous['value'].get('compatibility') if previous else None})
        results.append(result)
        manifest.update(completed=len(results),updated_at=datetime.now(timezone.utc).isoformat())
        persist(storage,root,state.run_id,manifest)
    if active:active.details(unit_id=None, call_id=None)
    manifest.update(complete=True,current_unit=None)
    persist(storage,root,state.run_id,manifest)
    state.qa_manifest=manifest
    errors=[];warnings=[];structured=[];seen=set()
    def narrative(value):
        return value.translate(str.maketrans({'"':"'",'„':"'",'“':"'",'”':"'"}))
    for result in results:
        warnings.extend(result.warnings)
        for item in result.findings:
            signature=(item.module_id,item.rule,normalized(item.quote),item.severity)
            if signature in seen: continue
            seen.add(signature)
            structured.append(ReviewFinding.model_validate({k:v for k,v in item.model_dump().items() if k in ReviewFinding.model_fields}))
            target=errors if item.severity=='blocking' else warnings
            target.append(f'{item.module_id}'+('/'+item.claim_id if item.claim_id else '')+
                          ': „'+item.quote+'“ — '+narrative(item.problem)+' '+narrative(item.correction))
    return CheckResult(passed=not errors,errors=list(dict.fromkeys(errors)),warnings=list(dict.fromkeys(warnings)), findings=structured)


def estimate_units(settings, units):
    """Offline request bounds and reusable-unit costs; no credentials or network calls."""
    from src.services.model_routes import resolve_route, text_request_options, validate_text_route
    from src.services.research_budget import text_upper_price, micros
    from src.services.llm import request_messages
    route=resolve_route(settings,'seo_qa')
    result=[]
    for unit in units:
        payload=unit['request']
        schema=schema_for(unit['kind']) if unit.get('prompt') else QAUnitResult
        options=text_request_options(settings,route,schema.__name__)
        # Build and validate once; these same messages supply the token bound.
        messages=request_messages(settings,schema,unit.get('prompt') or prompt_for(unit['kind']),payload)
        # Includes ample allowance for prompt/schema. ModelRouter repeats the exact request check.
        input_bound=len(json.dumps(payload,ensure_ascii=False).encode())+len(json.dumps(schema.model_json_schema()).encode())+16000
        validate_text_route(settings,route,input_bound,options['max_tokens'])
        result.append({'unit_id':unit['id'],'dependency':unit['dependency'],
                       'maximum_token_reservation':len(json.dumps(messages,ensure_ascii=False).encode())+1024+options['max_tokens'],
                       'maximum_reservation_microusd':micros(text_upper_price(settings,route,input_bound,options['max_tokens']))})
    return result


def schema_for(kind):
    from src.services.guided_qa import schema_for as current
    return current(kind)


def prompt_for(kind):
    from src.services.guided_qa import prompt_for as current
    return current(kind)


def plan(state,settings,*,legacy=False):
    fingerprint,units=plan_v1(state,settings,legacy=legacy)
    if legacy:return fingerprint,units
    from src.services.guided_qa import enrich
    current=enrich(state,settings,units)
    return digest({'base':fingerprint,'units':[u['dependency'] for u in current]}),current


def validate_unit(state,unit,result):
    validate_v1(state,unit,result)
    from src.services.guided_qa import validate
    return validate(state,unit,result)


def cached_unit(storage,state,unit):
    entry=storage.get_operation('qa-unit:'+str(state.run_id)+':'+unit['dependency'])
    if entry and entry['status']=='completed':
        validate_unit(state,unit,schema_for(unit['kind']).model_validate(entry['value']['result']))
        return entry
    if unit['kind']=='page' or not unit.get('v1_unit'):return None
    from src.services.guided_qa import prompt_for as verified_prompt,QA_SYSTEM
    actual=unit['request'].get('_qa_contract',{})
    if (actual.get('prompt_hash')!=digest(verified_prompt(unit['kind'])) or actual.get('schema_hash')!=digest(schema_for(unit['kind']).model_json_schema()) or actual.get('system_hash')!=digest(QA_SYSTEM)):return None
    old=cached_v1(storage,state,unit['v1_unit'])
    if not old:return None
    raw=old['value']['result']
    # Only previously accepted clean units map to the same semantic obligations.
    # Findings need the new typed evidence; their earlier records remain untouched.
    if any(f.get('severity','blocking')=='blocking' for f in raw.get('findings',[])):return None
    from src.services.guided_qa import CRITERIA,VERSION
    projected=[{**f,**({'rule':'pending_input'} if f.get('severity')=='release' else {})} for f in raw.get('findings',[])]
    mapped={**raw,'findings':projected,'assessments':[{'criterion':c,'complete':True} for c in CRITERIA[unit['kind']]]}
    validate_unit(state,unit,schema_for(unit['kind']).model_validate(mapped))
    return {**old,'value':{**old['value'],'result':mapped,'compatibility':{
        'policy':VERSION,'source_operation':old['value'].get('compatibility',{}).get('source_operation','qa-unit:'+str(state.run_id)+':'+unit['v1_unit']['dependency']),
        'validation':'Accepted clean v1 module/claim; identical saved content/evidence/model; same semantic obligations; typed grounding revalidated.'}}}
