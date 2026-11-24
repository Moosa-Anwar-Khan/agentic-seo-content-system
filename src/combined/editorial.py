"""Versioned editorial module rules, adapted to HTML for the MVP."""
import json
import re
from html import escape
from urllib.parse import urlsplit

from src.combined.markdown import parse, word_count, plain_text
from src.combined.models import EditorialOutput
from src.combined.evidence import claim_errors, allowed_official
from src.services.image_processing import keyword_slug
from src.schemas.content import Article, Section


def rules(settings, page_type):
    directory = settings.input_dir/'brand-guidelines'
    pack = json.loads((directory/('blog.json' if page_type == 'blog' else 'landing.json')).read_text())
    pack['approved_facts'] = json.loads((directory/'facts.json').read_text())['facts']
    pack['approved_prices'] = json.loads((directory/'prices.json').read_text())
    pack['approved_reviews'] = json.loads((directory/'reviews.json').read_text())
    # Explicit operator-approved override; original references remain unchanged.
    if page_type == 'landing_page':
        for matrix in pack['matrix'].values():
            matrix['R'] = [item for item in matrix['R'] if item != 'L12']
        for module in pack['modules']:
            if module['id'] == 'L12': module['rule'] = 'Optional: only genuine approved reviews with correct attribution; omit when unavailable.'
            if module['id'] == 'L01': module['rule'] = 'H1, benefit and primary CTA; ratings only with genuine approved evidence.'
    # Complete authoritative text supplements the compact machine rules.
    filename = ('Brand-Guideline_Blog-Articles_ENGLISH.md' if page_type == 'blog'
                else 'Brand-Guideline_Landing-Pages_ENGLISH.md')
    original = (settings.input_dir.parents[0]/'docs'/filename).read_text()
    pack['effective_policy'] = (
        'Current combined-mode requirements override older source-guideline instructions: '
        'Use only the selected configured audience and approved service facts; formal German Sie; manual publishing only. '
        'Keep visible FAQs, but do not request FAQPage as a Google rich-result target '
        'or self-serving AggregateRating. Use only the effective schema_types below. '
        'Tavily/Firecrawl are API tools; NotebookLM and Google Docs are manual tools. '
        'Do not follow tool-specific execution or filesystem instructions '
        'in the preserved reference. Never invent missing prices, reviews or URLs. '
        'Testimonials are optional: omit L12 and hero ratings when genuine approved reviews are unavailable. Never invent reviews or ratings. '
        'Professional review and final editorial approval remain separate human gates.'
    )
    pack['guideline'] = ('EFFECTIVE PROJECT POLICY (takes precedence over the preserved '
                         'reference below):\n'+pack['effective_policy']+'\n\n'
                         'PRESERVED BUSINESS GUIDELINE:\n'+original)
    return pack


def prompt_rules(settings, page_type):
    """Preserve the complete reference in ordered pieces below the input limit."""
    pack = rules(settings, page_type)
    from src.services.editorial_brand import PROFILE_VERSION, profile
    pack['visual_contract']={'version':PROFILE_VERSION,'profile':profile(page_type),
        'ownership':'Code owns layout and styling; generate supported German module content only. Do not invent CSS or rewrite theme-owned areas.'}
    guideline = pack.pop('guideline')
    sections = []
    current = ''
    for line in guideline.splitlines(keepends=True):
        if current and len(current) + len(line) > 10000:
            sections.append(current)
            current = ''
        current += line
    if current:
        sections.append(current)
    pack['guideline_sections'] = sections
    return pack


def markdown(draft):
    return '\n\n'.join(f'<!-- {m.id} -->\n{m.markdown}' for m in draft.modules)


def faq_questions(draft):
    """Recognize both supported Markdown presentations without changing the text."""
    questions = []
    for module in draft.modules:
        if module.id not in {'L14', 'S10', 'S11'}:
            continue
        for line in module.markdown.splitlines():
            match = re.fullmatch(r'\s*(?:#{2,3}\s+(.+\?)|\*\*(.+\?)\*\*)\s*', line)
            if match:
                questions.append((module.id, match.group(1) or match.group(2)))
    return questions


def same_page(first, second):
    """Tracking parameters do not change the destination of a contact CTA."""
    a, b = urlsplit(first), urlsplit(second)
    return (a.scheme, a.netloc, a.path.rstrip('/')) == (b.scheme, b.netloc, b.path.rstrip('/'))


