"""Printable management report from exactly the dashboard's verified view model."""
from io import BytesIO
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.graphics.shapes import Drawing,Line,String,PolyLine

NAVY=colors.HexColor('#1b2f4a');BLUE=colors.HexColor('#1f5c99');PALE=colors.HexColor('#eef4fc')
LABELS={'clicks':'Klicks','impressions':'Impressionen','ctr':'Klickrate (CTR)','position':'Durchschnittsposition'}

def format_metric(key,value):
    if value is None:return 'Nicht erhoben'
    if key=='ctr':return f'{value*100:.2f} %'
    if key=='position':return f'{value:.1f}'
    return f'{value:,.0f}'.replace(',','.')

def plot(data,key,width=480,height=155):
    drawing=Drawing(width,height);left=48;bottom=28;top=height-22;right=width-10
    values=[r[k] for r in data for k in [key,'previous_'+key] if r[k] is not None]
    if not values:
        drawing.add(String(left,height/2,'Tageswerte wurden für diesen Lauf nicht erhoben.',fontSize=10,fillColor=NAVY));return drawing
    actual=max(max(values),1)
    step=max(1,math.ceil(actual/4));maximum=math.ceil(actual/step)*step
    for value in range(0,maximum+1,step):
        y=bottom+(top-bottom)*value/maximum
        drawing.add(Line(left,y,right,y,strokeColor=colors.HexColor('#dce3ed'),strokeWidth=.5))
        drawing.add(String(left-7,y-3,str(value),fontSize=8,textAnchor='end'))
    for name,color,dashed in [(key,BLUE,False),('previous_'+key,colors.HexColor('#73849b'),True)]:
        segment=[]
        def draw_segment():
            if len(segment)>=4:drawing.add(PolyLine(segment,strokeColor=color,strokeWidth=1.4,strokeDashArray=[3,2] if dashed else None))
            elif segment:drawing.add(Line(segment[0]-1,segment[1],segment[0]+1,segment[1],strokeColor=color,strokeWidth=2))
        for i,row in enumerate(data):
            value=row[name]
            if value is None:draw_segment();segment=[];continue
            segment += [left+(right-left)*i/max(1,len(data)-1),bottom+(top-bottom)*value/maximum]
        draw_segment()
    if data:
        for i,anchor in [(0,'start'),(len(data)//2,'middle'),(len(data)-1,'end')]:
            drawing.add(String(left+(right-left)*i/max(1,len(data)-1),10,data[i]['date'],fontSize=8,textAnchor=anchor))
    drawing.add(String(left,height-9,LABELS[key]+' pro Tag',fontSize=9,fillColor=NAVY))
    return drawing

def create_report(data,language='de'):
    if language=='en':
        from src.services.evidence_report import english_report
        return english_report(data)
    stream=BytesIO();styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='BodyReport',fontName='Helvetica',fontSize=9.5,leading=12.5,spaceAfter=4,textColor=colors.HexColor('#334155')))
    styles.add(ParagraphStyle(name='ReportTitle',fontName='Helvetica-Bold',fontSize=23,leading=27,textColor=NAVY,spaceAfter=8))
    styles.add(ParagraphStyle(name='ReportHeading',fontName='Helvetica-Bold',fontSize=12,leading=16,textColor=NAVY,spaceBefore=10,spaceAfter=6,keepWithNext=True))
    def p(value,style='BodyReport'):return Paragraph(escape(str(value)),styles[style])
    def stamp(value):
        return datetime.fromisoformat(value).astimezone(ZoneInfo('Europe/Berlin')).strftime('%d.%m.%Y %H:%M') if value else 'Nicht erhoben'
    def table(rows,widths=None):
        t=Table([[p(v) for v in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),PALE),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.7,colors.HexColor('#c9d7e8')),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),4)]));return t
    story=[p('SEO-Analyse & Performance','ReportTitle'),p('Muster & Partner | '+data['scope_label']+' | '+data['country_label']),
        p('Datenstand (deutsche Zeit): '+stamp(data['created_at'])+' | Lauf: '+data['run_id'][:8])]
    windows=data['windows']
    if windows:story.append(p('Aktuell: '+' bis '.join(windows['current'])+' | Vorperiode: '+' bis '.join(windows['previous'])))
    story.append(p('Property: '+str(data['property'] or 'nicht erhoben')+' | Run-ID: '+data['run_id']))
    story.append(p('Kurzüberblick','ReportHeading'))
    story.append(p('Dieser Bericht trennt beobachtete Suchleistung von vorgeschlagenen Maßnahmen. '+str(len(data['recommendations']))+
        ' Themenbewertungen sind gespeichert; '+str(sum(r['research_needed'] for r in data['recommendations']))+
        ' benötigen weitere Recherche oder eine dokumentierte fachliche Einschätzung. Bestehende Freigaben bleiben im ursprünglichen Lauf erhalten.'))
    if data.get('report_readiness')=='partial':story.append(p('TEILBERICHT: Quellen und Seitenzuordnungen sind noch unvollständig. Keine automatische Erstellung oder Veröffentlichung.'))
    if data.get('scope_coverage'):
        story.append(p('Erfassung des gewählten Bereichs: '+('vollständig für die definierten Seiten' if data['scope_coverage']['complete'] else 'noch unvollständig')+'. Themenfreigaben werden separat geprüft.'))
    if data.get('overall_report_readiness')=='partial' and data.get('report_readiness')=='ready':
        story.append(p('Die verifizierten ICP-Seiten sind vollständig erfasst. Bei weiteren Website-Inhalten bleiben dokumentierte Textauslassungen bestehen.'))
    story.append(p('Gemessene Suchleistung','ReportHeading'))
    def delta(key):
        change=data['changes'][key]
        if change['absolute'] is None:return 'Nicht verfügbar'
        if key=='ctr':return f"{change['absolute']*100:+.2f} Prozentpunkte"
        if key=='position':return f"{change['absolute']:+.1f} Positionen"
        return f"{change['percent']:+.1f} %" if change['percent'] is not None else 'Null-Basis; kein %'
    story.append(table([['Kennzahl','Aktuell','Vorperiode','Änderung'],*[ [LABELS[k],format_metric(k,data['current'][k]),format_metric(k,data['previous'][k]),delta(k)] for k in LABELS]], [170,95,95,120]))
    story.append(p('Klickrate = Klicks / Impressionen. Niedrigere Durchschnittspositionen bedeuten bessere beobachtete Positionen. Kein Nachweis für Umsatz oder die Ursache einer Veränderung.'))
    for key in ['clicks','impressions']:
        story.extend([Spacer(1,5),plot(data['daily'],key)])
    story.append(p('Blau: aktuelle Periode. Gestrichelt: vorherige gleich lange Periode, nach Berichtstag ausgerichtet. Fehlende Tageswerte bleiben Lücken.'))
    story.append(PageBreak())
    story.append(p('Bestehende Seiten & nächste Schritte','ReportTitle'))
    story.append(p('Seiten mit den meisten gemeldeten Klicks','ReportHeading'))
    if data['top_pages']:
        story.append(table([['Seite','Klicks','Impressionen'],*[[x['title'],x['clicks'],x['impressions']] for x in data['top_pages']]], [330,65,85]))
    else:story.append(p('Seitenstatistiken wurden für diesen Lauf nicht erhoben.'))
    story.append(p('Priorisierte Empfehlungen','ReportHeading'))
    for r in data['recommendations'][:1]:
        story.append(KeepTogether([p(r['title']+' - '+r['action_label'],'ReportHeading'),p(r['reasoning']),
            p('Status: '+('Recherchebedarf' if r['research_needed'] else 'Belegter Prüfpunkt')),
            p('Offene Punkte: '+('; '.join(r['uncertainties']) or 'Konkreter Änderungsbedarf bleibt zu belegen.'))]))
        measured=sorted((m for m in r.get('metrics',[]) if m['impressions'] is not None),key=lambda m:-(m['impressions'] or 0))[:2]
        estimates=[m for m in r.get('metrics',[]) if m['volume'] is not None][:1]
        for m in measured+estimates:
            story.append(p(m['keyword']+' | '+m['source']+' | '+
                ('GSC: '+str(m['impressions'])+' Impressionen, '+str(m['clicks'])+' Klicks, Position '+format_metric('position',m['position'])
                 if m['impressions'] is not None else 'Schätzung: Suchvolumen '+str(m['volume'])+', Schwierigkeit '+str(m['difficulty']))))
        for evidence in r.get('website_evidence',[])[:2]:
            story.append(p('Website-Beleg: „'+evidence['quote']+'“ | '+evidence['url']))
        for change in r.get('supported_changes',[]):
            story.append(p('Belegte Linkmaßnahme','ReportHeading'))
            story.append(p('Vorhandener Text: „'+change['evidence_quote']+'“. Ausgangsseite: '+change['source_url']))
            story.append(p('Ziel: '+change['target_url']+' | Linktext: '+change['anchor']))
            story.append(p(change['reasoning']))
        for blocker in r.get('readiness_blockers',[])[:2]:story.append(p('Entwurfssperre: '+blocker))
    if len(data['recommendations'])>1:
        story.append(p('Weitere priorisierte Prüfpfade','ReportHeading'))
        story.append(table([['Thema','Status / Nachweis'],*[[r['title'],r['action_label']+'; '+
            ('keine direkte Nachfrage belegt' if not r.get('metrics') else '; '.join(
                m['keyword']+': '+str(m['volume'])+' geschätzte Suchen/Monat' for m in r.get('metrics',[])[:1] if m['volume'] is not None))]
            for r in data['recommendations'][1:5]]],[190,290]))
        story.append(p('Diese Prüfpfade sind noch nicht zur Erstellung freigegeben. Vollständige Belege und Unsicherheiten stehen im Dashboard und Empfehlungsbericht.'))
    if not data['recommendations']:story.append(p('Keine Empfehlungen für diesen Lauf gespeichert.'))
    story.append(PageBreak())
    story.append(p('Themenplan, Quellen & Kosten','ReportTitle'))
    story.append(p('Die 6-8 Pillars bleiben erhalten','ReportHeading'))
    if data['pillars']:
        story.append(table([['Pillar','Status'],*[[r['title'],'Recherchebedarf' if r['research_needed'] else 'Belegter Prüfpunkt'] for r in data['pillars']]], [340,140]))
    else:story.append(p('Kein Themenplan gespeichert.'))
    dfs=data['sources']['dataforseo'];website=data['sources']['website']
    story.append(p('Quellenstatus','ReportHeading'))
    story.append(p('GSC: '+('Performance erhoben' if data['performance_available'] else 'Performance nicht erhoben')+' | Website: '+str(website.get('pages_retrieved','unbekannt'))+' Seiten geprüft; Abdeckung '+('vollständig im geprüften Rahmen' if website.get('inventory_complete') else 'unvollständig oder unbekannt')))
    if website.get('resolved_html_urls') is not None:
        story.append(p('URL-Inventar: '+str(website['resolved_html_urls'])+' / '+str(website['discovered_html_urls'])+' HTML-URLs geklärt; '+str(website.get('duplicate_urls',0))+' Alias-URLs; '+str(website.get('external_redirects',0))+' Weiterleitungen zu fremden Websites. Fremde Inhalte wurden nicht abgerufen.'))
    if website.get('content_limitations'):
        story.append(p('Dokumentierte Textauslassungen auf '+str(len(website['content_limitations']))+' Seiten; ausgeschlossenes Material wurde nicht als fehlender Inhalt gewertet.'))
    story.append(p('DataForSEO: '+('Verwendbare Keyword-Daten, aber unvollständige Erweiterung' if dfs.get('usable') and dfs.get('status')!='usable' else 'Verwendbare Keyword-Daten' if dfs.get('usable') else 'Keine verwendbaren Keyword-Daten')+'. '+str(dfs.get('unique_keywords',0))+' verschiedene Keywords; '+str(len(dfs.get('serps',[])))+' SERP-Momentaufnahmen. Schätzwerte sind keine gemessenen Website-Besuche. Kein vollständiger Wettbewerber-Audit.'))
    story.append(p('GSC-Quelle (deutsche Zeit): '+stamp(data['sources']['gsc'].get('retrieved_at'))+' | Aggregation: '+str(data['sources']['gsc'].get('aggregation') or 'unbekannt')))
    dates=[d['retrieved_at'] for d in dfs.get('diagnostics',[]) if d.get('retrieved_at')]
    if dates:story.append(p('DataForSEO erhoben (deutsche Zeit): '+stamp(min(dates))+' bis '+stamp(max(dates))))
    b=data['budget'];story.append(p('Zusätzliche Recherchekosten (USD)','ReportHeading'))
    story.append(p(f"Erfasste Kosten: ${b['recorded_usd']:.6f} | Reserviert/unklar: ${b['reserved_usd']:.6f} | Limit: "+(f"${b['limit_usd']:.2f}" if b['limit_usd'] is not None else 'nicht für diesen historischen Lauf eingerichtet')))
    story.append(p('Verfügbar: '+(f"${b['remaining_usd']:.6f}" if b['remaining_usd'] is not None else 'nicht eingerichtet')))
    if b.get('inherited_research_costs'):
        inherited=b['inherited_research_costs']
        story.append(p('Wiederverwendete Anbieterbelege aus Lauf '+inherited['run_id'][:8]+f"; ursprünglich ${inherited['recorded_microusd']/1000000:.6f} erfasst. Dieser Betrag wurde nicht erneut berechnet."))
    if data['cohort']:
        story.append(p('Bestätigte ICP-Seiten','ReportHeading'))
        for member in data['cohort']:story.append(p(member['url']+' | '+member['reason']))
    story.append(PageBreak())
    story.append(p('Grenzen & Nachvollziehbarkeit','ReportTitle'))
    story.append(p('Was vor einer Umsetzung zu prüfen bleibt','ReportHeading'))
    for limitation in data['limitations']:story.append(p('- '+limitation))
    external=[x for x in website.get('excluded',[]) if x.get('reason')=='external_redirect']
    if external:
        story.append(p('Erfasste externe Weiterleitungen','ReportHeading'))
        story.append(p('Bitte prüfen Sie, ob diese Weiterleitungen beabsichtigt sind. Sie sind kein Nachweis für fehlende Inhalte auf einer eigenen aktuellen Seite.'))
        story.append(table([['Eigene URL','Beobachtetes Ziel'],*[[x['url'],x['destination']] for x in external]],[240,240]))
    noindex=[r for r in data['recommendations'][:5] if any('nicht indexierbar' in b for b in r.get('readiness_blockers',[]))]
    if noindex:
        story.append(p('Vorhandene Inhalte mit Indexierungsausschluss','ReportHeading'))
        for r in noindex:story.append(p(r['title']+': '+str(r.get('target_url') or 'unbekannt')+'. Die Seite zählt als Inhaltsbeleg; eine automatische Umstellung der Indexierung wurde nicht empfohlen.'))
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Helvetica',8);canvas.setFillColor(NAVY)
        canvas.drawString(48,25,'Interner Analysebericht | Keine automatische Veröffentlichung')
        canvas.drawRightString(A4[0]-48,25,str(doc.page));canvas.restoreState()
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=48,rightMargin=48,topMargin=38,bottomMargin=48,
        title='Muster SEO-Analyse',author='Muster & Partner').build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
