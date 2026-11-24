from src.services.failures import diagnostic_boundary
"""Bounded API adapters; discovery results never become legal evidence themselves."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from src.combined.evidence import OFFICIAL, allowed_official
from src.services.recovery import (UncertainOperation, begin_paid, decrypt_response,
                                   encrypt_response)
from src.services.research_budget import micros


class ResearchProviderError(ValueError):
    """Sanitized research failure, without provider bodies or credentials."""


@diagnostic_boundary('Official Source Research', 'Retrieve official discovery or extraction response')
def paid_json(settings, storage, run_id, provider, payload, transport=None):
    endpoints = {'tavily': ('https://api.tavily.com/search', settings.tavily_api_key,
                            settings.tavily_request_max_usd),
                 'firecrawl': ('https://api.firecrawl.dev/v2/scrape', settings.firecrawl_api_key,
                               settings.firecrawl_request_max_usd)}
    endpoint, secret, allowance = endpoints[provider]
    if settings.demo_mode:
        raise ResearchProviderError('Paid research is disabled in demo mode')
    if not secret.get_secret_value() or allowance is None:
        raise ResearchProviderError(f'{provider}: API key and verified request allowance required')
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    key = f'{run_id}:official-{provider}:{fingerprint}'
    prior = storage.get_operation(key)
    if prior and prior['status'] == 'pending':
        raise UncertainOperation('Research request outcome is unknown; inspect provider before retrying')
    saved = (prior or {}).get('value') or {}
    if saved.get('encrypted_content'):
        age = datetime.now(timezone.utc) - datetime.fromisoformat(saved['retrieved_at'])
        if not timedelta(0) <= age <= timedelta(hours=settings.research_cache_hours):
            raise ResearchProviderError('Discovery expired; start a fresh research run')
        raw = decrypt_response(settings, saved['encrypted_content'])
    else:
        if prior:
            raise ResearchProviderError('Research request already failed; inspect before creating a new run')
        encrypt_response(settings, '')
        reservation = storage.reserve_research_cost(run_id, micros(allowance), micros(settings.max_run_usd))
        begin_paid(key, storage, run_id)
        try:
            with httpx.Client(transport=transport, timeout=60, follow_redirects=False) as client:
                with client.stream('POST', endpoint, json=payload,
                                   headers={'Authorization': 'Bearer ' + secret.get_secret_value()}) as response:
                    response.raise_for_status()
                    if response.is_redirect:
                        raise ResearchProviderError('Research endpoint redirect rejected')
                    chunks = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ResearchProviderError('Research response exceeds the size limit')
                        chunks.append(chunk)
                    raw = b''.join(chunks).decode('utf-8')
            saved = {'encrypted_content': encrypt_response(settings, raw),
                     'retrieved_at': datetime.now(timezone.utc).isoformat(),
                     'cost_reservation': reservation}
            storage.save_operation_response(key, saved)
        except (httpx.TimeoutException, httpx.NetworkError):
            raise UncertainOperation('Research request outcome is unknown') from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise UncertainOperation('Research request outcome is unknown') from None
            storage.fail_operation(key)
            if exc.response.status_code in {400, 401, 403, 429}:
                storage.settle_research_cost(reservation, 0)
            raise ResearchProviderError(f'{provider}: request rejected; check account and configuration') from None
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise ResearchProviderError(f'{provider}: invalid JSON response; saved response retained') from None
    storage.finish_operation(key, saved)
    # Credit-based responses do not establish a dollar charge. Keep the upper
    # reservation counted rather than guessing currency usage or refunding it.
    return body


def discover(settings, storage, run_id, keyword, transport=None):
    query = f'{keyword[:200]} Steuerrecht Deutschland Steuerjahr {settings.tax_year}'
    payload = {'query': query, 'search_depth': 'basic', 'topic': 'general',
               'max_results': settings.official_discovery_results,
               'include_domains': sorted(OFFICIAL), 'include_domains_mode': 'restrict',
               'include_answer': False, 'include_raw_content': False, 'include_usage': True}
    body = paid_json(settings, storage, run_id, 'tavily', payload, transport)
    if not isinstance(body.get('results'), list):
        raise ResearchProviderError('Tavily did not return a source list')
    accepted = []
    seen = set()
    for item in body['results']:
        if not isinstance(item, dict):
            continue
        url = item.get('url')
        if not isinstance(url, str) or not allowed_official(url) or url in seen:
            continue
        seen.add(url)
        host = urlsplit(url).hostname or ''
        kind = ('judgment' if host.endswith('bundesfinanzhof.de') else
                'statute' if host.endswith(('gesetze-im-internet.de', 'recht.bund.de')) else 'administrative')
        accepted.append({'url': url, 'kind': kind})
        if len(accepted) == settings.official_discovery_results:
            break
    if not accepted:
        raise ResearchProviderError('Discovery found no approved official URLs; do not draft unsupported claims')
    return accepted, {'query': query, 'provider': 'tavily', 'candidate_urls': [x['url'] for x in accepted],
                      'retrieved_at': datetime.now(timezone.utc).isoformat(),
                      'note': 'Discovery only; original source texts and professional applicability review required.'}


def extract_fallback(settings, storage, run_id, url, transport=None):
    if not settings.firecrawl_enabled or not allowed_official(url):
        raise ResearchProviderError('Extraction fallback is disabled or the URL is not official')
    body = paid_json(settings, storage, run_id, 'firecrawl',
                     {'url': url, 'formats': ['markdown'], 'onlyMainContent': True,
                      'skipTlsVerification': False}, transport)
    data = body.get('data') or {}
    final = (data.get('metadata') or {}).get('sourceURL')
    text = data.get('markdown')
    if (body.get('success') is not True or not isinstance(final, str) or
            not allowed_official(final) or urlsplit(final).hostname != urlsplit(url).hostname or
            not isinstance(text, str) or not text.strip() or len(text) > 60000):
        raise ResearchProviderError('Firecrawl returned incomplete text or unapproved provenance')
    return text


def extract_website_fallback(settings, storage, run_id, url, transport=None):
    """Called only after a permitted direct fetch; never bypasses access failures."""
    from src.integrations.website_scan import WebsiteScan
    if not settings.firecrawl_enabled:
        raise ResearchProviderError('Website extraction is disabled')
    WebsiteScan(settings).validate_url(url)
    body=paid_json(settings,storage,run_id,'firecrawl',
        {'url':url,'formats':['html'],'onlyMainContent':True,'skipTlsVerification':False},transport)
    data=body.get('data') or {}
    final=(data.get('metadata') or {}).get('sourceURL')
    html=data.get('html')
    if (body.get('success') is not True or not isinstance(final,str) or
            final.rstrip('/')!=url.rstrip('/') or not isinstance(html,str) or
            not html.strip() or len(html.encode())>2_000_000):
        raise ResearchProviderError('Website extraction provenance or content is invalid')
    return html
