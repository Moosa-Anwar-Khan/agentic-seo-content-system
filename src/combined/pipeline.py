"""Adapters that join the two projects without a second orchestrator."""
from src.combined.evidence import source_issue as source_eligibility_issue
from src.services.link_policy import contract as link_contract
from src.services.review_contract import contract as review_contract
from src.combined.models import EditorialOutput, Module, GraphicBrief
from src.combined.editorial import rules, prompt_rules, structural_errors, link_errors, as_article, render, release_errors
from src.combined.research import prompt_packet
from src.schemas.content import CheckResult


def demo_draft(state, settings):
    """Explicit placeholders demonstrate the full journey, never factual approval."""
    page_type = state.briefing.page_type
    pack = rules(settings,page_type)
    ids = ([m['id'] for m in pack['modules'] if m.get('required') and not m.get('template')]
           if page_type == 'blog' else pack['matrix']['landing:industry']['R'])
    keyword = state.briefing.primary_keyword
    modules = []
    for index, module_id in enumerate(ids):
        name = next(m['name'] for m in pack['modules'] if m['id'] == module_id)
        body = ('# '+keyword+' - Fragen für Ihre Beratung' if index == (1 if page_type == 'blog' else 0)
                else '## '+name)+'\n\nDemo-Platzhalter: Dieser Abschnitt wird im Live-Modus anhand der freigegebenen Recherche erstellt. Sie erhalten hier keine steuerliche Aussage.'
        modules.append(Module(id=module_id,markdown=body))
    description = (keyword+': Bereiten Sie Ihre Fragen zu vermieteten Immobilien vor. Vereinbaren Sie ein kostenfreies Erstgespräch mit Muster & Partner.')
    description = (description+' Persönlich und strukturiert.')[:155]
    if len(description)<140:
        description += ' '*(140-len(description))
    from src.services.image_processing import keyword_slug
    return EditorialOutput(title=keyword+' - Fragen für Ihre Beratung',meta_title=(keyword+' | Muster & Partner')[:60],
        meta_description=description,slug=keyword_slug(keyword),page_type=page_type,modules=modules,
        graphic=GraphicBrief(title='Vorbereitung auf das Erstgespräch',facts=['Fragen sammeln','Unterlagen ordnen','Termin vereinbaren','Individuellen Fall besprechen'],
        notebooklm_prompt='Erstellen Sie eine deutsche Infografik zur Vorbereitung auf das Erstgespräch. Demo; fachlich prüfen.',
        alt='Infografik: Fragen und Unterlagen für das Erstgespräch vorbereiten.',caption='Demo-Grafikbriefing, keine geprüften Steuerinformationen.',filename=keyword_slug(keyword)+'-infografik-vorbereitung.webp'),
        signature_element='Individuelle Checkliste zur Vorbereitung auf Ihr Gespräch.',
        unresolved_questions=['Demo benötigt Live-Recherche, vollständig ausgearbeitete Inhalte und fachliche Prüfung.'])


