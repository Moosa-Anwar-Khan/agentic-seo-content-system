"""Offline website handoff. No WordPress client, hosting or publication.

The generic Elementor HTML-widget candidate is deliberately NOT a verified
site-template export. A private import is required before promising compatibility.
"""
import hashlib
import json
from bs4 import BeautifulSoup
from src.services.editorial_brand import ASSETS, PROFILE_VERSION, asset_data, branded_document, validate_branded_document
from src.services.presentation import WORDPRESS_NOTICE, design_status, render_current


def files(state, settings):
    if not state.combined_version or not state.editorial:
        return {}
    html=render_current(state,settings)
    soup=BeautifulSoup(html,'html.parser')
    status=design_status(state,settings)
    errors=validate_branded_document(html,state.editorial['page_type'])
    if errors:raise ValueError(' '.join(errors))
    approved=state.status=='exported'
    # A scoped content fragment: never import a second site header/footer or
    # change global theme settings. Inline media needs no public image hosting.
    main=soup.select_one('main.rs-content')
    if main is None:
        raise ValueError('Branded content is unavailable for website handoff')
    banner='' if approved else '<aside class="rs-notice">Testentwurf — nicht freigegeben. Nicht veröffentlichen.</aside>'
    fragment=('<style>'+soup.style.string+'</style><div class="'+ ' '.join(soup.body['class'])+
        '" data-content-revision="'+str(state.revision)+'">'+banner+str(main)+'</div>')
    candidate={'title':state.editorial['title']+' — UNVERIFIED',
        'type':'post' if state.editorial['page_type']=='blog' else 'page','version':'0.4','page_settings':{},
        'content':[{'id':'c'+hashlib.sha256(fragment.encode()).hexdigest()[:7],'elType':'container','isInner':False,
            'settings':{'content_width':'full'},'elements':[{
                'id':hashlib.sha256(fragment.encode()).hexdigest()[:8],'elType':'widget','isInner':False,
                'widgetType':'html','settings':{'html':fragment},'elements':[]}]}]}
    guide=('WORDPRESS HANDOFF — '+('CONTENT APPROVED; IMPORT UNVERIFIED' if approved else 'UNAPPROVED TEST OUTPUT')+'\n\n'+WORDPRESS_NOTICE+'\n\n'
        '1. Open preview.html to inspect the styled German page.\n'
        '2. The Elementor JSON is a GENERIC CANDIDATE, not an export of your private site template. Do not import it into the live site yet.\n'
        '3. First test its import in a private copy with the same Astra/Elementor configuration. Check images, fonts, existing title/header/footer and mobile layout.\n'
        '4. The candidate includes styles and inline images. It does not modify global theme settings. Your site may restrict HTML widgets, embedded fonts or data images; those behaviours remain unverified.\n'
        '5. Metadata is supplied separately in metadata.json; SEO-plugin field import is also unverified. No claim of one-click metadata/media installation is made.\n'
        '6. If an import needs layout changes, stop and have the integration corrected; the package is not upload-ready.\n\n'
        'For an existing target URL, replace content only after approval: do not create a duplicate page or change its permalink.\n\n'
        'No automatic upload or publication occurs. Professional attestation and final approval remain required for release.\n')
    target_url=(state.briefing.target_url if state.briefing else None) or (state.article.target_url if state.article else None)
    metadata={k:state.editorial.get(k) for k in ['title','meta_title','meta_description','slug']}
    metadata.update({'target_url':target_url,'preserve_existing_url':bool(target_url)})
    result={'website/preview.html':html,'website/content-fragment.html':fragment,
        'website/elementor-content.UNVERIFIED.json':json.dumps(candidate,ensure_ascii=False,indent=2),
        'website/UPLOAD_INSTRUCTIONS.txt':guide,
        'website/metadata.json':json.dumps(metadata,ensure_ascii=False,indent=2)}
    manifest=json.loads((ASSETS/'manifest.json').read_text())
    media=[]
    for name in manifest['assets']:
        asset_data(name)  # Verify original bytes before packaging them.
        raw=(ASSETS/name).read_bytes()
        result['website/assets/'+name]=raw
        media.append({'path':'assets/'+name,'sha256':hashlib.sha256(raw).hexdigest()})
    result['website/assets/brand-sources.json']=json.dumps(manifest,ensure_ascii=False,indent=2)
    for name in ['cabin-OFL.txt','montserrat-OFL.txt','robotoslab-LICENSE.txt']:
        result['website/assets/'+name]=(ASSETS/name).read_bytes()
    if state.graphic_file:
        from src.services.evaluation_vision import attachment
        try:raw,info=attachment(settings,state)
        except ValueError:status['infographic']='unavailable_or_invalid'
        else:
            name=state.editorial['graphic']['filename'];result['website/assets/'+name]=raw
            media.append({'path':'assets/'+name,'sha256':info['sha256'],
                'alt':state.editorial['graphic']['alt'],'caption':state.editorial['graphic']['caption']})
    status.update({'format':'elementor-html-widget-candidate','upload_ready':False,
        'local_design_checks':{'status':'passed','coverage':'Assembled component structure, shared stylesheet, profile version, offline media and active-content restrictions; browser audit is recorded separately, not an import receipt.'},
        'limitations':['Private site template unavailable.','Import, media retention and SEO-plugin mapping not tested on the target site.'],
        'assets':media,'artifacts':{name:{'sha256':hashlib.sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()}
            for name,value in result.items()}})
    result['export-manifest.json']=json.dumps(status,ensure_ascii=False,indent=2)
    return result


def start_page(state, content, *, unapproved=True):
    title='Test export — unapproved' if unapproved else 'Local export — manual upload'
    heading=('<header><p class="label">Muster &amp; Partner</p><h1>'+title+'</h1></header>'
        '<aside><strong>Revision '+str(state.revision)+'</strong>. '+('Professional attestation and final approval remain pending. ' if unapproved else '')+
        'Nothing is published.</aside><p><strong>Page-specific branding applied · Version '+PROFILE_VERSION+'</strong>. '
        'The app preview and this download use the same renderer.</p><details><summary>Website import details</summary><p><strong>'+WORDPRESS_NOTICE+'</strong> '
        'The package includes an unverified Elementor candidate. Import testing remains separate and does not block previewing or downloading.</p></details>')
    return branded_document(title,heading+content,language='en',page_type='planning',report=True)


def handoff_index(state):
    links='<p><a href="website/preview.html">View the German website layout</a> · <a href="website/UPLOAD_INSTRUCTIONS.txt">Website handoff instructions</a></p>'
    if state.topic_map:links+='<p><a href="topic-maps/topic-map.html">View the topic plan</a> — planned content, not finished additional pages.</p>'
    links+='<p>Approved social text and approved channel images, when available, are included in the social and images folders. Publication stays manual.</p>'
    return start_page(state,links,unapproved=state.status!='exported')
