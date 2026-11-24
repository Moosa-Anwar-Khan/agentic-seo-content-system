"""Conservative, auditable formatting repairs; no factual or evidence invention."""
import re
from urllib.parse import unquote

from src.combined.editorial import markdown, same_page
from src.combined.markdown import parse
from src.services.image_processing import keyword_slug


def cleanup_formatting(draft, keyword, pack):
    draft = draft.model_copy(deep=True)
    changes = []
    renamed={}
    def align(title):
        if keyword.casefold() in title.casefold():return title
        words=keyword.split()
        pattern=r'\b'+r'\s+(?:(?:bei|fuer|für|der|die|das|von|zur|zum)\s+)?'.join(re.escape(word) for word in words)+r'\b'
        return re.sub(pattern,lambda match:keyword,title,count=1,flags=re.I)
    for field in ['title','meta_title']:
        previous=getattr(draft,field);updated=align(previous)
        if updated!=previous and (field!='meta_title' or 20<=len(updated)<=60):
            setattr(draft,field,updated);changes.append({'kind':'keyword_metadata','field':field})
    for module in draft.modules:
        match=re.search(r'^# (.+)$',module.markdown,re.M)
        if match:
            previous=match.group(1);updated=align(previous)
            if updated!=previous:
                module.markdown=module.markdown[:match.start()]+'# '+updated+module.markdown[match.end():]
                renamed[keyword_slug(previous)]=keyword_slug(updated)
                changes.append({'kind':'keyword_h1','module':module.id})
    headings = [heading for heading in parse(markdown(draft)).headings if heading.level == 2]
    missing = max(0, 2 - sum(keyword.casefold() in heading.text.casefold() for heading in headings))
    priorities = ['L04', 'L07', 'L15'] if draft.page_type == 'landing_page' else ['S08', 'S04', 'S10']
    for module_id in priorities:
        if not missing:
            break
        module = next((item for item in draft.modules if item.id == module_id), None)
        if not module:
            continue
        heading = re.search(r'^## (.+)$', module.markdown, re.M)
        if not heading or keyword.casefold() in heading.group(1).casefold():
            continue
        old_title = heading.group(1)
        new_title = keyword + ': ' + old_title
        module.markdown = module.markdown[:heading.start()] + '## ' + new_title + module.markdown[heading.end():]
        old_anchor, new_anchor = keyword_slug(old_title), keyword_slug(new_title)
        renamed[old_anchor]=new_anchor
        for other in draft.modules:
            other.markdown = other.markdown.replace('](#'+old_anchor+')', '](#'+new_anchor+')')
        changes.append({'kind': 'heading_and_anchor', 'module': module.id,
                        'before': old_title, 'after': new_title})
        missing -= 1
    # Repeated subheadings are useful within sections, but each rendered fragment
    # must be unique. Use their actual section context, never fabricated sections.
    used=set()
    for module in draft.modules:
        parent='';lines=[]
        for line in module.markdown.splitlines():
            match=re.fullmatch(r'(#{1,3}) (.+)',line)
            if match:
                level,title=match.groups();old=keyword_slug(title)
                if old in used:
                    context=parent.removeprefix(keyword+': ')
                    updated=(title+' – '+context) if context and level=='###' else title+' – weitere Hinweise'
                    base=updated;counter=2
                    while keyword_slug(updated) in used:
                        updated=f'Weitere Hinweise {counter}: '+base;counter+=1
                    line=level+' '+updated;changes.append({'kind':'unique_heading','module':module.id})
                    title=updated
                used.add(keyword_slug(title))
                if level=='##':parent=title
            lines.append(line)
        module.markdown='\n'.join(lines)
    def fragment(match):
        label,target=match.groups()
        if not target:
            changes.append({'kind':'unconfigured_link_removed'})
            if not any('Kanal' in item or 'Linkziel' in item for item in draft.unresolved_questions):
                draft.unresolved_questions.append('Bitte ergänzen Sie bestätigte Kanal- oder Linkziele vor der Veröffentlichung.')
            return label
        normalized=keyword_slug(unquote(target));normalized=renamed.get(normalized,normalized)
        if normalized in used and normalized!=target:
            changes.append({'kind':'existing_fragment_normalized'})
            return '['+label+'](#'+normalized+')'
        return match.group(0)
    for module in draft.modules:
        module.markdown=re.sub(r'(?<!!)\[([^\]]+)\]\(#([^)]*)\)',fragment,module.markdown)
    if draft.page_type == 'landing_page':
        # Keep hero, core service, prices, process and closing CTAs. Remove only
        # complete standalone redundant links, never service/fee/evidence prose.
        for module_id in ['L09', 'L10', 'L13', 'L05', 'L07']:
            count = sum(same_page(link.url, pack['cta_target']) for link in parse(markdown(draft)).links)
            if count <= 6:
                break
            module = next((item for item in draft.modules if item.id == module_id), None)
            if not module:
                continue
            for line in module.markdown.splitlines():
                match = re.fullmatch(r'\[([^\]]+)\]\((https://[^\s)]+)\)', line.strip())
                if match and same_page(match.group(2), pack['cta_target']):
                    module.markdown = module.markdown.replace(line, '', 1)
                    changes.append({'kind': 'redundant_cta_removed', 'module': module.id})
                    break
    for module in draft.modules:module.markdown=module.markdown.rstrip('\n')
    return draft, changes