def structural_errors(draft, keyword, pack):
    text = markdown(draft)
    doc = parse(text)
    ids = [m.id for m in draft.modules]
    required = ([m['id'] for m in pack['modules'] if m.get('required') and not m.get('template')]
                if draft.page_type == 'blog' else pack['matrix']['landing:'+draft.landing_type]['R'])
    errors = []
    if len(ids) != len(set(ids)):
        errors.append('Abschnittsmarkierungen sind doppelt vorhanden.')
    for module in required:
        if module not in ids:
            errors.append('Pflichtabschnitt fehlt: '+module)
    if draft.page_type == 'blog':
        catalogue = [m['id'] for m in pack['modules']]
        positions = [catalogue.index(i) for i in ids if i in catalogue]
        if positions != sorted(positions):
            errors.append('Blogabschnitte stehen nicht in der vorgegebenen Reihenfolge.')
    elif ids[:2] != ['L01','L02'] or ids[-3:] != ['L14','L15','L16']:
        errors.append('Landingpage beginnt mit L01/L02 und endet mit L14/L15/L16.')
    if any(i not in {m['id'] for m in pack['modules']} for i in ids):
        errors.append('Unbekannte Abschnittsmarkierung.')
    if sum(h.level == 1 for h in doc.headings) != 1:
        errors.append('Genau eine H1 erforderlich.')
    if not any(h.level == 1 and keyword.casefold() in h.text.casefold() for h in doc.headings):
        errors.append('Hauptkeyword fehlt in der tatsächlichen H1.')
    previous = 0
    for heading in doc.headings:
        if previous and heading.level > previous+1:
            errors.append('Überschriftenebene wurde übersprungen.')
        previous = heading.level
    anchors = {keyword_slug(h.text) for h in doc.headings}
    if len(anchors) != len(doc.headings):
        errors.append('Überschriften benötigen eindeutige Sprungziele.')
    for link in doc.links:
        if link.url.startswith('#') and link.url[1:] not in anchors:
            errors.append('Sprungziel fehlt: '+link.url)
    if re.search(r'<\s*/?\s*[a-zA-Z][^>]*>', text+' '+draft.signature_element):
        errors.append('Roher HTML-Code im Markdown ist nicht zulässig.')
    if re.search(r'\b(du|dich|dein|deine|euch|euer)\b', plain_text(text), re.I):
        errors.append('Informelle Ansprache gefunden.')
    for forbidden in pack['forbidden']:
        if re.search(r'(?<!\w)'+re.escape(forbidden), plain_text(text), re.I):
            errors.append('Unzulässige Formulierung: '+forbidden)
    if re.search(r'garantierte Steuerersparnis|100\s*%\s*rechtssicher', text, re.I):
        errors.append('Unzulässige Garantie.')
    kw = keyword.casefold()
    if sum(kw in h.text.casefold() for h in doc.headings if h.level == 2) < 2:
        errors.append('Hauptkeyword fehlt in mindestens zwei H2.')
    for label, value in [('H1', draft.title), ('Title',draft.meta_title),
                         ('Meta-Description',draft.meta_description)]:
        if kw not in value.casefold():
            errors.append('Hauptkeyword fehlt: '+label)
    if keyword_slug(keyword) not in draft.slug:
        errors.append('Hauptkeyword fehlt im Slug.')
    if draft.page_type == 'blog':
        minimum = 2000 if draft.article_kind == 'guide' else 1200
        maximum = 3500 if draft.article_kind == 'guide' else 2000
        if not minimum <= word_count(text) <= maximum:
            errors.append(f'Artikellänge muss {minimum}-{maximum} Wörter betragen.')
        for module, low, high in [('S06',5,8),('S14',5,8)]:
            section = doc.section(module)
            count = len(re.findall(r'^\s*(?:[-*]|\d+[.)])\s+', section.text, re.M)) if section else 0
            if not low <= count <= high:
                errors.append(f'{module}: {low}-{high} Listeneinträge erforderlich.')
        core = [h for h in doc.headings if h.module == 'S08' and h.level == 2]
        if not 3 <= len(core) <= 7:
            errors.append('S08 benötigt drei bis sieben Kernabschnitte.')
        if sum(h.module == 'S10' and h.level == 2 and h.text.endswith('?') for h in doc.headings) < 2:
            errors.append('S10 benötigt mindestens zwei Frageüberschriften.')
    else:
        if not draft.signature_element.strip():
            errors.append('Individuelles Signature-Element fehlt.')
        if not 600 <= word_count(text) <= 3000:
            errors.append('Landingpage benötigt 600-3000 Wörter.')
        if not 4 <= sum(same_page(l.url, pack['cta_target']) for l in doc.links) <= 6:
            errors.append('Vier bis sechs Kontakt-CTAs erforderlich.')
        if not 6 <= sum(module == 'L14' for module, question in faq_questions(draft)) <= 8:
            errors.append('L14 benötigt sechs bis acht FAQ-Fragen.')
    if not pack['approved_reviews'].get('reviews') and re.search(r'[★⭐]|\b\d(?:[.,]\d)?\s*(?:/\s*5|von\s*5|Sterne)', text, re.I):
        errors.append('Bewertungen oder Sterne ohne freigegebenen Beleg weglassen.')
    if draft.page_type == 'landing_page' and any(m.id == 'L12' for m in draft.modules):
        reviews = pack['approved_reviews'].get('reviews', [])
        if not reviews:
            errors.append('L12 ohne echte freigegebene Bewertungen weglassen.')
        else:
            body = next(m.markdown for m in draft.modules if m.id == 'L12')
            if not any(r.get('text') and r['text'] in body for r in reviews):
                errors.append('L12 enthält keinen freigegebenen Bewertungswortlaut.')
    return list(dict.fromkeys(errors))


