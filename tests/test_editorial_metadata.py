import json
import pytest
from src.combined.models import EditorialOutput
from src.combined.pipeline import demo_draft
from src.services.editorial_metadata import normalize_meta
from src.services.llm import ModelRouter,ModelRequestError
from src.services.model_routes import resolve_route
from types import SimpleNamespace


def test_metadata_repair_is_lossless_and_invalid_output_is_recoverable(settings):
    keyword='Werbungskosten Vermietung'
    draft=demo_draft(SimpleNamespace(briefing=SimpleNamespace(page_type='blog',primary_keyword=keyword)),settings)
    value=draft.model_dump(mode='json')
    original=value['meta_description']
    value['meta_description']='   '+original+'   '
    result=normalize_meta(value,keyword)
    assert result['meta_description']==original
    assert result['modules']==value['modules'] and result['claims']==value['claims']
    route=resolve_route(settings,'content')
    parsed=ModelRouter._validate(route,EditorialOutput,json.dumps(value),'stop',primary_keyword=keyword)
    assert parsed.meta_description==original
    value['meta_description']='x'*164
    assert normalize_meta(value,keyword)==value
    with pytest.raises(ModelRequestError) as error:
        ModelRouter._validate(route,EditorialOutput,json.dumps(value),'stop',primary_keyword=keyword)
    assert error.value.code=='schema_validation'
    with pytest.raises(ModelRequestError) as error:
        ModelRouter._validate(route,EditorialOutput,json.dumps(value),'length',primary_keyword=keyword)
    assert error.value.code=='output_limit'


def test_valid_metadata_and_unrepresentable_keywords_are_not_rewritten():
    original={'meta_description':'Werbungskosten Vermietung: Informationen zur Orientierung. Bereiten Sie Ihre Unterlagen und Fragen für ein persönliches Beratungsgespräch vor.'}
    assert normalize_meta(original,'Werbungskosten Vermietung') is original
    assert normalize_meta({'meta_description':'x'*170},'y'*160)['meta_description']=='x'*170


def test_local_heading_cleanup_keeps_body_and_does_not_invent_link_targets(settings):
    from src.services.editorial_cleanup import cleanup_formatting
    from src.combined.editorial import rules,markdown
    from src.combined.markdown import parse
    from src.services.image_processing import keyword_slug
    keyword='Werbungskosten Vermietung'
    draft=demo_draft(SimpleNamespace(briefing=SimpleNamespace(page_type='blog',primary_keyword=keyword)),settings)
    draft.title='Werbungskosten bei Vermietung: Ihre Vorbereitung'
    draft.meta_title='Werbungskosten bei Vermietung | Muster'
    draft.modules[0].markdown='# Werbungskosten bei Vermietung: Ihre Vorbereitung\n\nSie ordnen Ihre Unterlagen.'
    draft.modules[1].markdown='## Regelmäßige Kosten\n### Ihr Vorgehen\nSie ordnen Ihre Belege.\n## Weitere Kosten\n### Ihr Vorgehen\nSie prüfen Ihre Fragen.\n[Kosten](#regelmäßige-kosten)\n[LinkedIn](#)'
    draft.modules[2].markdown=('## '+('Langer Kontext '*10)+'\n### Ihr Vorgehen\nSie ordnen Ihre Fragen.\n')*2
    result,changes=cleanup_formatting(draft,keyword,rules(settings,'blog'))
    doc=parse(markdown(result));slugs=[keyword_slug(h.text) for h in doc.headings]
    assert len(slugs)==len(set(slugs))
    assert keyword in result.title and keyword in result.meta_title
    assert 'Sie ordnen Ihre Belege.' in markdown(result)
    assert all(link.url!='#' for link in doc.links)
    assert all(link.url[1:] in slugs for link in doc.links if link.url.startswith('#'))
    assert result.claims==draft.claims and result.unresolved_questions
    again,other=cleanup_formatting(result,keyword,rules(settings,'blog'))
    assert again==result and not other and changes
