"""Read-only public website inventory; full HTML is parsed before excerpts."""
import hashlib
import ipaddress
import socket
import time
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from urllib.parse import urlsplit,urljoin,urldefrag
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
from src.security.sanitize import sanitize_text
from src.integrations.wordpress import decode_public_collection


class ExternalRedirect(ValueError):
    """An observed redirect is an outcome, never permission to crawl another firm."""
    def __init__(self, destination, status):
        parsed=urlsplit(destination)
        self.destination=(parsed._replace(netloc=parsed.hostname or '',query='',fragment='').geturl()
                          if parsed.scheme in {'https','http'} and parsed.hostname else 'Unsichere Weiterleitung')
        self.status=status
        super().__init__('Redirect leaves the configured website')

def extract_page(html,url):
    soup=BeautifulSoup(html,'html.parser')
    from src.services.visibility_audit import html_signals
    visibility_signals=html_signals(soup,url)
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    meta=soup.find('meta',attrs={'name':'description'})
    robots=soup.find('meta',attrs={'name':'robots'})
    canonical=soup.find('link',rel='canonical')
    description=meta.get('content','') if meta else None
    robots_value=robots.get('content','') if robots else ''
    canonical_value=canonical.get('href','') if canonical else None
    for node in soup(['script','style','nav','header','footer','form','iframe','object','noscript']): node.decompose()
    for node in soup.select('.elementor-location-header,.elementor-location-footer,.elementor-widget-nav-menu,.site-header,.site-footer'):
        node.decompose()
    candidates=[n for n in soup.select('[data-elementor-type],main,article') if n.find(['h1','h2'])]
    content=max(candidates,key=lambda n:len(n.get_text(' ',strip=True))) if candidates else soup
    sections=[]; exclusions=0; seen=set();headings=[]
    blocks=content.find_all(['h1','h2','h3','h4','h5','h6','p','li','td','th','dt','dd','summary','blockquote'])
    # Elementor accordion questions and text-only widgets are not always paragraphs.
    for node in content.select('.elementor-tab-title,.elementor-accordion-title,.elementor-toggle-title,.e-n-accordion-item-title-text,.elementor-heading-title,div'):
        if not node.find(['h1','h2','h3','h4','h5','h6','p','li','td','th','dt','dd','summary','blockquote','div']) and node not in blocks:
            blocks.append(node)
    order={id(node):i for i,node in enumerate(content.descendants)}
    for node in sorted(blocks,key=lambda n:order.get(id(n),0)):
        if node.name=='p' and node.find_parent('li'):continue
        value=node.get_text(' ',strip=True)
        if not value or value in seen:continue
        seen.add(value)
        for start in range(0,len(value),3500):
            try:text=sanitize_text(value[start:start+3500],3500)
            except ValueError:exclusions+=1;continue
            if node.name in {'h1','h2','h3','h4','h5','h6'}:
                headings.append({'level':int(node.name[1]),'text':text[:500]})
            sections.append({'id':f'section-{len(sections)}','kind':node.name,'text':text})
    plain=' '.join(s['text'] for s in sections)
    substantive=any(s['kind'] not in {'h1','h2','h3','h4','h5','h6'} and len(s['text'])>=20 for s in sections)
    if not sections:
        try:plain=sanitize_text(content.get_text(' ',strip=True)[:12000],12000)
        except ValueError:plain='';exclusions+=1
    links=[];invalid_links=0
    for anchor in content.find_all('a',href=True):
        try:
            destination=urldefrag(urljoin(url,anchor['href']))[0]
            parsed=urlsplit(destination)
            if parsed.scheme=='https' and parsed.hostname==urlsplit(url).hostname and not parsed.username and not parsed.password:
                # Keep only safe public link values for downstream model context.
                sanitize_text(destination,2000)
                if destination not in links:links.append(destination)
        except (ValueError,TypeError):invalid_links+=1
    def safe(value,limit):
        try:return sanitize_text(value,limit)
        except ValueError:return ''
    return {'url':url,'title':safe(title,500),'meta_title':safe(title,500),
        'meta_description':safe(description,1000) if description is not None else None,
        'canonical':urljoin(url,canonical_value) if canonical_value is not None else None,
        'indexable':not {'noindex','none'}.intersection(robots_value.casefold().replace(',',' ').split()),
        'headings':headings,'sections':sections,'excerpt':plain[:12000],
        'visibility_signals':visibility_signals,
        'content_truncated':bool(exclusions) or not plain or not headings or not substantive,
        'links_complete':not invalid_links,
        'content_exclusions':([{'reason':'privacy_or_untrusted_text','count':exclusions}] if exclusions else [])+
            ([{'reason':'no_substantive_text','count':1}] if not substantive else [])+
            ([{'reason':'malformed_or_unsafe_link','count':invalid_links}] if invalid_links else []),
        'exclusions':exclusions,'internal_links':links,
        'content_hash':hashlib.sha256(plain.encode()).hexdigest(),
        'retrieved_at':datetime.now(timezone.utc).isoformat()}