def content(context,state):
    settings = context.settings
    if settings.demo_mode:
        draft = demo_draft(state,settings)
    else:
        from src.combined.research import prepare_brief
        if not state.research_packet.get('official_sources'):
            prepare_brief(context,state)
        from src.services.preflight import ensure_ready
        ensure_ready(context,state,'content')
        from src.services.seo_decisions import selected_context
        known_defects = []
        if state.editorial:
            previous = EditorialOutput.model_validate(state.editorial)
            from src.combined.evidence import claim_errors
            known_defects = list(dict.fromkeys(
                structural_errors(previous, state.briefing.primary_keyword, rules(settings, previous.page_type)) +
                claim_errors(previous, state.research_packet.get('official_sources', []), settings.tax_year) +
                link_errors(state, settings)))
        draft = context.llm.generate(str(state.run_id),EditorialOutput,
            'Write the complete German page, using the selected keyword and page type unchanged. '
            'Every required module needs its exact marker ID and substantial Markdown. '
            'Follow the effective_policy before any conflicting preserved guideline text; '
            'follow the supplied blog/landing rules and choose the landing subtype when needed. '
            'The supplied internal_link_policy overrides ONLY conflicting internal-link quantities in the preserved guideline; apply it consistently. '
            'For every tax assertion include a SupportedClaim with a literal source passage, '
            'applicable tax year and conditions. Do not infer that retrieval proves applicability. '
            'Competitor analysis and examples guide coverage/style only. Use only retrieved official '
            'sources for tax facts. Never invent quotes, prices, reviews or company services. '
            'Omit L12 and hero ratings when genuine approved reviews are unavailable. '
            'Follow review_contract.fee_presentation when supplied: an approved_wording_only exception replaces numeric-price placeholders with approved fee wording in L05/L14. Otherwise use [PREIS FEHLT] for unapproved prices. Use unresolved_questions for missing expertise. '
            'Internal links must be verified existing URLs. Missing link targets are editorial '
            'blockers, never made-up pages. H2 anchors are generated by code using ASCII keyword slugs. '
            'Supply a NotebookLM brief for the mandatory infographic; never claim the image exists. '
            'For landing pages include a distinct useful signature element. '
            'For blog output use article_kind=guide and write 2000-3500 German words, targeting the briefing length. '
            'Blog S08 needs 3-7 H2 core sections, S10 at least two H2 questions, and S06/S14 each need 5-8 list entries. '
            'All reader values German, formal Sie, firm voice wir. No raw HTML. '
            'Revisions must address the supplied feedback and return the complete updated draft.',
            {'review_contract':review_contract(state,settings),
             'briefing':state.briefing.model_dump(),'selected':selected_context(state),
             'internal_link_policy':link_contract(state),
             'rules':prompt_rules(settings,state.briefing.page_type),'evidence':prompt_packet(state),
             'previous':state.editorial or None,'feedback':state.human_feedback,
             'known_defects_to_correct': known_defects,
             'output_contract': {
                 'claims': 'Every SupportedClaim.text must occur verbatim in modules[].markdown. The quote must occur verbatim in its identified official source. Do not add claim summaries absent from the page.',
                 'headings': 'Use the unchanged primary keyword in at least two H2 headings. Fragment links must match the ASCII slug of an actual heading; never assume #ablauf exists.',
                 'faq': ('Put six to eight question headings in L14, using ### followed by a question ending in ?. Do not invent guide URLs.' if state.briefing.page_type=='landing_page' else 'Use the supplied S modules in order. S10 needs at least two H2 headings ending in ?. Do not use L modules in a blog.'),
                 'cta': ('Use four to six links to the existing enquiry page in total. Tracking query parameters are allowed but each link still counts.' if state.briefing.page_type=='landing_page' else 'Use the existing enquiry destination and verified internal links; preserve the mandatory blog modules and disclaimer.'),
                 'missing_inputs': 'Do not invent prices, reviews, attributed statements or internal URLs. Keep missing approvals explicit; they cannot be solved by an AI rewrite.',
             },
             'verified_internal_pages': [{'url': p.url, 'title': p.title}
                                         for p in (state.analysis.page_evidence if state.analysis else [])],
             'supplied_source_urls':[s['url'] for s in state.research_packet.get('official_sources',[]) if not source_eligibility_issue(s,settings.tax_year)]},
            model_role='content')
    if draft.page_type != state.briefing.page_type:
        raise ValueError('Draft changed the selected page type')
    if not settings.demo_mode:
        from src.services.editorial_cleanup import cleanup_formatting
        draft, edits = cleanup_formatting(draft, state.briefing.primary_keyword, rules(settings, draft.page_type))
        state.agent_outputs['local_formatting'] = {'changes': edits}
    from src.combined.evidence import normalize_source_quotes
    state.agent_outputs['source_quote_normalization'] = {
        'changes': normalize_source_quotes(draft, state.research_packet.get('official_sources', []))}
    state.editorial = draft.model_dump(mode='json')
    state.article = as_article(draft,state.briefing.primary_keyword)
    state.article.target_url = state.briefing.target_url
    state.html = render(draft)
    state.brand_check = state.seo_check = None
    state.qa_manifest = {}
    state.graphic_file = '';state.graphic_sha256 = '';state.graphic_revision = -1
    state.professional_review = None
    state.publication = {}
    state.status = 'draft_created'
    state.release_blockers = release_errors(state,settings)
    return state


def brand(context,state):
    draft = EditorialOutput.model_validate(state.editorial)
    errors = structural_errors(draft,state.briefing.primary_keyword,rules(context.settings,draft.page_type))
    if not context.settings.demo_mode:
        from src.combined.evidence import claim_errors
        # Give the repair step all deterministic defects together, before another
        # paid rewrite. Missing company approvals remain separate release gates.
        errors += claim_errors(draft, state.research_packet.get('official_sources', []), context.settings.tax_year)
        errors += link_errors(state, context.settings)
    warnings = []
    if context.settings.demo_mode:
        warnings = ['Demo-Platzhalter zeigen den Ablauf; sie sind nicht veröffentlichbar.',*errors]
        errors = []
    state.brand_check = CheckResult(passed=not errors,errors=errors,warnings=warnings)
    if not errors:state.status='brand_check_passed'
    return state


def seo(context,state):
    from src.combined.evidence import claim_errors
    draft=EditorialOutput.model_validate(state.editorial)
    errors=claim_errors(draft,state.research_packet.get('official_sources',[]),context.settings.tax_year)
    warnings=[];findings=[]
    if not context.settings.demo_mode and not errors:
        from src.services.qa_units import review
        check=review(context,state)
        errors+=check.errors;warnings+=check.warnings;findings+=check.findings
        if not check.passed and not check.errors:
            errors.append('Fachliche Prüfung hat den Entwurf nicht bestätigt.')
    state.release_blockers=release_errors(state,context.settings)
    warnings+=state.release_blockers
    state.seo_check=CheckResult(passed=not errors,errors=errors,warnings=warnings,findings=findings)
    if not errors:state.status='seo_check_passed'
    return state


def require_release(settings,state,event, *, allow_correction=False):
    if event.action == 'needs_revision' and allow_correction:
        return
    if event.reviewer_name not in {'Alex','Sam'}:
        raise PermissionError('Final approval is restricted to Alex or Sam')
    if event.action!='approved' or not state.combined_version:
        return
    errors=release_errors(state,settings)
    if errors:
        raise ValueError('Release is blocked: '+'; '.join(errors))
    if not (event.factual_review_confirmed and event.graphic_review_confirmed and event.manual_upload_confirmed):
        raise ValueError('Factual review, graphic review and manual-upload confirmation are required')
    from src.services.professional_review import is_current
    if not is_current(state):
        raise ValueError('A current authenticated professional review is required')
    import hashlib
    from pathlib import Path
    path=Path(state.graphic_file)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=state.graphic_sha256:
        raise ValueError('Approved graphic is missing or changed')
