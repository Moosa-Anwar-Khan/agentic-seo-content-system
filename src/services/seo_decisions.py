"""Ground semantic topic matching in saved evidence and enforce editorial policy locally."""

import re
import unicodedata
from urllib.parse import urlsplit
from uuid import uuid5
from src.schemas.decisions import (AssessmentBatch, PageAssessment, PageEvidence,
                                  LinkSuggestion, SEORecommendation, SelectionRequest)

ASSESSMENT_PROMPT = (
    'Compare the supplied planned topics with the supplied existing pages. Return assessments only. '
    'Return one main relationship per topic, or at most two when clearly supported. '
    'Keep reasoning under 200 characters and each quote, issue and distinct need under 180 characters. '
    'Dedicated means the full supplied content answers the same search intent; a title or H1 alone is insufficient. '
    'Mention means a brief secondary treatment, with a distinct reader need for a deeper page. '
    'Different intent means overlapping wording but a different reader goal; this is not proven cannibalization. '
    'Use uncertain when evidence is incomplete. Use only supplied topic IDs and page URLs. '
    'For every page relationship quote a literal passage from its supplied title, headings or content. '
    'Identify a specific supported editorial issue when present, without inventing omissions in truncated text. '
    'A WordPress page title is not proof of its SEO meta-title or meta-description; do not claim a metadata defect without supplied metadata. '
    'Explain distinct reader needs and uncertainty in German using Sie. Do not change metrics, select ranking thresholds, '
    'assert legal accuracy, fetch URLs or authorize drafting. Use none without a page URL only when no supplied match exists.'
)


def normalize(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).casefold()
    value = value.replace('ä','ae').replace('ö','oe').replace('ü','ue').replace('ß','ss')
    return ' '.join(re.findall(r'\w+', value))


def url_key(value: str) -> str:
    parsed = urlsplit(value)
    return parsed._replace(fragment='', path=parsed.path.rstrip('/')).geturl()


