"""Read-only, permission-aware workflow and revision-specific review projection."""
import hashlib
import re
from bs4 import BeautifulSoup, NavigableString
from src.combined.markdown import plain_text

STEPS=[('research','Research',0),('selection','Topic selection',0),('draft','Draft',1),('qa','QA',1),
       ('infographic','Infographic',2),('professional','Professional review',2),('approval','Final approval',2),('export','Export',2)]


def workflow_summary(state,report,actor=None):
    from src.services.professional_review import is_current
    actor=actor or {}; role=actor.get('role');name=actor.get('name')
    draft=bool(state.article);qa=bool(state.brand_check and state.brand_check.passed and state.seo_check and state.seo_check.passed)
    exported=state.status=='exported';graphic=bool(state.graphic_file and state.graphic_revision==state.revision) if state.combined_version else qa
    professional=is_current(state) if state.combined_version else qa
    from src.services.internal_workflow import flags
    internal=flags(state, None) if state.internal_workflow.get('active') else None
    if internal: professional=internal['reviewed'];exported=internal['approved']
    completed=[bool(state.analysis),bool(state.briefing),draft,qa,graphic,professional,exported,internal['exported'] if internal else exported]
    current=next((i for i,done in enumerate(completed) if not done),7)
    drafting=bool(actor and (role=='admin' or state.owner_user_id==actor.get('id') or role=='reviewer' and name in {'Alex','Sam'}))
    final=role in {'admin','reviewer'} and name in {'Alex','Sam'}
    active=bool(report.get('latest_attempt') and report['latest_attempt']['status'] in {'queued','running','paused'})
    ready=report.get('qa_readiness',{}).get('ready',True)
    actions=[('Open research form',False,'The owner starts research from the new-run form.'),
      ('Choose a topic',False,'Choose a supported recommendation below. The owner or final reviewer confirms the selection.'),
      ('View draft',False,'A draft will be saved by the selected drafting job.'),
      ('Review saved draft',False,'Read the highlighted findings before another check.'),
      ('Open infographic upload',False,'Create the image from the saved brief, then upload a WebP. The owner or final reviewer can upload.'),
      ('Open professional review',False,'An authorized tax adviser must review this revision, its sources and infographic.'),
      ('Open final approval',False,'Alex or Sam must give final approval after all release requirements are resolved.'),
      ('Download approved export',False,'Download the approved local package. Publishing remains manual.')]
    label,paid,reason=actions[current];enabled=True;action='navigate'
    if current==3:
        if not drafting:reason='The owner or final reviewer must resolve the QA step.'
        elif not ready:reason=report['qa_readiness']['reason']
        elif any(f.get('display_kind')=='review_error' for f in report.get('findings',[])):
            label='View review error';reason='Your draft is saved. Read the blue notice; the review needs attention. Another paid check requires separate confirmation.'
        elif any(f.get('blocking') and f.get('display_kind')=='correction' for f in report.get('findings',[])):
            reason='Correct the red findings locally, then review the updated check estimate.'
        else:label='Review check estimate';reason='Inspect reusable units and the estimate before explicitly starting paid QA.'
    if current==5 and role!='tax_adviser' and not internal:enabled=False
    if current==6 and not final and not internal:enabled=False
    if current in {1,4} and not drafting:enabled=False
    if active:enabled=False;reason='Work is active or uncertain. Saved progress is retained; inspect its status before submitting anything else.'
    if internal and current>=5:
        enabled=bool(actor and (role=='admin' or state.owner_user_id==actor.get('id'))) and not active
        action='internal';label=['Open internal review','Open final internal approval','Open internal export'][current-5]
        reason='Actual internal review and approval only. Qualified tax-adviser approval and publication approval are not granted.'
    steps=[{'id':key,'label':title,'workspace':workspace,'status':'complete' if completed[i] else 'current' if i==current else 'pending'} for i,(key,title,workspace) in enumerate(STEPS)]
    if internal:
        steps[5]['label']='Internal review';steps[6]['label']='Final internal approval';steps[7]['label']='Internal export'
    return {'steps':steps,'current_step':STEPS[current][0],'workspace':STEPS[current][2],
      'saved_work':'Your saved research, German draft and completed valid checks remain available.' if draft else 'Completed research and jobs remain saved.',
      'primary_action':{'id':action,'label':label,'enabled':enabled,'cost':'Free','explanation':reason},
      'permissions':{'draft':drafting,'professional':role=='tax_adviser','final':final},
      'release_requirements':list(dict.fromkeys(state.release_blockers))}


