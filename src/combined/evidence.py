"""Bounded official-source retrieval and literal evidence checks.

Retrieval establishes provenance, not legal applicability. The human release gate
also requires a full factual review. Public evidence only; never client records.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from src.combined.fetch import Fetcher, FetchPolicy
from src.combined.models import SourceEvidence

OFFICIAL = {'gesetze-im-internet.de','bundesfinanzministerium.de','bundesfinanzhof.de','recht.bund.de','elster.de'}

def allowed_official(url):
    try:
        p=urlsplit(url)
        host=(p.hostname or '').casefold()
        community=any(part in host.split('.') for part in ('forum','community')) or bool(re.search(r'/(?:anwenderforum|forum|community)(?:/|$)',p.path,re.I))
        return not community and p.scheme=='https' and not p.username and not p.password and p.port in {None,443} and any(host==d or host.endswith('.'+d) for d in OFFICIAL)
    except ValueError:
        return False


def source_issue(source,tax_year):
    if source.get('kind') in {'competitor','example'}:return 'not an official legal source'
    if not allowed_official(source.get('url','')):return 'not an eligible official authority'
    if source.get('truncated') or source.get('status','read')!='read':return 'incomplete source text'
    text=source.get('text','')
    if not text or hashlib.sha256(text.encode()).hexdigest()!=source.get('content_hash'):return 'missing text or evidence hash mismatch'
    # Only explicitly year-specific document URLs establish this mismatch.
    # A year mentioned inside a statute is not evidence that it is inapplicable.
    years=re.findall(r'(?<!\d)(20\d{2})(?!\d)',source.get('url',''))
    if years and str(tax_year) not in years:return 'document URL identifies a different tax year'
    return None

def retrieve(settings, urls=None, fetcher=None, storage=None, run_id=None):
    items=urls if urls is not None else json.loads((settings.input_dir/'official-sources.json').read_text())['sources']
    own=fetcher is None;fetcher=fetcher or Fetcher();sources=[];errors=[]
    cache=settings.data_dir/'evidence';cache.mkdir(parents=True,exist_ok=True)
    try:
        for item in items[:12]:
            url=item['url']
            if not allowed_official(url):
                errors.append('Quelle nicht als amtlicher Rechtsbeleg freigegeben: '+url);continue
            key=hashlib.sha256(url.encode()).hexdigest();path=cache/(key+'.json')
            source=None
            if path.exists():
                try:
                    cached=SourceEvidence.model_validate_json(path.read_text())
                    if cached.url==url and cached.content_hash==hashlib.sha256(cached.text.encode()).hexdigest() and timedelta(0)<=datetime.now(timezone.utc)-cached.retrieved_at<timedelta(hours=settings.research_cache_hours):source=cached
                except (ValueError,KeyError):pass
            if source is None:
                result=fetcher.fetch(url,FetchPolicy(allowed_domain=urlsplit(url).hostname))
                text=result.text
                extraction_method = 'direct'
                if not text and result.body and result.status.value=='pdf':
                    from io import BytesIO
                    from pypdf import PdfReader
                    reader = PdfReader(BytesIO(result.body))
                    if len(reader.pages) <= 50:
                        text='\n'.join(p.extract_text() or '' for p in reader.pages)
                if (not text and settings.firecrawl_enabled and storage is not None and run_id and
                        result.status.value in {'js_shell','non_html'} and not result.truncated and
                        result.http_status == 200):
                    from src.integrations.official_research import extract_fallback
                    text = extract_fallback(settings, storage, run_id, url)
                    extraction_method = 'firecrawl'
                if not text or result.truncated:
                    errors.append('Quelle nicht vollständig abrufbar: '+url);continue
                source=SourceEvidence(id='official-'+key[:16],url=url,kind=item['kind'],text=text[:60000],content_hash=hashlib.sha256(text[:60000].encode()).hexdigest(),truncated=len(text)>60000,
                                      extraction_method=extraction_method)
                tmp=path.with_suffix('.tmp');tmp.write_text(source.model_dump_json());tmp.replace(path)
            sources.append(source.model_dump(mode='json'))
    finally:
        if own:fetcher.close()
    return sources,errors

def claim_errors(draft,sources,tax_year):
    known={s['id']:SourceEvidence.model_validate(s) for s in sources};errors=[]
    ids=set()
    body=' '.join(m.markdown for m in draft.modules)
    for claim in draft.claims:
        source=known.get(claim.source_id)
        if claim.id in ids:errors.append('Doppelte Behauptungs-ID: '+claim.id)
        ids.add(claim.id)
        if not source or source.kind in {'competitor','example'} or not allowed_official(source.url):
            errors.append('Amtlicher Beleg fehlt: '+claim.id);continue
        if source_issue(source.model_dump(),tax_year) or claim.quote not in source.text:
            errors.append('Wörtlicher, vollständiger Quellenbeleg fehlt: '+claim.id)
        if claim.tax_year!=tax_year:errors.append('Steuerjahr stimmt nicht überein: '+claim.id)
        if claim.text not in body:errors.append('Behauptung gehört nicht zum Entwurf: '+claim.id)
    return errors


def normalize_source_quotes(draft, sources):
    """Restore source typography only when the same words match uniquely.

    Whitespace may differ after extraction. Numbers, words, punctuation, source
    identity and legal conditions are never inferred or changed by this helper.
    """
    known = {source['id']: source for source in sources}
    changes = []
    for claim in draft.claims:
        source = known.get(claim.source_id)
        if not source or claim.quote in source['text']:
            continue
        parts = claim.quote.split()
        if not parts:
            continue
        matches = list(re.finditer(r'\s+'.join(re.escape(part) for part in parts), source['text']))
        if len(matches) != 1:
            continue
        previous = claim.quote
        claim.quote = matches[0].group()
        changes.append({'claim_id': claim.id, 'source_id': claim.source_id,
                        'previous_sha256': hashlib.sha256(previous.encode()).hexdigest(),
                        'quote_sha256': hashlib.sha256(claim.quote.encode()).hexdigest(),
                        'reason': 'source_whitespace_restored'})
    return changes
