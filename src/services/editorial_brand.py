"""Deterministic brand presentation for landing pages and blog articles.

Fonts and original company assets are bundled, hash checked and embedded.
Rendering never calls a font service, image provider or paid model.
"""
import base64
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from bs4 import BeautifulSoup

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'brand'
PROFILE_VERSION = json.loads((ASSETS/'design-contract.json').read_text(encoding='utf-8'))['version']
ARTICLE_CSP = "default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'"


def brand_for_agents(settings):
    directory=settings.input_dir/'brand-guidelines'
    rules=json.loads((directory/'rules.json').read_text(encoding='utf-8'))
    rules['editorial_guidelines']=json.loads((directory/'editorial-rules.json').read_text(encoding='utf-8'))
    # The original PDF-derived file is preserved, but its older visual values
    # must not compete with the later page-specific guides in model context.
    for key in ('typography','layout','colors'):
        rules['editorial_guidelines'].pop(key,None)
    rules['editorial_guidelines']['visual_contract']=design_contract()
    return rules


def public_contact_emails(settings):
    """Only the explicit company email fact supplies public contact exceptions."""
    from urllib.parse import urlsplit
    host = (urlsplit(settings.wordpress_base_url).hostname or '').removeprefix('www.').casefold()
    facts = json.loads((settings.input_dir / 'brand-guidelines/facts.json').read_text())['facts']
    return frozenset(address.casefold() for fact in facts if fact.get('id') == 'email'
                     for address in re.findall(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', fact.get('wording', ''), re.I)
                     if address.rsplit('@', 1)[1].casefold() == host)


@lru_cache(maxsize=8)
def asset_data(name):
    manifest=json.loads((ASSETS/'manifest.json').read_text(encoding='utf-8'))
    if name not in manifest['assets']:raise ValueError('Unapproved brand asset')
    content=(ASSETS/name).read_bytes()
    if hashlib.sha256(content).hexdigest()!=manifest['assets'][name]['sha256']:
        raise ValueError('Brand asset differs from the verified original')
    mime={'.woff2':'font/woff2','.svg':'image/svg+xml','.avif':'image/avif','.webp':'image/webp'}[Path(name).suffix]
    return 'data:'+mime+';base64,'+base64.b64encode(content).decode('ascii')


@lru_cache(maxsize=1)
def design_contract():
    return json.loads((ASSETS/'design-contract.json').read_text(encoding='utf-8'))


def profile(page_type='landing_page'):
    # Return copies: callers cannot change the cached effective contract.
    return dict(design_contract()['profiles'][page_type])


@lru_cache(maxsize=3)
def editorial_css(page_type='landing_page'):
    tokens=profile(page_type)
    variables=';'.join('--rs-'+k.replace('_','-')+':'+str(v)+('px' if k in {
        'copy_width','section_desktop','section_mobile','button_radius','card_radius'} else '')
        for k,v in tokens.items() if k!='reference')
    variables+=';'+';'.join('--rs-gap-'+key+':'+str(value)+'px' for key,value in design_contract()['spacing'].items())
    faces=[]
    for family,weight,name in [('Cabin',400,'cabin-400-latin.woff2'),('Cabin',500,'cabin-500-latin.woff2'),
        ('Montserrat',600,'montserrat-600-latin.woff2'),('Roboto Slab',400,'roboto-slab-400-latin.woff2')]:
        faces.append(f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};font-display:swap;src:url('{asset_data(name)}') format('woff2')}}")
    return ''.join(faces)+'.rs-document{'+variables+'}'+(ASSETS/'editorial.css').read_text(encoding='utf-8')


def brand_header(label='Muster & Partner · Steuerberatung'):
    from html import escape
    return ('<header class="rs-brand-header"><div class="brand-wrap"><img src="'+asset_data('logo.svg')+
        '" width="1442" height="191" alt="Muster &amp; Partner Steuerberatung"><p>'+escape(label)+'</p></div></header>')


def action_groups(body):
    """Give existing standalone buttons layout containers; leave prose links alone.

    Runs once in the common shell, covering combined/legacy articles and reports.
    No labels, destinations or saved content are generated or changed.
    """
    from bs4 import NavigableString
    soup=BeautifulSoup(body,'html.parser')
    def button(tag):
        return getattr(tag,'name',None)=='a' and bool({'button','rs-cta'}.intersection(tag.get('class',[])))
    for group in soup.select('.hero-actions,.rs-actions'):
        group['class']=list(dict.fromkeys(group.get('class',[])+['rs-actions']))
    for paragraph in soup.find_all('p'):
        if paragraph.find_parent(class_='rs-actions'):continue
        children=list(paragraph.contents)
        if any(button(child) for child in children) and all(
            button(child) or isinstance(child,NavigableString) and not str(child).strip()
            or getattr(child,'name',None)=='br' for child in children):
            paragraph.name='div';paragraph['class']=list(dict.fromkeys(paragraph.get('class',[])+['rs-actions']))
    for link in list(soup.select('a.button,a.rs-cta')):
        if link.find_parent(class_='rs-actions') or link.parent.name in {'p','span','strong','em','h1','h2','h3','h4','figcaption','summary'}:continue
        group=soup.new_tag('div',attrs={'class':'rs-actions'});link.wrap(group)
    # Adjacent standalone paragraphs are one visual group, rather than separate
    # blocks with doubled spacing. Keep targetable containers intact.
    def next_group(group):
        following=group.next_sibling
        while isinstance(following,NavigableString) and not str(following).strip():following=following.next_sibling
        return following if getattr(following,'name',None) and 'rs-actions' in following.get('class',[]) else None
    for group in list(soup.select('.rs-actions')):
        if group.parent is None:continue
        following=next_group(group)
        while following and not following.get('id'):
            for child in list(following.contents):group.append(child.extract())
            following.decompose();following=next_group(group)
    return str(soup)