def link_errors(state, settings):
    """Unverified destinations must be corrected before a draft reaches review."""
    draft = EditorialOutput.model_validate(state.editorial)
    errors = []
    known = {p.url for p in state.analysis.page_evidence} if state.analysis else set()
    doc = parse(markdown(draft))
    # Existing WordPress guides need not use a /blog/ permalink. S15 is the
    # declared related-reading list; contextual links must refer to those same
    # verified targets. Semantic relevance remains a QA/professional requirement.
    related_targets={link.url.split('#')[0].split('?')[0] for link in doc.links if link.module=='S15'}
    blog_links = []; lp_links = []
    for link in doc.links:
        url = link.url.split('#')[0].split('?')[0]
        if not url or url.startswith(('tel:','mailto:')):
            continue
        host = urlsplit(url).hostname or ''
        if host == urlsplit(settings.wordpress_base_url).hostname:
            if '/mandant-werden/' in url:
                continue
            if url not in known:
                errors.append('Interner Link nicht im vorhandenen Seiteninventar: '+url)
            elif '/blog/' in url or link.module == 'S15' or (link.module=='S08' and url in related_targets) or (draft.page_type == 'landing_page' and link.module == 'L14'):
                blog_links.append(link)
            else:
                lp_links.append(link)
        elif not allowed_official(url) and host not in {'www.linkedin.com','www.instagram.com'}:
            errors.append('Externer Link nicht als amtliche Quelle oder Kanal freigegeben: '+url)
    if draft.page_type == 'blog':
        from src.services.link_policy import contract
        policy=contract(state)
        contextual={l.url.split('#')[0].split('?')[0].rstrip('/') for l in blog_links if l.module=='S08'}
        if len(contextual) < policy['contextual_minimum']:
            errors.append('Mindestens ein kontextueller Ratgeberlink in S08 erforderlich.' if state.internal_link_policy=='starter_library' else 'Mindestens drei kontextuelle Bloglinks in S08 erforderlich.')
        if not policy['related_minimum'] <= len({l.url.split('#')[0].split('?')[0].rstrip('/') for l in blog_links if l.module == 'S15'}) <= 8:
            errors.append('S15 benötigt ein bis acht vorhandene Ratgeberziele.' if state.internal_link_policy=='starter_library' else 'S15 benötigt sechs bis acht vorhandene Blogziele.')
        if not 1 <= len(lp_links) <= 3:
            errors.append('Ein bis drei passende Landingpage-Links erforderlich.')
    else:
        if any(l.module != 'L14' for l in blog_links):
            errors.append('Bloglinks nur unter L14 zulässig.')
        if not 2 <= len({l.url.split('#')[0].split('?')[0].rstrip('/') for l in blog_links}) <= 4:
            errors.append('Zwei bis vier vorhandene Ratgeberlinks unter L14 erforderlich.')
    return list(dict.fromkeys(errors))