def snapshot_pages(pages: list[dict], base_url: str) -> list[PageEvidence]:
    result = []
    host = urlsplit(base_url).hostname
    seen = set()
    for page in pages:
        url = page.get('url', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password or url_key(url) in seen:
            continue
        seen.add(url_key(url))
        result.append(PageEvidence(id=f'page-{len(result)}', url=url, title=page.get('title', ''),
                                   headings=page.get('headings', []), content=page.get('excerpt', '')[:12000],
                                   content_truncated=page.get('content_truncated', True),
                                   retrieved_at=page.get('retrieved_at'), modified_at=page.get('modified_at')))
        for field in ['sections','meta_title','meta_description','canonical','indexable','internal_links','content_hash','links_complete','content_exclusions','visibility_signals']:
            if field in page:setattr(result[-1],field,page[field])
    return result


def topics(state) -> list[dict]:
    result = [{'id': state.topic_map.seed_id, 'title': state.topic_map.seed_title,
               'keyword': state.research_snapshot.get('research_audience',{}).get('seed_topic',state.icp.seed_topic), 'intent': 'commercial', 'page_type': 'landing_page'}]
    for pillar in state.topic_map.pillars:
        result.append({'id': pillar.id, 'title': pillar.title, 'keyword': pillar.keyword,
                       'intent': pillar.intent, 'page_type': 'blog' if pillar.intent == 'informational' else 'landing_page'})
        result.extend({'id': child.id, 'title': child.title, 'keyword': child.keyword,
                       'intent': child.intent, 'page_type': 'blog' if child.intent == 'informational' or child.kind=='blog' else 'landing_page'}
                      for child in pillar.clusters)
    if state.research_scope=='site':
        existing={normalize(t['keyword']) for t in result}
        result += [t for t in state.research_snapshot.get('observed_topics',[]) if normalize(t['keyword']) not in existing]
    return result


def page_text(page: PageEvidence) -> str:
    return ' '.join([page.title, *[h.text for h in page.headings], page.content,*[s.get('text','') for s in page.sections]])


def quoted_in(quote: str, text: str) -> bool:
    # Literal wording is required; only HTML layout whitespace (line breaks, indentation) is collapsed.
    return ' '.join(quote.split()) in ' '.join(text.split())


def context_keywords(keywords, seed):
    """Bound model input while retaining the original measured rows in storage."""
    words = set(normalize(seed).split())
    return sorted(keywords, key=lambda k: (0 if 'dataforseo' in k.source.casefold() else 1,
                   -len(words.intersection(normalize(k.keyword).split())), -(k.impressions or 0)))[:50]


def analysis_context(state):
    payload = state.analysis.model_dump(mode='json')
    payload['keywords'] = [k.model_dump(mode='json') for k in context_keywords(state.analysis.keywords, state.icp.seed_topic)]
    payload['existing_pages'] = state.analysis.existing_pages[:150]
    payload['page_evidence'] = [{**p.model_dump(mode='json'), 'content': p.content[:1200],
                              'content_truncated': p.content_truncated or len(p.content) > 1200}
                              for p in state.analysis.page_evidence[:15]]
    payload['recommendations'] = []
    payload['page_assessments'] = []
    payload['context_limits'] = {
        'keyword_rows_sent': len(payload['keywords']), 'keyword_rows_saved': len(state.analysis.keywords),
        'keyword_rows_omitted': max(0, len(state.analysis.keywords) - len(payload['keywords'])),
        'page_excerpts_sent': len(payload['page_evidence']), 'page_excerpts_saved': len(state.analysis.page_evidence),
        'characters_per_page': 1200,
    }
    return payload


class DecisionService:
    def __init__(self, context):
        self.context = context

    def build(self, state, assessments: AssessmentBatch | None = None, *, quarantine_invalid=False) -> list[SEORecommendation]:
        candidates = topics(state)
        pages = state.analysis.page_evidence
        if assessments is None:
            if state.research_scope and not self.context.settings.demo_mode and pages:
                from src.services.research_assessments import assess
                assessments=assess(self,state,candidates,pages)
            elif self.context.settings.demo_mode or not pages:
                assessments = self._demo_assessments(candidates, pages)
            else:
                # Bound provider context without treating omitted content as proof of absence.
                assessments = self.context.llm.generate(
                    str(state.run_id), AssessmentBatch, ASSESSMENT_PROMPT + (
                        ' An issue must include its literal evidence quote inside the issue text. '
                        'Do not assert missing coverage from excerpts. Prefer uncertain rather than inventing a defect. '
                        'Copy evidence_quote verbatim; a paraphrase is not a quotation. '
                        'For relation none set page_url to null and evidence_quote to an empty string.'
                        if state.research_snapshot else ''),
                    {'topics': candidates, 'pages': [{'url': p.url, 'title': p.title,
                     'headings': [h.model_dump() for h in p.headings[:40]], 'content': self._excerpt(p,candidates) if state.research_snapshot else p.content[:2000],
                     'meta_title':p.meta_title,'meta_description':p.meta_description,'canonical':p.canonical,'indexable':p.indexable,
                     'content_truncated': p.content_truncated or len(p.content) > 2000}
                     for p in self._candidate_pages(candidates, pages)],
                     'coverage': state.analysis.coverage.model_dump(mode='json'), 'supplied_source_urls': [p.url for p in self._candidate_pages(candidates, pages)]},
                    model_role='seo_strategy')
        if quarantine_invalid:
            assessments = self._validated_subset(state, candidates, pages, assessments)
        self.validate_assessments(candidates, pages, assessments)
        if state.research_snapshot:
            for item in assessments.assessments:
                narrative=' '.join([item.issue,item.reasoning,item.distinct_need]).casefold()
                if re.search(r'\b(klicks|impressionen|durchschnittsposition|suchvolumen)\b',narrative):
                    raise ValueError('Semantic coverage assessment cannot invent search measurements')
                if item.page_url and re.search(r'fehlt|fehlend|nicht vorhanden|nicht erklärt|nicht behandelt|nicht angeboten|ohne (?:eine|einen|eigenständige|nachvollziehbare)',narrative):
                    item.relation='uncertain';item.issue='';item.distinct_need='';item.reasoning='Eine unbelegte Lücke aus Ausschnitten wurde nicht übernommen; Abdeckung fachlich prüfen.'
        state.analysis.page_assessments = assessments.assessments
        result = [self._recommend(state, topic, [a for a in assessments.assessments if a.topic_id == topic['id']])
                  for topic in candidates]
        if quarantine_invalid:
            validation = state.research_snapshot['assessment_validation']
            affected = set(validation['blocked_topic_ids'])
            for recommendation in result:
                if recommendation.topic_id in affected:
                    recommendation.drafting_eligible = False
                    recommendation.research_needed = True
                    recommendation.readiness_blockers.append('Die KI-Seitenzuordnung wurde wegen fehlender oder ungültiger Belege nicht freigegeben.')
                    recommendation.uncertainties.append('Ungültige KI-Feststellungen wurden ausgeschlossen; die Seitenzuordnung benötigt eine belegte Prüfung.')
                    recommendation.next_steps.append('Die vorhandenen Website-Belege fachlich zuordnen; keine neuen Inhalte aus verworfenen KI-Aussagen ableiten.')
        self.validate_recommendations(state, result)
        state.analysis.recommendations=result
        from src.services.visibility import annotate
        annotate(state)
        return result

    def _validated_subset(self, state, candidates, pages, batch):
        """Quarantine bad model findings without accepting them or discarding measurements.

        The strict validator remains the authority. One bad topic cannot discard
        independent valid findings; every affected or omitted topic stays blocked.
        The original paid response remains unchanged in the recovery archive.
        """
        accepted = []
        rejected = []
        known_ids = {topic['id'] for topic in candidates}
        blocked = set()
        for index, item in enumerate(batch.assessments):
            try:
                self.validate_assessments(candidates, pages, AssessmentBatch(assessments=[*accepted, item]))
                narrative = ' '.join([item.issue, item.reasoning, item.distinct_need]).casefold()
                if re.search(r'\b(klicks|impressionen|durchschnittsposition|suchvolumen)\b', narrative):
                    raise ValueError('Semantic coverage assessment cannot invent search measurements')
            except ValueError as exc:
                rejected.append({'index': index, 'topic_id': item.topic_id, 'reason': str(exc)})
                if item.topic_id in known_ids:
                    blocked.add(item.topic_id)
                continue
            accepted.append(item.model_copy(deep=True))
        missing = known_ids - {item.topic_id for item in accepted}
        blocked.update(missing)
        validation = {'status': 'partial' if rejected or missing else 'verified',
                      'accepted_count': len(accepted), 'rejected_count': len(rejected),
                      'rejected': rejected, 'missing_topic_ids': sorted(missing),
                      'blocked_topic_ids': sorted(blocked)}
        state.research_snapshot['assessment_validation'] = validation
        if rejected or missing:
            warning = ('Die KI-Seitenprüfung enthält nicht belegte oder fehlende Feststellungen. '
                       'Diese wurden nicht übernommen; betroffene Themen bleiben für automatische Entwürfe gesperrt.')
            for limitations in [state.analysis.limitations, state.research_snapshot.setdefault('limitations', [])]:
                if warning not in limitations:
                    limitations.append(warning)
        return AssessmentBatch(assessments=accepted)

    @staticmethod
    def _candidate_pages(candidates, pages):
        words = set(normalize(' '.join(t['keyword'] for t in candidates)).split())
        return sorted(pages, key=lambda p: -len(words.intersection(normalize(page_text(p)).split())))[:20]

    @staticmethod
    def _excerpt(page,candidates):
        words=set(normalize(' '.join(t['keyword'] for t in candidates)).split())-{'fuer','und','bei','finden','immobilien'}
        sections=sorted((s for s in page.sections if not s.get('template')),key=lambda s:-len(words.intersection(normalize(s.get('text','')).split())))
        return ' '.join(s.get('text','') for s in sections[:5])[:3000] or page.content[:3000]

    @staticmethod
    def _demo_assessments(candidates, pages):
        items = []
        for topic in candidates:
            word = normalize(topic['keyword'])
            matches = []
            for page in pages:
                primary = [page.title, *[h.text for h in page.headings if h.level == 1]]
                quote = next((text for text in primary if word in normalize(text)), '')
                relation = 'dedicated' if quote else 'mention'
                if not quote:
                    quote = next((h.text for h in page.headings if word in normalize(h.text)), '')
                if quote:
                    matches.append(PageAssessment(topic_id=topic['id'], page_url=page.url, relation=relation,
                        evidence_quote=quote[:600], distinct_need=('Ein eigenständiger Überblick zu diesem Unterthema.' if relation == 'mention' else ''),
                        reasoning='Demo-Zuordnung aus Beispieldaten; Suchintention und Abdeckung müssen fachlich geprüft werden.'))
            items.extend(matches[:2] or [PageAssessment(topic_id=topic['id'], relation='none',
                                                   reasoning='In den gelieferten Beispieldaten ist keine passende Seite erfasst.')])
        return AssessmentBatch(assessments=items[:100])

    @staticmethod
    def validate_assessments(candidates, pages, batch):
        ids = {t['id'] for t in candidates}
        known = {p.url: p for p in pages}
        seen = set()
        per_topic = {}
        for item in batch.assessments:
            per_topic[item.topic_id] = per_topic.get(item.topic_id, 0) + 1
            if per_topic[item.topic_id] > 2:
                raise ValueError('At most two relationships per topic are supported')
            identity = (item.topic_id, item.page_url)
            if item.topic_id not in ids or identity in seen:
                raise ValueError('Unknown or duplicate assessment topic')
            seen.add(identity)
            if item.relation == 'none':
                if item.page_url or item.evidence_quote:
                    raise ValueError('An absent match cannot cite a page')
            elif item.page_url not in known or not item.evidence_quote.strip() or not quoted_in(item.evidence_quote, page_text(known[item.page_url])):
                raise ValueError('Assessment is not supported by saved page evidence')

    def _recommend(self, state, topic, matches):
        if state.research_snapshot:
            from src.services.verified_recommendations import recommend
            return recommend(self.context,state,topic,matches)
        analysis = state.analysis
        minimum = self.context.settings.seo_min_impressions
        keyword = normalize(topic['keyword'])
        measured = [(index, k) for index, k in enumerate(analysis.keywords) if normalize(k.keyword) == keyword]
        refs = [f'keyword-{i}' for i, _ in measured]
        pages = {p.url: p for p in analysis.page_evidence}
        dedicated = [a for a in matches if a.relation == 'dedicated']
        mentions = [a for a in matches if a.relation == 'mention' and a.distinct_need]
        uncertainty = []
        action, draft_action, target, parent = 'manual_review', 'create_new', None, None
        reason = 'Die Daten reichen für eine belastbare Entscheidung nicht aus. Bitte prüfen Sie das Thema vor der Erstellung.'
        demand = any((k.volume is not None and k.volume > 0) or (k.impressions is not None and k.impressions >= minimum) for _, k in measured)
        complete = (analysis.coverage.inventory_complete and len(self._candidate_pages(topics(state), analysis.page_evidence)) == len(pages)
                    and all(not p.content_truncated and len(p.content) <= 2000 for p in pages.values()))
        if not analysis.coverage.inventory_complete:
            uncertainty.append('Das WordPress-Inventar ist unvollständig; weitere passende Seiten können existieren.')
        if not demand:
            uncertainty.append('Für dieses Keyword fehlt ein ausreichender gemessener Nachfragenachweis.')
        if not analysis.coverage.gsc_complete:
            uncertainty.append('GSC-Abdeckung ist unvollständig oder unbekannt; Durchschnittspositionen gelten nur für die gelieferten Beobachtungen.')
        if any('gsc' in k.source.casefold() and (k.reporting_start is None or k.reporting_end is None) for _, k in measured):
            uncertainty.append('Für mindestens eine GSC-Zeile fehlt der gespeicherte Berichtszeitraum.')
        if self.context.settings.demo_mode:
            uncertainty.append('Demo-Zuordnung: Suchintention und Inhaltsabdeckung wurden nicht durch Live-Recherche geprüft.')
        if dedicated:
            target = dedicated[0].page_url
            draft_action = 'optimize'
            page_metrics = [(i, k) for i, k in measured if 'gsc' in k.source.casefold() and k.page and url_key(k.page) == url_key(target)]
            sufficient = [(i, k) for i, k in page_metrics if k.impressions is not None and k.impressions >= minimum and k.position is not None]
            # A strong query/page match always wins over weaker optimization signals.
            protected = next((a for a in dedicated if any('gsc' in k.source.casefold() and k.page and url_key(k.page) == url_key(a.page_url)
                         and k.impressions is not None and k.impressions >= minimum
                         and k.position is not None and 1 <= k.position <= 3 for _, k in measured)), None)
            if protected:
                target, action = protected.page_url, 'protect'
                reason = 'Eine passende eigenständige Seite erreicht in den gemeldeten Suchanfrage-/URL-Daten eine durchschnittliche Position von 1–3. Wir schützen sie vor unnötiger Überarbeitung.'
            elif len(dedicated) > 1:
                uncertainty.append('Mehrere Seiten behandeln denselben Bedarf; mögliche Überschneidungen müssen geprüft werden.')
            elif any(8 <= k.position <= 20 for _, k in sufficient) and dedicated[0].issue:
                action = 'optimize'
                reason = 'Die Seite hat ausreichend Beobachtungen bei durchschnittlicher Position 8–20. Konkreter Prüfpunkt: ' + dedicated[0].issue
            else:
                uncertainty.append('Ranking oder ein konkret belegter Verbesserungsbedarf fehlt. Niedrige CTR allein rechtfertigt keine Überarbeitung.')
            refs.extend(pages[a.page_url].id for a in dedicated)
        elif mentions:
            parent, draft_action = mentions[0].page_url, 'create_cluster'
            reason = 'Das Thema wird auf einer vorhandenen Seite nur kurz behandelt. Eigenständiger Leserbedarf: ' + mentions[0].distinct_need
            refs.extend(pages[a.page_url].id for a in mentions)
            if complete and demand:
                action = 'create_cluster'
        elif matches and all(a.relation in {'none', 'different_intent'} for a in matches):
            reason = 'In den geprüften Inhalten wurde keine eigenständige Seite für diesen Suchbedarf gefunden. Eine neue Seite wird vorgeschlagen.'
            refs.extend(pages[a.page_url].id for a in matches if a.page_url)
            if complete and demand:
                action = 'create_new'
        else:
            uncertainty.append('Die Suchintention oder die inhaltliche Abdeckung ist nicht eindeutig.')
        if not complete:
            uncertainty.append('Die Abdeckungsprüfung nutzt begrenzte Auszüge; fehlende Inhalte müssen zusätzlich geprüft werden.')
        if any(a.relation == 'uncertain' for a in matches) and action != 'protect':
            action = 'manual_review'
            uncertainty.append('Mindestens eine Seitenzuordnung bleibt unsicher.')
        return SEORecommendation(id=str(uuid5(state.run_id, topic['id'])), topic_id=topic['id'], title=topic['title'],
            keyword=topic['keyword'], search_intent=topic['intent'], page_type=topic['page_type'], action=action,
            draft_action=draft_action, target_url=target, parent_url=parent, evidence_refs=list(dict.fromkeys(refs)),
            reasoning=reason, uncertainties=list(dict.fromkeys(uncertainty)), demo=self.context.settings.demo_mode,
            internal_link_suggestions=self._links(analysis.page_evidence, target or parent, topic['keyword']))

    @staticmethod
    def _links(pages, destination, keyword):
        if not destination:
            return []
        tokens = set(normalize(keyword).split())
        return [LinkSuggestion(source_url=p.url, target_url=destination, anchor=keyword,
                reasoning='Thematisch verwandte Erwähnung; Anker und Einfügestelle müssen redaktionell geprüft werden.')
                for p in pages if p.url != destination and tokens.intersection(normalize(page_text(p)).split())][:5]

    @staticmethod
    def validate_recommendations(state, recommendations):
        known = {p.url for p in state.analysis.page_evidence}
        refs = {p.id for p in state.analysis.page_evidence} | {f'keyword-{i}' for i in range(len(state.analysis.keywords))}
        ids = set()
        for item in recommendations:
            if item.id in ids or item.topic_id not in {t['id'] for t in topics(state)} or not set(item.evidence_refs).issubset(refs):
                raise ValueError('Invalid recommendation evidence or identity')
            ids.add(item.id)
            if any(url and url not in known for url in (item.target_url, item.parent_url)):
                raise ValueError('Recommendation URL was not retrieved from WordPress')
            if item.action in {'protect', 'optimize'} and not item.target_url:
                raise ValueError('Existing-page action requires an existing URL')
            for link in item.internal_link_suggestions:
                if link.source_url not in known or link.target_url not in known or link.source_url == link.target_url:
                    raise ValueError('Internal link suggestion contains an unverified destination')
            if item.supported_changes:
                from src.services.website_evidence import contextual_changes
                expected=[c.model_dump() for c in contextual_changes(state,item.target_url)]
                if any(c.model_dump() not in expected for c in item.supported_changes):
                    raise ValueError('Supported change is not grounded in the complete saved page')
            if item.links_ready!=bool(item.supported_changes):
                raise ValueError('Link readiness requires a verified supported change')


def validate_selection(state, selection: SelectionRequest):
    selection = SelectionRequest.model_validate({field: getattr(selection, field) for field in SelectionRequest.model_fields})
    if state.status != 'awaiting_selection' or selection.expected_revision != state.revision or not state.analysis:
        raise ValueError('Stale or unavailable planning selection')
    if state.research_scope:
        from src.services.visibility import annotate
        annotate(state)
    recommendations = state.analysis.recommendations
    DecisionService.validate_recommendations(state, recommendations)
    chosen = next((r for r in recommendations if r.id == selection.recommendation_id), None)
    if chosen is None:
        raise ValueError('Recommendation is not part of this run')
    if selection.mode=='draft' and not chosen.profile_ready:
        raise ValueError('Configure the matching approved audience/service profile before drafting')
    if state.research_snapshot and selection.mode=='draft' and not chosen.drafting_eligible:
        if not selection.professional_support or len(selection.notes.strip())<40:
            raise ValueError('Research-needed topic requires a documented professional assessment')
    if state.research_snapshot and selection.mode=='draft' and not (chosen.target_url or chosen.parent_url):
        known={p.url for p in state.analysis.page_evidence if p.indexable is not False}
        if selection.verified_parent_url not in known:
            raise ValueError('New content requires a verified parent page selected by the reviewer')
        chosen=chosen.model_copy(update={'parent_url':selection.verified_parent_url,'draft_action':'create_cluster'})
    if selection.mode == 'links_only':
        if not (chosen.target_url or chosen.parent_url) or selection.override_protection:
            raise ValueError('Link recommendations require a verified existing destination')
        return chosen
    if chosen.action == 'protect' and not selection.override_protection:
        raise ValueError('Protected content requires an explicit corrective override')
    if chosen.action != 'protect' and selection.override_protection:
        raise ValueError('Protection override is not applicable')
    if (chosen.action == 'manual_review' or chosen.uncertainties) and not selection.acknowledge_uncertainty:
        raise ValueError('Uncertain evidence requires explicit reviewer acknowledgement')
    return chosen.model_copy(update={'action': chosen.draft_action})


def selected_context(state):
    if state.selected_evidence:
        import copy,hashlib,json
        package=copy.deepcopy(state.selected_evidence)
        signature=package.pop('evidence_hash',None)
        if signature!=hashlib.sha256(json.dumps(package,sort_keys=True,ensure_ascii=False).encode()).hexdigest():
            raise ValueError('Selected research evidence was changed')
        package['evidence_hash']=signature
        return package
    chosen = state.selected_recommendation
    if not chosen or not state.analysis:
        return {}
    metrics = [k.model_dump(mode='json') for i, k in enumerate(state.analysis.keywords) if f'keyword-{i}' in chosen.evidence_refs]
    return {'recommendation': chosen.model_dump(), 'metrics': metrics[:150], 'metrics_truncated': len(metrics) > 150,
            'pages': [p.model_dump(mode='json') for p in state.analysis.page_evidence
            if p.id in chosen.evidence_refs or p.url in {chosen.target_url, chosen.parent_url}],
            'assessments': [a.model_dump() for a in state.analysis.page_assessments if a.topic_id == chosen.topic_id],
            'selection': state.selection_history[-1].model_dump(mode='json') if state.selection_history else None,
            'limitations': state.analysis.coverage.model_dump(mode='json')}


def verified_topic_links(state):
    if not state.analysis:
        return {}
    known = {p.url for p in state.analysis.page_evidence}
    return {r.topic_id: r.target_url for r in state.analysis.recommendations if r.target_url in known}