def branded_document(title,body,*,page_type='planning',language='de',meta='',schema=None,report=False):
    """One offline, sandbox-compatible shell for current previews and exports."""
    from html import escape
    structured=''
    if schema:
        value=json.dumps(schema,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
        structured='<script type="application/ld+json">'+value+'</script>'
    if report:body=brand_header()+'<main class="rs-report">'+body+'</main>'
    body=action_groups(body)
    return ('<!doctype html><html lang="'+escape(language,quote=True)+'" data-brand-version="'+PROFILE_VERSION+
        '" data-page-type="'+page_type+'"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow"><title>'+escape(title)+'</title><meta name="description" content="'+escape(meta,quote=True)+
        '"><style>'+editorial_css(page_type)+'</style>'+structured+'</head><body class="rs-document rs-'+('landing' if page_type=='landing_page' else page_type)+'">'+body+'</body></html>')


def validate_editorial_design(html, article):
    soup=BeautifulSoup(html,'html.parser');errors=[]
    root=soup.find('html')
    if not root or root.get('data-brand-version')!=PROFILE_VERSION:
        return ['Der Entwurf verwendet nicht die verbindliche redaktionelle Markenvorlage.']
    if root.get('data-page-type')!=article.page_type:errors.append('Markenvorlage passt nicht zum bestätigten Seitentyp.')
    styles=soup.find_all('style')
    if len(styles)!=1 or (styles[0].string or '').strip()!=editorial_css(article.page_type).strip():
        errors.append('Die verbindlichen Markenschriften, Farben oder Layoutregeln wurden verändert.')
    logo=soup.select_one('.brand-logo');photo=soup.select_one('.hero-photo')
    if not logo or logo.get('src')!=asset_data('logo.svg'):errors.append('Original-Logo fehlt oder wurde verändert.')
    image='office.webp' if article.page_type=='blog' else 'team.avif'
    if not photo or photo.get('src')!=asset_data(image):errors.append('Verifizierte Kanzleibildsprache fehlt oder wurde verändert.')
    if not soup.select_one('.site-header .mobile-menu summary'):errors.append('Mobile Markennavigation fehlt.')
    if not soup.select_one('.hero-actions .button') or not soup.select_one('.floating-cta'):
        errors.append('Verbindliche Kontaktmöglichkeiten fehlen.')
    return errors


def validate_branded_document(html,page_type):
    """Local structural checks only; not semantic QA or a WordPress import test."""
    soup=BeautifulSoup(html,'html.parser');errors=[]
    if not soup.html or soup.html.get('data-brand-version')!=PROFILE_VERSION or soup.html.get('data-page-type')!=page_type:
        errors.append('The current page-specific branding profile is missing.')
    styles=soup.find_all('style')
    if len(styles)!=1 or styles[0].string!=editorial_css(page_type):
        errors.append('The shared fonts, colours or layout stylesheet differs.')
    if soup.find('link',rel='stylesheet') or soup.find(['iframe','object','embed']):
        errors.append('The output contains an external stylesheet or active embedded content.')
    if any(tag.get('type')!='application/ld+json' for tag in soup.find_all('script')):
        errors.append('Executable scripts are not permitted in the preview.')
    if any(any(name.lower().startswith('on') for name in tag.attrs) for tag in soup.find_all(True)):
        errors.append('Inline event handlers are not permitted.')
    if any(not img.get('src','').startswith('data:image/') for img in soup.find_all('img')):
        errors.append('Preview media must be embedded and available offline.')
    # Inspect the assembled components as well as stylesheet integrity. Missing
    # business content is not filled in or rejected by this presentation check.
    for module,selector,expected in [('S01','p','rs-breadcrumb'),('S03','p','rs-meta')]:
        for item in soup.select(f'[data-module="{module}"] .module-copy>div>{selector}'):
            if expected not in item.get('class',[]):errors.append(f'{module} is missing its dedicated presentation.')
    for quote in soup.select('section[data-module] blockquote'):
        if not {'rs-callout','rs-practice-quote'}.intersection(quote.get('class',[])):
            errors.append('An existing quote has no identified presentation.')
    for table in soup.select('section[data-module] table'):
        if 'table-scroll' not in table.parent.get('class',[]):errors.append('An existing table has no scrollable wrapper.')
    return errors
