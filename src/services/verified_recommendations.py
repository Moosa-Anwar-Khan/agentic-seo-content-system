"""Conservative decisions derived from measured rows and quoted public evidence."""
from uuid import uuid5
from src.schemas.decisions import SEORecommendation,LinkSuggestion

ALIASES={
 'immobiliensteuerberater':['steuerberater immobilien','steuerberater für immobilien'],
 'afa immobilien':['abschreibung immobilien','afa rechner immobilien','immobilien afa'],
 'kaufpreisaufteilung immobilie':['kaufpreisaufteilung immobilien','kaufpreisaufteilung'],
 'anlage v vermietung':['anlage v','anlage v vermietung und verpachtung'],
 'immobilienfinanzierung steuern':['immobilien finanzierung steuern'],
}

def recommend(context,state,topic,matches):
    from src.services.seo_decisions import normalize,url_key,page_text
    allowed={normalize(topic['keyword']),*[normalize(v) for v in ALIASES.get(topic['keyword'].casefold(),[])]}
    measured=[(i,k) for i,k in enumerate(state.analysis.keywords) if normalize(k.keyword) in allowed
        and (k.measurement_context.get('country')=='de' or
             k.measurement_context.get('location_code')==2276 and k.measurement_context.get('language_code')=='de')]
    refs=[f'keyword-{i}' for i,k in measured]
    pages={p.url:p for p in state.analysis.page_evidence}
    dedicated=[a for a in matches if a.relation=='dedicated']; mentions=[a for a in matches if a.relation=='mention']
    target=dedicated[0].page_url if dedicated else None
    provisional=not dedicated
    if target is None:
        observed=sorted((k for _,k in measured if k.page and k.position is not None),key=lambda k:-(k.impressions or 0))
        target=next((k.page for k in observed if any(a.page_url==k.page and a.relation=='uncertain' for a in matches) and k.page in pages),None)
    parent=mentions[0].page_url if mentions and not target else None
    draft='optimize' if target else 'create_cluster' if parent else 'create_new'
    action='manual_review';reason='Recherchebedarf: ein konkreter, belegter nächster Schritt fehlt.'
    uncertainty=[];priority=[];complete=state.analysis.coverage.inventory_complete
    demand=any(k.volume is not None and k.volume>0 or k.impressions is not None and k.impressions>=context.settings.seo_min_impressions for _,k in measured)
    page_metrics=[k for _,k in measured if k.page and target and url_key(k.page)==url_key(target) and k.position is not None]
    sufficient=[k for k in page_metrics if (k.impressions or 0)>=context.settings.seo_min_impressions]
    # Existing-page defects require a literal quote and no invented absence or claim of completeness.
    issue=dedicated[0].issue if dedicated else ''
    bad_absence=any(w in issue.casefold() for w in ['fehlt','fehlend','missing','nicht vorhanden','keine erklär','nicht erklärt'])
    supported_issue=bool(issue and not bad_absence and dedicated[0].evidence_quote in issue)
    if target:
        if provisional:
            action='investigate';reason='Gespeicherte GSC-Belege verknüpfen Suchanfrage und vorhandene Seite. Die Ursache der Sichtbarkeit und die Inhaltszuordnung müssen geprüft werden.'
        elif any(1<=k.position<=3 for k in sufficient):
            action='protect';reason='Eine passende bestehende Seite ist bei ausreichend beobachteten Impressionen gut sichtbar. Unnötige Änderungen vermeiden.'
        elif len(dedicated)>1:
            uncertainty.append('Mehrere vorhandene Seiten bedienen einen ähnlichen Bedarf; Zuordnung fachlich prüfen.')
        elif sufficient and supported_issue:
            action='optimize';reason='Ein belegter Prüfpunkt und gemessene Suchnachfrage unterstützen eine gezielte Überarbeitung: '+issue
        elif page_metrics:
            action='investigate';reason='Ranking-Beobachtungen liegen vor. Die Ursache der Sichtbarkeit ist nicht belegt; keine automatische Überarbeitung empfohlen.'
        priority.append('Vorhandene passende Seite zuerst berücksichtigen.')
    elif complete and demand and (mentions or matches and all(a.relation=='none' for a in matches)):
        review=state.research_snapshot.get('coverage_review',{})
        reviewed=topic['id'] in review.get('completed_topic_ids',[]) and review.get('topic_scopes',{}).get(topic['id'],{}).get('includes_all_eligible_pages',False)
        distinct=bool(mentions and mentions[0].distinct_need.strip())
        if reviewed and state.analysis.coverage.content_complete and distinct:
            action='create_cluster';reason='Gemessene Nachfrage und vollständig geprüfte Inhalte unterstützen einen eigenständigen vertiefenden Beitrag: '+mentions[0].distinct_need
        elif reviewed and state.analysis.coverage.content_complete and any(len(a.distinct_need.strip())>=12 for a in matches):
            action='create_new';reason='Gemessene Nachfrage und die vollständig geprüfte Website-Abdeckung unterstützen einen eigenständigen Leserbedarf. Eine passende übergeordnete Seite muss vor dem Entwurf bestätigt werden.'
        else:uncertainty.append('Eigenständigen Leserbedarf und Überschneidungen vor einer neuen Seite fachlich bestätigen.')
    if not demand:uncertainty.append('Ausreichende direkte Nachfragenachweise fehlen; verwandte Themen beweisen diese Nachfrage nicht.')
    if not complete:uncertainty.append('Website-Abdeckung ist unvollständig; fehlende Inhalte sind nicht nachgewiesen.')
    if any(a.relation=='uncertain' for a in matches):uncertainty.append('Mindestens eine Seitenzuordnung bleibt unsicher.')
    if not any('dataforseo' in k.source.casefold() and k.volume is not None for _,k in measured):
        uncertainty.append('DataForSEO-Schätzwerte sind nicht verfügbar; keine Null-Nachfrage daraus ableiten.')
    if state.research_snapshot.get('dataforseo',{}).get('status') not in {None,'usable'}:
        uncertainty.append('DataForSEO-Recherche ist teilweise; ein späterer Endpunktfehler stoppte die Erweiterung.')
    refs += [pages[a.page_url].id for a in matches if a.page_url in pages]
    gsc_impressions=sum(k.impressions or 0 for _,k in measured)
    priority+=['Beobachtete direkte Impressionen: '+str(gsc_impressions), 'Geschätztes Suchvolumen wird nicht über verwandte Keywords addiert.']
    rank={'optimize':0,'create_cluster':1,'create_new':1,'investigate':2,'manual_review':3,'protect':4}[action]
    target_page=pages.get(target)
    eligible=action=='optimize' and demand and len(dedicated)==1 and not any(a.relation=='uncertain' for a in matches)
    eligible=eligible and bool(target_page and target_page.indexable is not False and not target_page.content_truncated)
    if action=='create_cluster':eligible=bool(parent and pages[parent].indexable is not False and not uncertainty)
    blockers=[];next_steps=[]
    if not eligible:
        if not demand:blockers.append('Direkte Nachfrage für dieses konkrete Thema ist noch nicht ausreichend belegt.')
        if target_page and target_page.indexable is False:blockers.append('Die zugeordnete Seite ist ausdrücklich nicht indexierbar; sie bleibt ein Inhaltsbeleg.')
        if target_page and target_page.content_truncated:blockers.append('Der Text der Zielseite enthält noch ungeklärte Auslassungen.')
        if target and not supported_issue:
            blockers.append('Ein konkreter redaktioneller Änderungsbedarf ist nicht belegt. Eine schwache Position allein rechtfertigt keine Neufassung.')
            next_steps.append('Den vollständigen Zielseitentext und die passende Suchintention prüfen; einen konkreten Änderungsbedarf mit wörtlichem Beleg dokumentieren.')
        if not target:
            blockers.append('Eigenständiger Leserbedarf und eine passende bestehende oder übergeordnete Seite sind noch nicht gemeinsam bestätigt.')
            next_steps.append('Bestehende Inhalte und Überschneidungen zuerst fachlich zuordnen; neue Inhalte erst für einen belegten eigenständigen Bedarf planen.')
        if provisional or any(a.relation=='uncertain' for a in matches):blockers.append('Die Zuordnung zwischen Thema und vorhandener Seite ist noch unsicher.')
    links=[]
    destination=target or parent
    if destination and pages[destination].indexable is not False:
        for p in pages.values():
            if p.url==destination or p.indexable is False:continue
            if any(url_key(link)==url_key(destination) for link in p.internal_links):continue
            quote=next((s['text'] for s in p.sections if s.get('kind') in {'p','li'} and not s.get('template') and len(s['text'])>=40
                and any(normalize(word) in normalize(s['text']) for word in ALIASES.get(topic['keyword'].casefold(),[topic['keyword']]))),None)
            if quote:links.append(LinkSuggestion(source_url=p.url,target_url=destination,anchor=topic['title'],reasoning='Belegte thematische Textstelle: '+quote[:180]))
    from src.services.website_evidence import contextual_changes
    changes=contextual_changes(state,target) if target else []
    if changes:
        links=[LinkSuggestion(source_url=c.source_url,target_url=c.target_url,anchor=c.anchor,
                              reasoning=c.reasoning+' Beleg: '+c.evidence_quote) for c in changes]+links
        next_steps.insert(0,'Die belegte kontextuelle Verlinkung zur vorhandenen ICP-Seite prüfen und über „Nur Linkempfehlungen speichern“ vorbereiten.')
    return SEORecommendation(id=str(uuid5(state.run_id,topic['id'])),topic_id=topic['id'],title=topic['title'],keyword=topic['keyword'],
        search_intent=topic['intent'],page_type=topic['page_type'],action=action,draft_action=draft,target_url=target,parent_url=parent,
        evidence_refs=list(dict.fromkeys(refs)),reasoning=reason,uncertainties=uncertainty,internal_link_suggestions=links[:3],
        research_needed=not eligible and action not in {'protect','create_new'},drafting_eligible=eligible,priority=rank*1000000-min(gsc_impressions,999999),
        priority_reasons=priority,measured_keywords=list(dict.fromkeys(k.keyword for _,k in measured)),demo=context.settings.demo_mode,
        supported_changes=changes,links_ready=bool(changes),readiness_blockers=blockers,next_steps=next_steps)