def remove_repeated_templates(pages):
    """Keep original sections as evidence, but omit shared boilerplate from excerpts."""
    from collections import Counter
    counts=Counter(s['text'] for p in pages for s in p['sections'] if s.get('kind') in {'p','li'})
    threshold=max(3,len(pages)//3);removed=0
    for page in pages:
        for section in page['sections']:
            section['template']=section.get('kind') in {'p','li'} and counts[section['text']]>=threshold
            removed+=bool(section['template'])
        page['excerpt']=' '.join(s['text'] for s in page['sections'] if not s.get('template'))[:12000]
    return removed

class WebsiteScan:
    def __init__(self,settings,storage=None,run_id=None):
        self.base=settings.wordpress_base_url.rstrip('/');self.host=urlsplit(self.base).hostname
        self.settings=settings;self.last_request=0;self.failures=[];self.excluded=[]
        self.outcomes=[];self.discovery={}
        self.storage,self.run_id=storage,run_id

    def validate_url(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!=self.host or parsed.port not in {None,443} or parsed.username or parsed.password:
            raise ValueError('Website destination is outside the configured public host')
        addresses=socket.getaddrinfo(self.host,443,type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Non-public website address blocked')

    def get(self,client,url):
        # Only repeat read-only GETs on transient transport/server failures.
        for attempt in range(2):
            try:return self._get(client,url)
            except (httpx.TimeoutException,httpx.NetworkError):
                if attempt:raise
            except httpx.HTTPStatusError as exc:
                if attempt or exc.response.status_code not in {502,503,504}:raise

    def _get(self,client,url):
        for _ in range(4):
            self.validate_url(url)
            if getattr(self,'robots',None) and not self.robots.can_fetch('MusterSEOResearch',url):
                raise ValueError('Robots restriction')
            delay=max(0,.5-(time.monotonic()-self.last_request))
            if delay:time.sleep(delay)
            self.last_request=time.monotonic()
            with client.stream('GET',url) as response:
                if response.status_code in {301,302,303,307,308}:
                    destination=urljoin(url,response.headers.get('location',''))
                    if urlsplit(destination).hostname!=self.host:
                        raise ExternalRedirect(destination,response.status_code)
                    url=destination;continue
                response.raise_for_status();data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>5*1024*1024:raise ValueError('Website response exceeds 5 MB')
                return bytes(data),url,response.headers
        raise ValueError('Too many website redirects')

    def collect(self,extra_urls=()):
        pages=[];urls=set();sitemaps=[];seen_maps=set();complete=True;robots_available=False
        self.failures=[];self.excluded=[];self.outcomes=[];self.discovery={}
        def discover(address,source):
            address=urldefrag(address)[0]
            urls.add(address);self.discovery.setdefault(address,set()).add(source)
        def failure(address,exc):
            code=('robots_blocked' if str(exc)=='Robots restriction' else
                  'response_too_large' if 'exceeds 5 MB' in str(exc) else
                  'redirect_limit' if 'Too many website redirects' in str(exc) else
                  'unsafe_destination' if isinstance(exc,ValueError) and 'website' in str(exc).casefold() else
                  'http_error' if isinstance(exc,httpx.HTTPStatusError) else
                  'transport_error' if isinstance(exc,(httpx.TimeoutException,httpx.NetworkError)) else 'invalid_content')
            value={'url':address,'reason':code}
            if isinstance(exc,httpx.HTTPStatusError):value['http_status']=exc.response.status_code
            self.failures.append(value);self.outcomes.append({**value,'status':'failed'})
        with httpx.Client(timeout=30,follow_redirects=False,headers={'User-Agent':'MusterSEOResearch/1.0'}) as client:
            robots=RobotFileParser();robots.set_url(self.base+'/robots.txt')
            try:
                raw,_,_=self.get(client,self.base+'/robots.txt');robots.parse(raw.decode('utf-8','replace').splitlines())
                self.robots=robots;robots_available=True
                sitemaps=robots.site_maps() or [self.base+'/sitemap_index.xml']
            except Exception as exc:
                failure(self.base+'/robots.txt',exc)
                return [],{'inventory_complete':False,'pages_retrieved':0,'failures':self.failures,'limitations':['Robots-Regeln konnten nicht geprüft werden; Scan gestoppt.']}
            while sitemaps and len(seen_maps)<50:
                address=sitemaps.pop(0)
                if address in seen_maps:continue
                seen_maps.add(address)
                try:
                    if not robots.can_fetch('MusterSEOResearch',address):raise ValueError('Robots restriction')
                    raw,_,_=self.get(client,address)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsafe XML')
                    tree=ET.fromstring(raw)
                    if tree.tag.endswith('sitemapindex'):sitemaps += [n.text for n in tree.findall('.//{*}sitemap/{*}loc') if n.text]
                    else:
                        for node in tree.findall('.//{*}url/{*}loc'):
                            if node.text:discover(node.text,'sitemap')
                except Exception as exc:complete=False;failure(address,exc)
            if sitemaps:complete=False
            # Read all three published content collections, with no WordPress write routes.
            for route in ['pages','posts','branchen']:
                for offset in range(1,6):
                    address=self.base+f'/wp-json/wp/v2/{route}?context=view&status=publish&per_page=100&page={offset}&_fields=id,link'
                    try:
                        if not robots.can_fetch('MusterSEOResearch',address):raise ValueError('Robots restriction')
                        raw,_,headers=self.get(client,address);items=decode_public_collection(httpx.Response(200,content=raw))
                        if not isinstance(items,list):raise ValueError('Invalid public collection')
                        for item in items:
                            if isinstance(item,dict) and isinstance(item.get('link'),str):discover(item['link'],'wordpress:'+route)
                        total=int(headers.get('x-wp-totalpages','1'))
                        if offset>=total:break
                        if offset==5:complete=False
                    except Exception as exc:complete=False;failure(address,exc);break
            for address in extra_urls:discover(address,'gsc')
            allowed=[]
            for address in sorted(urls):
                if urlsplit(address).path.lower().endswith(('.xml','.kml','.pdf','.jpg','.png','.webp','.zip')):
                    self.excluded.append({'url':address,'reason':'non_html'});continue
                try:self.validate_url(address)
                except ValueError:complete=False;self.excluded.append({'url':address,'reason':'unsafe_destination'});continue
                allowed.append(address)
            if len(allowed)>500:complete=False
            for address in allowed[:500]:
                try:
                    if not robots.can_fetch('MusterSEOResearch',address):raise ValueError('Robots restriction')
                    raw,final,headers=self.get(client,address)
                    if 'html' not in headers.get('content-type',''):raise ValueError('Non-HTML response')
                    page=extract_page(raw,final)
                    # Only an already fetched, robots-permitted, same-host HTML page
                    # with no usable text is eligible. Privacy exclusions and denied
                    # access never trigger a third-party bypass.
                    if (self.settings.firecrawl_enabled and self.storage and self.run_id and
                            page['content_exclusions']==[{'reason':'no_substantive_text','count':1}]):
                        from src.integrations.official_research import extract_website_fallback
                        rendered=extract_website_fallback(self.settings,self.storage,self.run_id,final)
                        replacement=extract_page(rendered,final)
                        replacement['extraction_method']='firecrawl'
                        replacement['original_html_sha256']=hashlib.sha256(raw).hexdigest()
                        for field in ('meta_title','meta_description','canonical'):
                            if page.get(field):replacement[field]=page[field]
                        replacement['visibility_signals']=page['visibility_signals']
                        replacement['indexable']=replacement['indexable'] and page['indexable']
                        page=replacement
                    if 'noindex' in headers.get('x-robots-tag','').casefold():page['indexable']=False
                    duplicate=any(p['url']==final for p in pages)
                    self.outcomes.append({'url':address,'status':'duplicate' if duplicate else 'retrieved',
                        'final_url':final,'sources':sorted(self.discovery.get(address,[]))})
                    if not duplicate:pages.append(page)
                except ExternalRedirect as exc:
                    outcome={'url':address,'reason':'external_redirect','destination':exc.destination,'http_status':exc.status,
                             'sources':sorted(self.discovery.get(address,[]))}
                    self.excluded.append(outcome);self.outcomes.append({**outcome,'status':'external_redirect'})
                except Exception as exc:
                    from src.services.recovery import UncertainOperation
                    from src.storage.sqlite_storage import BudgetExceeded
                    from src.integrations.official_research import ResearchProviderError
                    if isinstance(exc,(UncertainOperation,BudgetExceeded,ResearchProviderError)):
                        raise
                    complete=False;failure(address,exc)
        removed=remove_repeated_templates(pages)
        content_limits=[{'url':p['url'],'exclusions':p['content_exclusions']} for p in pages if p['content_truncated'] or not p['links_complete']]
        resolved=sum(o['status'] in {'retrieved','duplicate','external_redirect'} for o in self.outcomes)
        external=sum(o['status']=='external_redirect' for o in self.outcomes)
        limitations=[]
        if not complete:limitations.append('Website-Inventar ist unvollständig; nicht alle eigenen HTML-URLs konnten geprüft werden.')
        if content_limits:limitations.append('Einzelne Website-Textstellen wurden aus Datenschutz- oder Sicherheitsgründen ausgeschlossen. Diese Auslassungen beweisen keine fehlenden Inhalte.')
        if external:limitations.append(f'{external} eigene URLs leiten zu fremden Websites weiter. Die Weiterleitungen sind erfasst; fremde Inhalte wurden nicht abgerufen.')
        return pages,{'inventory_complete':complete,'content_complete':complete and not content_limits,
            'pages_retrieved':len(pages),'discovered_html_urls':len(allowed),'resolved_html_urls':resolved,
            'duplicate_urls':sum(o['status']=='duplicate' for o in self.outcomes),'external_redirects':external,
            'content_limitations':content_limits,'url_outcomes':self.outcomes,'crawl_limit_reached':len(allowed)>500 or bool(sitemaps),
            'crawler_access':[{'agent':agent,'scope_url':self.base+'/',
                'purpose':purpose,'allowed_by_robots':robots.can_fetch(agent,self.base+'/') if robots_available else None,
                'limitations':'Robots policy for this URL only; CDN access and indexing are not verified.'}
                for agent,purpose in [('Googlebot','search'),('bingbot','search'),('Claude-SearchBot','search'),('Claude-User','user_retrieval'),('OAI-SearchBot','search'),('ClaudeBot','separate_training_control'),('GPTBot','separate_training_control')]],
            'template_sections_omitted':removed,'failures':self.failures,'excluded':self.excluded,'limitations':limitations}