def release_errors(state, settings):
    if not state.editorial:
        return ['Strukturierter Entwurf fehlt.']
    draft = EditorialOutput.model_validate(state.editorial)
    errors = structural_errors(draft, state.briefing.primary_keyword, rules(settings,draft.page_type))
    errors += claim_errors(draft,state.research_packet.get('official_sources',[]),settings.tax_year)
    from datetime import datetime,timedelta,timezone
    for source in state.research_packet.get('official_sources',[]):
        age=datetime.now(timezone.utc)-datetime.fromisoformat(source['retrieved_at'])
        if not timedelta(0)<=age<=timedelta(hours=settings.research_cache_hours):
            errors.append('Quellenstand erneuern: '+source['url'])
    if not state.research_packet.get('official_sources'):
        errors.append('Amtliche Quellen fehlen.')
    errors += ['Offene Fachfrage: '+q for q in draft.unresolved_questions]
    if state.research_packet.get('official_errors'):
        errors += ['Quellenabruf unvollständig; Quellenliste fachlich vervollständigen.']
    if settings.demo_mode:
        errors.append('Demo-Inhalte können nicht als Live-Inhalte freigegeben werden.')
    if not state.graphic_file or state.graphic_revision != state.revision:
        errors.append('Geprüfte Hauptinfografik für diese Revision fehlt.')
    errors += link_errors(state, settings)
    if draft.page_type == 'landing_page':
        from src.services.fee_presentation import release_issues
        errors += release_issues(state, settings, draft, markdown(draft))
        required = rules(settings, draft.page_type)['matrix']['landing:' + draft.landing_type]['R']
        if 'L08' in required:
            statement = next((m.markdown for m in draft.modules if m.id == 'L08'), '')
            if not statement.strip() or re.search(r'\[(?:FREIGEGEBENES STATEMENT FEHLT|STATEMENT FEHLT)\]', statement):
                errors.append('Freigegebenes persönliches Statement mit Zuschreibung fehlt.')
    return list(dict.fromkeys(errors))


def as_article(draft, keyword):
    """Compatibility facade for the existing portal, social drafts and image tools."""
    sections = []
    for module in draft.modules:
        if module.id in {'S02','L01'}:
            continue
        doc = parse('<!-- '+module.id+' -->\n'+module.markdown)
        heading = next((h.text for h in doc.headings if h.level == 2),module.id)
        sections.append(Section(id=keyword_slug(heading)+'-'+module.id.lower(),heading=heading,
                                paragraphs=[plain_text(module.markdown)]))
    return Article(title=draft.title,meta_title=draft.meta_title,meta_description=draft.meta_description,
        introduction=['Redaktioneller Entwurf zur Prüfung.','Freigabe und Upload erfolgen ausschließlich manuell.'],
        sections=sections,internal_link_ids=[],cta_heading='Ihr persönliches Gespräch',
        cta_text='Das Erstgespräch ist kostenfrei und unverbindlich.',faqs=[],
        source_urls=[],editorial_notes=draft.unresolved_questions,page_type=draft.page_type,
        primary_keyword=keyword)


