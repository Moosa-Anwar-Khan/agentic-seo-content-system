"""Manual infographic attachment. Never generate or publish an image here."""
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image
from src.combined.editorial import release_errors


def attach(workflow, run_id, content, revision):
    if not content or len(content) >= 300_000:
        raise ValueError('Infographic must be a nonempty WebP under 300 KB')
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != 'WEBP' or not 1200 <= image.width <= 1600 or not 1 <= image.height <= 3200:
                raise ValueError('Infographic must be WebP, width 1200-1600 px, height <=3200 px')
            image.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Invalid infographic image') from exc
    with workflow.storage.run_lock(run_id):
        state = workflow.get(run_id)
        if not state.combined_version or state.status != 'seo_check_passed' or state.revision != revision:
            raise ValueError('Graphic requires the current reviewed draft')
        directory = workflow.settings.output_dir/'runs'/run_id/'manual-assets'
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        path = directory/(digest+'.webp')
        path.write_bytes(content)
        state.graphic_file = str(path.resolve())
        state.professional_review = None
        state.graphic_sha256 = digest
        state.graphic_revision = revision
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(state.html, 'html.parser')
        figure = soup.select_one('.graphic-placeholder')
        if figure:
            figure.clear()
            image = soup.new_tag('img', src='data:image/webp;base64,'+base64.b64encode(content).decode())
            image['alt'] = state.editorial['graphic']['alt']
            image['style'] = 'max-width:100%;height:auto'
            figure.append(image)
            caption = soup.new_tag('figcaption')
            caption.string = state.editorial['graphic']['caption']
            figure.append(caption)
        schema = soup.find('script', type='application/ld+json')
        if schema and schema.string:
            value = json.loads(schema.string)
            value['@graph'][0]['image'] = '../assets/'+state.editorial['graphic']['filename']
            schema.string = json.dumps(value,ensure_ascii=False).replace('<','\\u003c')
        state.html = str(soup)
        state.release_blockers = release_errors(state, workflow.settings)
        workflow.graph.update_state(workflow._config(run_id), {'run':state.model_dump(mode='json')})
        workflow.storage.save_run_state(state)
        return state


def copy_approved(state, destination):
    if state.graphic_file and state.status == 'exported':
        content = Path(state.graphic_file).read_bytes()
        if hashlib.sha256(content).hexdigest() != state.graphic_sha256:
            raise ValueError('Approved infographic changed')
        path = destination/'assets'/state.editorial['graphic']['filename']
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        return str(path)
