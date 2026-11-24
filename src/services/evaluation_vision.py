"""Private, hash-bound image input. No remote image hosting or image generation."""
import base64
import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from src.services.model_routes import capabilities, ModelConfigurationError


def attachment(settings, state):
    if not state.graphic_file or state.graphic_revision != state.revision:
        raise ValueError('Upload an infographic for this revision first.')
    path=Path(state.graphic_file).resolve()
    root=(settings.output_dir/'runs'/str(state.run_id)/'manual-assets').resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('The revision-bound private infographic is unavailable.')
    data=path.read_bytes()
    if len(data)>=300000 or hashlib.sha256(data).hexdigest()!=state.graphic_sha256:
        raise ValueError('The saved infographic hash or size is invalid.')
    with Image.open(BytesIO(data)) as im:
        if im.format!='WEBP' or getattr(im,'is_animated',False):raise ValueError('A static WebP is required.')
        width,height=im.size
        if not 1200<=width<=1600 or not 1<=height<=3200:raise ValueError('Infographic dimensions are invalid.')
    return data,{'sha256':state.graphic_sha256,'width':width,'height':height,'bytes':len(data),'detail':'high'}


def token_bound(settings, route):
    """Do not invent an image-token multiplier for an undocumented model variant.

    An operator may declare a documented conservative bound in the existing
    server-only capability registry, with dated primary-source provenance.
    """
    c=capabilities(settings,route);v=c.get('evaluation_vision_bound',{})
    try:
        age=(date.today()-date.fromisoformat(v['verified_at'])).days
        source=urlsplit(v['source'])
        amount=v['max_image_tokens']
        if not c.get('vision') or route.provider!='openai' or not 0<=age<=30 or source.scheme!='https' or source.hostname not in {'developers.openai.com','platform.openai.com'} or type(amount)!=int or not 1<=amount<=100000 or v.get('detail')!='high':raise ValueError()
    except (ValueError,KeyError,TypeError):
        raise ModelConfigurationError('Image review is pending: a dated, model-specific image-token bound is not verified. An administrator must verify the official high-detail image contract and record evaluation_vision_bound in AI_MODEL_CAPABILITIES. No image request was sent.') from None
    return amount


def with_image(messages, data):
    result=[dict(m) for m in messages]
    result[-1]['content']=[{'type':'text','text':result[-1]['content']},
        {'type':'image_url','image_url':{'url':'data:image/webp;base64,'+base64.b64encode(data).decode(),'detail':'high'}}]
    return result
