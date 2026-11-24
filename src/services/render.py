import json
from html import escape
from src.schemas.content import Article, TopicMap
from src.services.editorial_brand import editorial_css,asset_data,PROFILE_VERSION

def document(title: str, body: str, meta: str = "", schema: dict | None = None, editorial_type: str | None = None) -> str:
    from src.services.editorial_brand import branded_document
    return branded_document(title,body,meta=meta,schema=schema,
        page_type=editorial_type or 'planning',report=not editorial_type)

def topic_titles(topic_map: TopicMap) -> dict[str, str]:
    return {topic_map.seed_id: topic_map.seed_title, **{p.id:p.title for p in topic_map.pillars}, **{c.id:c.title for p in topic_map.pillars for c in p.clusters}}

def render_article(article: Article, topic_map: TopicMap, verified_links: dict | None = None, parent_url: str | None = None) -> str:
    titles = topic_titles(topic_map)
    if not set(article.internal_link_ids).issubset(titles):
        raise ValueError("Article references an unknown topic")
    inquiry = 'https://steuerkanzlei.example/mandant-werden/' if article.primary_keyword else '#beratung'
    label = ('Blogartikel' if article.page_type == 'blog' else 'Leistungsseite') if article.primary_keyword else 'Steuerberatung'
    navigation='<a href="#inhalt">Überblick</a><a href="#weiterlesen">Themen</a><a href="#beratung">Kontakt</a>'
    body='<a class="skip-link" href="#inhalt">Zum Inhalt</a><header class="site-header"><div class="brand-wrap"><a class="brand-logo-link" href="https://steuerkanzlei.example/" aria-label="Muster &amp; Partner Steuerberatung – Startseite"><img class="brand-logo" src="'+asset_data('logo.svg')+'" alt="Muster &amp; Partner Steuerberatung" width="1442" height="191"></a><nav class="site-navigation" aria-label="Hauptnavigation">'+navigation+'</nav><details class="mobile-menu"><summary aria-label="Navigation öffnen">☰ Menü</summary><nav aria-label="Mobile Hauptnavigation">'+navigation+'</nav></details></div></header>'
    photo='office.webp' if article.page_type=='blog' else 'team.avif'
    body+='<header class="editorial-hero"><img class="hero-photo" src="'+asset_data(photo)+'" alt="" width="1536" height="1024"><div class="brand-wrap hero-content"><div><p class="label">Muster &amp; Partner Steuerberatung · '+label+'</p><h1>'+escape(article.title)+'</h1><div class="hero-actions"><a class="button" href="'+inquiry+'">Persönliches Gespräch vorbereiten</a><a class="button button-secondary" href="#inhalt">'+('Artikel lesen' if article.page_type=='blog' else 'Mehr erfahren')+'</a></div><p class="hero-caption">Digital modern und regional verwurzelt</p></div></div></header><main id="inhalt" class="editorial-content '+article.page_type+' '+('blog' if article.page_type=='blog' else 'landing')+'"><section class="intro-section"><div class="brand-wrap"><div class="article-copy">'
    if article.target_url:
        body += '<aside class="revision-note">Überarbeitungsvorschlag für die vorhandene Seite: <a href="' + escape(article.target_url, quote=True) + '">' + escape(article.target_url) + '</a>. Die veröffentlichte Seite bleibt unverändert.</aside>'
    body += ''.join('<p>'+escape(p)+'</p>' for p in article.introduction)
    body += '<nav class="article-toc" aria-label="Inhaltsverzeichnis"><p>In diesem '+('Artikel' if article.page_type=='blog' else 'Überblick')+'</p><ul>' + ''.join('<li><a href="#'+s.id+'">'+escape(s.heading)+'</a></li>' for s in article.sections) + '</ul></nav></div></div></section>'
    for s in article.sections:
        body += '<section class="content-section" id="'+s.id+'"><div class="brand-wrap"><div class="article-copy"><h2>'+escape(s.heading)+'</h2>' + ''.join('<p>'+escape(p)+'</p>' for p in s.paragraphs)
        for sub in s.subsections:
            body += '<h3>'+escape(sub.heading)+'</h3>' + ''.join('<p>'+escape(p)+'</p>' for p in sub.paragraphs)
            if sub.bullets:
                body += '<ul>'+''.join('<li>'+escape(b)+'</li>' for b in sub.bullets)+'</ul>'
        body += '</div></div></section>'
    verified_links = verified_links or {}
    body += '<section id="weiterlesen"><div class="brand-wrap"><div class="article-copy"><h2>Weiterführende Themen</h2><p>Vorhandene Seiten und geplante Inhalte sind getrennt gekennzeichnet.</p><ul class="reading-links">' if article.primary_keyword else '<section id="weiterlesen"><div class="brand-wrap"><div class="article-copy"><h2>Weiterführende Themen</h2><p>Die folgenden Links öffnen den lokalen Themenplan mit den vorgesehenen Vertiefungen.</p><ul class="reading-links">'
    body += ''.join('<li><a href="'+escape(verified_links.get(i, '../topic-maps/topic-map.html#'+i), quote=True)+'">'+escape(titles[i])+'</a><small>' + ('Vorhandene Seite' if i in verified_links else 'Geplantes Ziel') + '</small></li>' for i in article.internal_link_ids) + '</ul>'
    if parent_url:
        body += '<p>Übergeordnetes Thema: <a href="' + escape(parent_url, quote=True) + '">Zur vorhandenen Übersichtsseite</a></p>'
    body += '</div></div></section>'
    body += '<section id="beratung" class="cta-section"><div class="brand-wrap"><div class="article-copy"><h2>'+escape(article.cta_heading)+'</h2><p>'+escape(article.cta_text)+'</p><a class="button button-secondary" href="'+inquiry+'">Persönliches Gespräch vereinbaren</a></div></div></section>'
    body += '<section id="faq"><div class="brand-wrap"><div class="article-copy"><h2>Häufige Fragen</h2>'+''.join('<h3>'+escape(f.question)+'</h3><p>'+escape(f.answer)+'</p>' for f in article.faqs)+'</div></div></section>'
    body += '<section id="quellen"><div class="brand-wrap"><div class="article-copy"><aside class="disclaimer">'+escape(article.disclaimer)+'</aside><h2>Gesetzliche Grundlagen</h2><ul>'
    for url in article.source_urls:
        if url not in {"https://www.gesetze-im-internet.de/estg/__21.html", "https://www.gesetze-im-internet.de/estg/__9.html", "https://www.gesetze-im-internet.de/estg/__7.html", "https://www.gesetze-im-internet.de/estg/__6.html"}:
            raise ValueError("Unapproved legal source URL")
        body += '<li><a href="'+escape(url,quote=True)+'">EStG – '+escape(url.rsplit('/',1)[-1].replace('__','§ ').replace('.html',''))+'</a></li>'
    body += '</ul><p class="muted">Redaktioneller Entwurf. Fachliche Freigabe und Prüfung der Quellen vor Veröffentlichung erforderlich.</p></div></div></section></main><footer class="site-footer"><div class="brand-wrap"><p class="accent">Digital modern und regional verwurzelt</p><p>Muster &amp; Partner Steuerberatung</p></div></footer>'
    page_schema = 'Article' if not article.primary_keyword else 'BlogPosting' if article.page_type == 'blog' else 'WebPage'
    schema = {"@context":"https://schema.org", "@graph":[{"@type":page_schema,"headline":article.title,"description":article.meta_description,"inLanguage":"de-DE","publisher":{"@type":"Organization","name":"Muster & Partner Steuerberatung"}},{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":f.question,"acceptedAnswer":{"@type":"Answer","text":f.answer}} for f in article.faqs]}]}
    # The mobile contact dock reserves its own viewport space rather than covering prose.
    body='<div class="editorial-shell">'+body+'</div><div class="contact-dock"><a class="button floating-cta" href="'+inquiry+'">Persönliches Gespräch</a></div>'
    body=body.replace('<main id="inhalt"','<main tabindex="-1" id="inhalt"',1)
    return document(article.meta_title, body, article.meta_description, schema,editorial_type=article.page_type)

def render_topic_map(topic_map: TopicMap, verified_links: dict | None = None) -> str:
    count = sum(len(p.clusters) for p in topic_map.pillars)
    body = '<header><p class="label">Redaktioneller Themenplan</p><h1>Themenplan</h1></header><aside>Themenplanung — keine fertig erstellten oder freigegebenen Seiten. ' + str(len(topic_map.pillars)) + ' Pillar-Seiten und ' + str(count) + ' vertiefende Beiträge. Messwerte stehen im SEO-Analyseexport; diese Einträge sind geplante Inhalte.</aside>'
    body += '<section id="'+topic_map.seed_id+'"><h2>'+escape(topic_map.seed_title)+'</h2><ul>' + ''.join('<li><a href="#'+p.id+'">'+escape(p.title)+'</a></li>' for p in topic_map.pillars) + '</ul></section>'
    for p in topic_map.pillars:
        body += '<section id="'+p.id+'"><h2>'+escape(p.title)+'</h2><p>Hauptsuchbegriff: '+escape(p.keyword)+'</p><a href="#'+topic_map.seed_id+'">Zur zentralen Leistungsseite</a>'
        for c in p.clusters:
            body += '<div id="'+c.id+'"><h3>'+escape(c.title)+'</h3><p>'+escape({'cluster': 'Unterseite', 'blog': 'Blogbeitrag'}[c.kind])+' · '+escape(c.keyword)+'</p><a href="#'+p.id+'">Zum zugehörigen Pillar</a></div>'
        body += '</section>'
    body += '<h2>Interne Verlinkung</h2><div class="scroll"><table><tr><th>Von</th><th>Zu</th><th>Anker</th></tr>'
    body += ''.join('<tr><td><a href="#'+e.source+'">'+escape(e.source)+'</a></td><td><a href="#'+e.target+'">'+escape(e.target)+'</a></td><td>'+escape(e.anchor)+'</td></tr>' for e in topic_map.links)+'</table></div>'
    if verified_links:
        body += '<h2>Vorhandene Ziele</h2><ul>' + ''.join('<li>' + escape(topic) + ': <a href="' + escape(url, quote=True) + '">' + escape(url) + '</a></li>' for topic, url in verified_links.items()) + '</ul><p>Andere Themen-IDs sind Planungsziele und keine bestätigten Website-Adressen.</p>'
    body += '<h2>Planungsannahmen</h2><ul>'+''.join('<li>'+escape(a)+'</li>' for a in topic_map.assumptions)+'</ul>'
    return document('Themenplan | Muster & Partner', body)


def render_recommendations(state) -> str:
    labels = {'protect': 'Vorhandene Seite schützen', 'optimize': 'Überarbeitung prüfen',
              'create_cluster': 'Vertiefenden Beitrag erstellen', 'create_new': 'Neue Seite erstellen', 'manual_review': 'Manuelle Prüfung erforderlich', 'investigate':'Sichtbarkeit untersuchen'}
    body = '<h1>SEO-Empfehlungen</h1><aside>Redaktionelle Planung. Die Auswahl eines Themas ist keine Freigabe eines Artikels. WordPress bleibt unverändert.</aside>'
    if state.analysis.data_mode == 'demo':
        body += '<p>Demo-Beispiele: keine Live-Recherche und keine neu gemessenen Kennzahlen.</p>'
    if state.selection_history:
        selected = state.selection_history[-1]
        body += '<aside>Bestätigte Auswahl durch ' + escape(selected.reviewer_name) + ': ' + ('Nur Linkempfehlungen' if selected.mode == 'links_only' else 'Entwurf beauftragt') + '. ' + escape(selected.notes) + '</aside>'
    for item in state.analysis.recommendations:
        body += '<section><h2>' + escape(item.title) + '</h2><p>' + labels[item.action] + '</p><p>' + escape(item.reasoning) + '</p>'
        body += '<p>Keyword: ' + escape(item.keyword) + ' · ' + ('Blogartikel' if item.page_type == 'blog' else 'Leistungsseite') + '</p>'
        for index, metric in enumerate(state.analysis.keywords):
            if f'keyword-{index}' in item.evidence_refs:
                body += '<p>Beleg: ' + escape(metric.source) + ' · Impressionen: ' + str(metric.impressions if metric.impressions is not None else 'nicht gemessen') + ' · Klicks: ' + str(metric.clicks if metric.clicks is not None else 'nicht gemessen') + ' · Durchschnittsposition: ' + str(metric.position if metric.position is not None else 'nicht gemessen') + '</p>'
                if metric.reporting_start and metric.reporting_end:
                    body += '<p>Berichtszeitraum: ' + str(metric.reporting_start) + ' bis ' + str(metric.reporting_end) + '</p>'
        for assessment in state.analysis.page_assessments:
            if assessment.topic_id == item.topic_id and assessment.page_url:
                body += '<blockquote>' + escape(assessment.evidence_quote) + '</blockquote><p>' + escape(assessment.reasoning) + '</p>'
        for url in (item.target_url, item.parent_url):
            if url:
                body += '<p>Vorhandene Seite: <a href="' + escape(url, quote=True) + '">' + escape(url) + '</a></p>'
        body += '<ul>' + ''.join('<li>' + escape(note) + '</li>' for note in item.uncertainties) + '</ul>'
        if item.readiness_blockers:
            body += '<h3>Warum ist die Erstellung gesperrt?</h3><ul>'+''.join('<li>'+escape(note)+'</li>' for note in item.readiness_blockers)+'</ul>'
        if item.next_steps:
            body += '<h3>Nächste Schritte</h3><ul>'+''.join('<li>'+escape(note)+'</li>' for note in item.next_steps)+'</ul>'
        for change in item.supported_changes:
            body += '<h3>Belegte Linkmaßnahme</h3><blockquote>'+escape(change.evidence_quote)+'</blockquote><p>'+escape(change.reasoning)+'</p>'
        for link in item.internal_link_suggestions:
            body += '<p>Linkvorschlag: <a href="' + escape(link.source_url, quote=True) + '">' + escape(link.source_url) + '</a> → <a href="' + escape(link.target_url, quote=True) + '">' + escape(link.anchor) + '</a> · ' + escape(link.reasoning) + '</p>'
        body += '</section>'
    body += '<h2>Datengrenzen</h2><ul>' + ''.join('<li>' + escape(note) + '</li>' for note in state.analysis.limitations) + '</ul>'
    return document('SEO-Empfehlungen | Muster & Partner', body)
