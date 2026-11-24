"""Read-only, explicitly independent assistant observations; never QA/approval input."""
import hashlib
import json
from pathlib import Path
from src.services.evaluation import digest,latest
from src.services.evaluation_vision import attachment


def load(settings,storage,state):
    directory=settings.output_dir/'runs'/str(state.run_id)/'assistant-reviews'/f'revision-{state.revision:03d}'
    try:
        manifest=json.loads((directory/'manifest.json').read_text())
        raw=(directory/'image-review.json').read_bytes()
        page=(directory/'REVIEW.html').read_bytes()
        _,image=attachment(settings,state)
        results=(latest(storage,state,settings) or {}).get('results',{})
        expected={'run_id':str(state.run_id),'revision':state.revision,'article_hash':digest(state.editorial),
            'brief_hash':digest(state.editorial.get('graphic',{})),'image_sha256':image['sha256'],
            'evaluation_results_hash':digest(results),'review_sha256':hashlib.sha256(raw).hexdigest(),
            'html_sha256':hashlib.sha256(page).hexdigest()}
        if manifest!=expected:return None
        review=json.loads(raw)
        if review['run_id']!=str(state.run_id) or review['revision']!=state.revision or review['image_sha256']!=image['sha256']:return None
        # Explicit projection: no raw provider bodies, prompts or local paths.
        return {'review':{'label':'Independent assistant visual review — unapproved',
            'method':review['method'],'reviewed_at':review['reviewed_at'],'revision':state.revision,
            'observations':[{'category':o['category'],'message_en':o['message_en']} for o in review['observations']],
            'limitations':review['limitations'],
            'notice':'This is an independent assistant observation. The configured API image check remains pending. Professional attestation and final approval are unchanged.'},
            'html':page}
    except (OSError,ValueError,KeyError,TypeError):
        return None


def render_current_review(settings,storage,state,record):
    """Present verified historical observations beside current branded outputs.

    The hash-bound historical HTML is preserved by load(); it is not relabelled
    as a current renderer or modified on disk.
    """
    import base64
    from html import escape
    from src.services.editorial_brand import branded_document
    from src.services.presentation import render_current, WORDPRESS_NOTICE
    from src.services.evaluation_images import current as images_current, file as image_file
    review=record['review']
    body='<h1>Your output review</h1><aside>'+escape(review['label'])+' · Revision '+str(state.revision)+'</aside>'
    body+='<p>The original independent observations remain dated; this layout does not perform another review.</p><details><summary>Website import details</summary><p>'+WORDPRESS_NOTICE+' Viewing and downloading remain available.</p></details>'
    body+='<section><h2>Independent observations</h2><p>'+escape(review['notice'])+'</p><ul>'
    body+=''.join('<li>'+escape(o['message_en'])+'</li>' for o in review['observations'])+'</ul></section>'
    body+='<section><h2>German article — unapproved</h2><iframe title="German article — unapproved" sandbox="" style="width:100%;height:900px;border:1px solid #D3D7DF" srcdoc="'+escape(render_current(state,settings),quote=True)+'"></iframe></section>'
    data,_=attachment(settings,state)
    body+='<section id="image"><h2>Infographic — unapproved</h2><figure><img alt="'+escape(state.editorial['graphic']['alt'],quote=True)+'" src="data:image/webp;base64,'+base64.b64encode(data).decode()+'"><figcaption>'+escape(state.editorial['graphic']['caption'])+'</figcaption></figure></section>'
    social=(latest(storage,state,settings) or {}).get('results',{}).get('social_generate',{})
    body+='<section id="social"><h2>Social previews — unapproved</h2>'
    body+=''.join('<h3>'+escape(channel.replace('_',' ').title())+'</h3><pre>'+escape(text)+'</pre>' for channel,text in social.items())+'</section>'
    images=images_current(settings,storage,state)
    for platform,item in (images or {}).get('images',{}).items():
        data,_=image_file(settings,storage,state,platform)
        body+='<section><h2>'+escape(platform.replace('_',' ').title())+' image — unapproved</h2><img alt="'+escape(platform,quote=True)+' evaluation illustration" src="data:'+item['media_type']+';base64,'+base64.b64encode(data).decode()+'"></section>'
    body+='<section><h2>Limitations</h2><ul>'+''.join('<li>'+escape(text)+'</li>' for text in review['limitations'])+'</ul></section>'
    return branded_document('Your output review — unapproved',body,language='en',report=True)
