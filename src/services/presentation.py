"""Current read-only presentation, independent of content QA and saved approvals.

Old state.html and approved exports remain historical records. Current previews
and new downloads render the saved structured content with one brand contract.
"""
import base64
import hashlib
import json
from bs4 import BeautifulSoup
from src.services.editorial_brand import PROFILE_VERSION, editorial_css

WORDPRESS_NOTICE = 'WordPress compatibility not yet verified.'


def content_notes(state):
    """Identify historical graphic wording without silently editing reviewed text."""
    notes=[]
    if state.graphic_file and state.graphic_revision==state.revision and state.editorial:
        for module in state.editorial.get('modules',[]):
            if 'eine Bilddatei liegt diesem Entwurf nicht bei' in module.get('markdown',''):
                notes.append({'module':module['id'],'message':'A graphic is attached, but the reviewed text still says no image is attached. Update this wording explicitly before release.'})
        if 'zur manuellen Erstellung' in state.editorial.get('graphic',{}).get('caption',''):
            notes.append({'module':'graphic_caption','message':'The saved caption still describes a planned graphic. Review it explicitly before release.'})
    return notes


def render_current(state, settings=None):
    if not state.combined_version or not state.editorial:
        return state.html
    from src.combined.models import EditorialOutput
    from src.combined.editorial import render
    html=render(EditorialOutput.model_validate(state.editorial))
    soup=BeautifulSoup(html,'html.parser')
    if state.agent_outputs.get('local_correction') and state.graphic_revision != state.revision:
        figure = soup.select_one('.graphic-placeholder')
        if figure and figure.find('strong'):
            figure.find('strong').string = 'Infografik: Zuordnung und Prüfung für diese Revision stehen noch aus.'
    if state.status!='exported':
        notice=soup.new_tag('aside',attrs={'class':'rs-notice'})
        notice.string='Entwurf — nicht freigegeben · Revision '+str(state.revision)
        soup.body.insert(1,notice)
    if state.graphic_file and settings is not None:
        from src.services.evaluation_vision import attachment
        try:data,_=attachment(settings,state)
        except ValueError:
            notice=soup.new_tag('aside',attrs={'class':'rs-notice'})
            notice.string='Die gespeicherte Infografik ist für diese Revision nicht verifiziert. Bitte prüfen Sie den Anhang.'
            soup.body.insert(1,notice)
        else:
            figure=soup.select_one('.graphic-placeholder')
            if figure:
                figure.clear()
                figure.append(soup.new_tag('img',src='data:image/webp;base64,'+base64.b64encode(data).decode(),alt=state.editorial['graphic']['alt']))
                caption=soup.new_tag('figcaption');caption.string=state.editorial['graphic']['caption'];figure.append(caption)
    return str(soup)


def design_status(state, settings=None):
    """Derived status, not a self-certified WordPress-import receipt."""
    page_type=state.editorial.get('page_type') or (state.article.page_type if state.article else 'planning')
    article_hash=hashlib.sha256(json.dumps(state.editorial or (state.article.model_dump(mode='json') if state.article else {}),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'branding_version':PROFILE_VERSION,'page_type':page_type,'revision':state.revision,
        'branding':{'status':'applied','message':'Page-specific branding applied',
            'profile':page_type,'version':PROFILE_VERSION,
            'scope':'Shared presentation only; content QA and human approvals remain separate.'},
        'content_notes':content_notes(state),
        'fee_presentation':state.fee_presentation.model_dump(mode='json') if state.fee_presentation else None,
        'content_sha256':article_hash,'stylesheet_sha256':hashlib.sha256(editorial_css(page_type).encode()).hexdigest(),
        'wordpress':{'status':'unverified','message':WORDPRESS_NOTICE,'target_template':None,
            'observed_stack':['Astra','Elementor'],
            'reason':'The editable site templates, installed configuration and a representative private import test are unavailable.'},
        'approval':'approved' if state.status=='exported' else 'unapproved',
        'publication':'manual_only'}