def render(draft):
    """Read-only presentation: keep model text, use the effective visual contract."""
    import markdown as md
    from bs4 import BeautifulSoup
    from src.schemas.content import DISCLAIMER
    from src.services.editorial_brand import asset_data, brand_header, branded_document
    from urllib.parse import urlsplit
    blog = draft.page_type == 'blog'
    nodes = []
    headings = []
    used_ids = set()
    callout_label=re.compile(r'^(Achtung|Wichtig(?: für [^:]+)?|Gut zu wissen|Nächster Schritt):',re.I)
    for module in draft.modules:
        # Theme-owned modules are never duplicated inside exported article content.
        if module.id in {'S04','S19'}:
            continue
        content = md.markdown(re.sub(r'<[^>]*>','',module.markdown),extensions=['tables','sane_lists'])
        soup = BeautifulSoup(content,'html.parser')
        for image in soup.find_all('img'):
            image.decompose()
        for tag in soup.find_all(['h1','h2','h3']):
            base=keyword_slug(tag.get_text())
            ident=base;index=2
            while ident in used_ids:
                ident=base+'-'+str(index);index+=1
            used_ids.add(ident);tag['id']=ident
            if tag.name=='h2':headings.append((ident,tag.get_text()))
        for link in soup.find_all('a'):
            href=link.get('href','')
            if not href.startswith(('https://','#','tel:','mailto:')):
                link.unwrap();continue
            try:parsed=urlsplit(href)
            except ValueError:
                link.unwrap();continue
            if (parsed.scheme=='https' and parsed.hostname=='steuerkanzlei.example'
                    and parsed.path.rstrip('/')=='/mandant-werden' and not parsed.username and not parsed.password):
                link['class']=['rs-cta']
        for table in soup.find_all('table'):
            wrapper=soup.new_tag('div',attrs={'class':'table-scroll','role':'region','aria-label':'Tabelle — horizontal scrollbar','tabindex':'0'})
            table.wrap(wrapper)
        for listing in soup.find_all('ul',recursive=False):
            if module.id in {'L02','L03','L04','S06','S15'}:listing['class']=['rs-card-grid']
        for listing in soup.find_all('ol',recursive=False):
            if module.id in {'L07','S14'}:listing['class']=['rs-steps']
        # Attach presentation only to identifiable, existing content. In
        # particular, do not invent an eyebrow, author, price or metric.
        if module.id=='L01':
            title=soup.find('h1',recursive=False)
            category=title.find_next_sibling() if title else None
            if category and category.name=='p' and '·' in category.get_text() and not category.find('a'):
                category['class']=['rs-eyebrow'];title.insert_before(category.extract())
        if module.id=='S01':
            for item in soup.find_all(['p','nav'],recursive=False):item['class']=['rs-breadcrumb']
        if module.id=='S02':
            lead=soup.find('p',recursive=False)
            if lead:lead['class']=['rs-lead']
        if module.id=='S03':
            for item in soup.find_all('p',recursive=False):item['class']=['rs-meta']
        if module.id=='L10':
            metrics=soup.find('ul',recursive=False)
            if metrics and len(metrics.find_all('li',recursive=False))==3:
                metrics['class']=['rs-key-figures']
        if module.id in {'L06','L09','L13'}:
            cards=[]
            for paragraph in soup.find_all('p',recursive=False):
                if paragraph.contents and getattr(paragraph.contents[0],'name',None)=='strong':
                    paragraph['class']=['rs-objection'] if module.id=='L13' else ['rs-content-card']
                    if module.id=='L09':cards.append(paragraph)
            if cards:
                grid=soup.new_tag('div',attrs={'class':'rs-card-grid'});cards[0].insert_before(grid)
                for card in cards:grid.append(card.extract())
        if module.id=='L05':
            for value in list(soup.find_all(string=re.compile(r'\bStBVV\b'))):
                # Mark the existing fee-ordinance reference, never synthesize it.
                if value.parent.name in {'a','code','pre'}:continue
                pieces=re.split(r'(\bStBVV\b)',str(value))
                for piece in pieces:
                    if piece=='StBVV':
                        badge=soup.new_tag('span',attrs={'class':'rs-fee-badge'});badge.string=piece;value.insert_before(badge)
                    else:value.insert_before(piece)
                value.extract()
        if module.id=='S12':
            for cell in soup.find_all('td'):
                priority={'sofort handeln':'urgent','zeitnah':'soon','planbar':'planned'}.get(cell.get_text(' ',strip=True).casefold())
                if priority:cell['class']=['rs-priority-'+priority]
        # Markdown merges consecutive quote paragraphs into one block. Separate
        # explicit labels so a practice quote cannot swallow a coloured callout.
        for quote in list(soup.find_all('blockquote')):
            current=None
            for child in list(quote.contents):
                if not getattr(child,'name',None):continue
                if current is None or callout_label.match(child.get_text(' ',strip=True)):
                    current=soup.new_tag('blockquote');quote.insert_before(current)
                current.append(child.extract())
            quote.decompose()
        for quote in soup.find_all('blockquote'):
            text=quote.get_text(' ',strip=True).casefold()
            label=callout_label.match(quote.get_text(' ',strip=True))
            if label:
                quote['class']=['rs-callout']+(['rs-attention'] if text.startswith('achtung') else ['rs-good'] if text.startswith(('gut zu wissen','nächster schritt')) else [])
                # Bold an existing plain label without replacing nested Markdown
                # or changing text, punctuation, links or attribution.
                first=quote.find('p')
                if first and first.contents and isinstance(first.contents[0],str):
                    value=str(first.contents[0]);match=re.match(r'^\s*'+re.escape(label.group(0)),value,re.I)
                    if match:
                        strong=soup.new_tag('strong');strong.string=value[:match.end()]
                        first.contents[0].replace_with(strong);strong.insert_after(value[match.end():])
            else:
                quote['class']=['rs-practice-quote']
                if quote.get_text(' ',strip=True).startswith(('„','“','"','»')):
                    quote['class'].append('rs-already-quoted')
        # Preserve the saved wording while giving existing heading groups their
        # page-specific component. Never invent cards, process steps or FAQ text.
        if module.id in {'L04','L09','L07','L14','S09','S11','S13'}:
            groups=soup.find_all('h3',recursive=False)
            grid=soup.new_tag('div',attrs={'class':'rs-card-grid'}) if module.id in {'L04','L09','S09','S11','S13'} and groups else None
            steps=soup.new_tag('ol',attrs={'class':'rs-steps'}) if module.id=='L07' and groups else None
            if groups and (grid is not None or steps is not None):groups[0].insert_before(grid if grid is not None else steps)
            for heading in groups:
                parts=[];following=heading.next_sibling
                while following is not None:
                    if getattr(following,'name',None) in {'h2','h3'} or (hasattr(following,'select_one') and following.select_one('a.rs-cta')):break
                    parts.append(following);following=following.next_sibling
                if module.id=='L14':
                    component=soup.new_tag('details',attrs={'class':'rs-faq'});heading.insert_before(component)
                    summary=soup.new_tag('summary');summary.append(heading.extract());component.append(summary)
                else:
                    component=soup.new_tag('div' if grid is not None else 'li',attrs={'class':'rs-card'} if grid is not None else {})
                    (grid if grid is not None else steps).append(component);component.append(heading.extract())
                for part in parts:component.append(part.extract())
        graphic=''
        if module.id in {'S06a','L04a'}:
            graphic=('<figure class="graphic-placeholder"><strong>Infografik zur manuellen Erstellung</strong><p>'+escape(draft.graphic.title)+
                '</p><p>'+escape(draft.graphic.alt)+'</p></figure>')
        classes=[]
        if module.id=='L01':classes.append('rs-hero')
        if module.id=='L04a':classes.append('rs-signature')
        if module.id=='L05':classes.append('rs-price-section')
        if module.id in {'S18','L16'}:classes.append('rs-social-bar')
        if module.id in {'L15','S17'}:classes.append('rs-final-cta')
        rendered='<div>'+str(soup)+graphic+'</div>'
        if module.id=='L01':
            rendered+='<img class="rs-hero-photo" alt="Das Team von Muster &amp; Partner" src="'+asset_data('team.avif')+'">'
        nodes.append((module.id,'<section id="section-'+module.id+'" data-module="'+module.id+'" class="'+' '.join(classes)+'"><div class="module-copy">'+rendered+'</div></section>'))
    toc_links='<ul>'+''.join('<li><a href="#'+i+'">'+escape(t)+'</a></li>' for i,t in headings)+'</ul>'
    toc_mobile=('<details class="article-toc rs-toc-mobile"><summary>Inhalt anzeigen</summary><nav aria-label="Inhaltsverzeichnis">'+toc_links+'</nav></details>') if blog else ''
    body=''.join(html+(toc_mobile if ident=='S03' else '') for ident,html in nodes)
    if blog:
        body=('<div class="rs-blog-layout"><div class="rs-article-flow">'+body+'</div>'
            '<aside class="rs-toc-sidebar"><nav class="article-toc rs-toc-desktop" aria-label="Inhaltsverzeichnis"><strong>Inhalt</strong>'+toc_links+'</nav></aside></div>')
    graph=[{'@type':'Article' if blog else 'Service','headline':draft.title,'description':draft.meta_description,
        'inLanguage':'de-DE','publisher':{'@type':'Organization','name':'Muster & Partner'}}]
    if not blog:graph[0]['areaServed']='Deutschland'
    graph.append({'@type':'BreadcrumbList','itemListElement':[{'@type':'ListItem','position':1,'name':'Startseite','item':'https://steuerkanzlei.example/'},
        {'@type':'ListItem','position':2,'name':draft.category if blog else draft.title}]})
    # Notices are presentation, never part of the saved German draft or content QA.
    body=brand_header()+ '<main class="rs-content" id="inhalt" tabindex="-1">'+body+'<aside class="disclaimer">'+escape(DISCLAIMER)+'</aside></main>'
    return branded_document(draft.meta_title,body,page_type=draft.page_type,meta=draft.meta_description,
        schema={'@context':'https://schema.org','@graph':graph})