def norm(s):return ' '.join(s.split())


def location(state,item):
    quote=item.get('quote') or '';module=item.get('module_id')
    if not quote:return {'status':'section_only','module_id':module}
    metadata=item.get('metadata_field')
    for key in ([metadata] if metadata else []):
        if key and norm(quote) in norm(str(state.editorial.get(key,''))):return {'status':'exact','field':key}
    text=next((m['markdown'] for m in state.editorial.get('modules',[]) if m['id']==module),'')
    target=norm(plain_text(quote));body=norm(plain_text(text))
    count=body.count(target) if target else 0
    return {'status':'exact' if count==1 else 'ambiguous' if count>1 else 'not_found','module_id':module,
      'explanation':None if count==1 else 'The quotation cannot be located uniquely. The section and saved quotation are shown instead.'}


def classify(state,findings):
    result=[];seen=set()
    for source in findings:
        item=dict(source)
        if item.get('category')=='release_prerequisite':kind='release'
        elif item.get('category')=='suggestion':kind='suggestion'
        elif item.get('category')=='content_defect':kind='correction'
        else:kind='information'
        identity=(kind,item.get('module_id'),item.get('rule'),item.get('quote') or item.get('message'))
        if identity in seen:continue
        seen.add(identity)
        item.update(display_kind=kind,reviewed_revision=state.revision,
          finding_id=hashlib.sha256(repr((state.revision,identity)).encode()).hexdigest()[:24])
        item['location']=location(state,item)
        result.append(item)
    return result


def preview(state,findings,finding_id=None):
    """Annotate escaped DOM text only. Never change saved HTML or export files."""
    soup=BeautifulSoup(state.html,'html.parser')
    for tag in soup.find_all(['script','iframe','object','embed']):tag.decompose()
    for tag in soup.find_all(True):
        for name in list(tag.attrs):
            if name.lower().startswith('on'):del tag.attrs[name]
    css=soup.new_tag('style');css.string='mark.qa-suggestion{background:#fff0b3;color:#473400}mark.qa-correction{background:#ffe0e0;color:#7d1717}.qa-focus{outline:3px solid #1457a5;outline-offset:4px}html{scroll-behavior:auto}'
    (soup.head or soup).append(css)
    for section in soup.find_all(attrs={'data-module':True}):
        section['id']='section-'+section['data-module']
    for item in findings:
        if item['display_kind'] not in {'suggestion','correction'}:continue
        loc=item['location'];module=loc.get('module_id')
        section=soup.find(attrs={'data-module':module}) if module else None
        if not section and module:section=soup.find(id=module)
        if not section:continue
        if not section.get('id'):section['id']='section-'+module
        if loc['status']!='exact':continue
        target=norm(plain_text(item.get('quote','')))
        # Locate across inline Markdown-rendered elements while preserving markup.
        nodes=[n for n in section.descendants if isinstance(n,NavigableString) and n.parent.name not in {'style','script'}]
        raw='';positions=[]
        for node in nodes:
            for offset,char in enumerate(str(node)):
                if char.isspace():
                    if raw and raw[-1]==' ':continue
                    char=' '
                raw+=char;positions.append((node,offset))
        start=raw.find(target)
        if start<0 or raw.count(target)!=1:continue
        end=start+len(target);groups={}
        for node,offset in positions[start:end]:groups.setdefault(id(node),[node,[]])[1].append(offset)
        for index,(node,offsets) in enumerate(groups.values()):
            text=str(node);lo=min(offsets);hi=max(offsets)+1;mark=soup.new_tag('mark')
            mark['class']=['qa-'+item['display_kind']];mark['title']='Suggestion' if item['display_kind']=='suggestion' else 'Correction required'
            if index==0:mark['id']='finding-'+item['finding_id']
            mark.string=text[lo:hi];node.replace_with(NavigableString(text[:lo]),mark,NavigableString(text[hi:]))
    if finding_id:
        item=next((f for f in findings if f['finding_id']==finding_id),None)
        if item is None:raise KeyError('Finding not found for this revision')
        target=soup.find(id='finding-'+finding_id) or soup.find(id='section-'+str(item.get('module_id')))
        if target:target['class']=[*target.get('class',[]),'qa-focus']
    return str(soup)
