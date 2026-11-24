from src.services.failures import diagnostic_boundary
import base64
import json
import logging
import time
from abc import ABC, abstractmethod
from uuid import UUID, uuid5
from urllib.parse import urlsplit
import httpx
from src.config.settings import Settings
from src.orchestrator.state import RunState
from src.schemas.assets import ImageAsset, Platform
from src.storage.base import BaseStorage
from src.services.image_processing import (BADGE_VERSION, OUTPUT_SIZES, image_file_path,
    DecodedImage, keyword_slug, master_image_path, process_image, public_filename, raw_image_path)
from src.services.model_routes import (ModelConfigurationError, credential, resolve_route,
    validate_image_route)
from src.schemas.ai import AICall
from src.security.run_access import AccessRevoked
from src.services.recovery import current_job, operation_key, begin_paid, private_result_path, UncertainOperation

SIZES = {'website': ((1536, 1024), OUTPUT_SIZES['website']),
         'linkedin': ((1024, 1024), OUTPUT_SIZES['linkedin']),
         'google_business_profile': ((1024, 1024), OUTPUT_SIZES['google_business_profile'])}

class ImageProcessingError(RuntimeError):
    def __init__(self, image_id: UUID):
        super().__init__('Image post-processing failed')
        self.image_id = image_id


class ImageGenerationError(RuntimeError):
    def __init__(self, provider: str, model: str, code: str):
        super().__init__(f'image_generation: {provider}/{model} failed ({code})')
        self.provider, self.model, self.code = provider, model, code

class ImageProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, size: tuple[int, int]) -> tuple[bytes, float | None]: ...


def image_generation_options(settings: Settings) -> dict:
    """Inspect local configuration without contacting a provider or exposing secrets."""
    configured = True
    reason = None
    try:
        route = resolve_route(settings, 'image_generation')
        validate_image_route(settings, route)
        credential(settings, route, image=True)
        if settings.combined_mode and not settings.demo_mode and settings.image_request_max_usd is None:
            raise ModelConfigurationError('Image request allowance is missing')
    except ModelConfigurationError:
        configured = False
        reason = 'Die Bildgenerierung ist nicht vollständig eingerichtet. Bitte lassen Sie Modell, API-Schlüssel und Budgetreservierung prüfen.'
    if not settings.image_generation_enabled:
        reason = 'Die Bildgenerierung ist in den Einstellungen deaktiviert.'
    return {'enabled': settings.image_generation_enabled, 'configured': configured,
            'demo_mode': settings.demo_mode, 'request_max_usd': settings.image_request_max_usd,
            'reason': reason}


def image_operation_key(settings, provider, model, fallback, prompt, size):
    identity = {'provider': provider, 'model': model, 'fallback': fallback,
                'prompt': prompt, 'size': size}
    if settings.image_quality and provider in {'openai', 'openai_compatible'}:
        identity['quality'] = settings.image_quality
    return operation_key('image', identity)


class OpenAIImageProvider(ImageProvider):
    def __init__(self, settings: Settings, model: str | None = None, key: str | None = None,
                 base: str = 'https://api.openai.com/v1'):
        self.settings, self.model, self.key, self.base = settings, model or settings.image_model, key, base

    def generate(self, prompt: str, size: tuple[int, int]) -> tuple[bytes, float | None]:
        key = self.key or self.settings.openai_api_key.get_secret_value() or self.settings.image_api_key.get_secret_value()
        if not key:
            raise ValueError('IMAGE_API_KEY is required')
        with httpx.Client(timeout=self.settings.image_timeout_seconds, follow_redirects=False) as client:
            response = client.post(self.base.rstrip('/') + '/images/generations',
                headers={'Authorization': 'Bearer ' + key},
                json={'model': self.model, 'prompt': prompt, 'n': 1,
                      'size': f'{size[0]}x{size[1]}',
                      'quality': self.settings.image_quality or ('high' if size == (2048, 2048) else 'medium'), 'output_format': 'png'})
            response.raise_for_status()
            encoded = response.json()['data'][0]['b64_json']
            if len(encoded) > 30_000_000:
                raise ValueError('Image response exceeds size limit')
            return base64.b64decode(encoded, validate=True), None


class GoogleImageProvider(ImageProvider):
    def __init__(self, settings: Settings, model: str, key: str):
        self.settings, self.model, self.key = settings, model, key

    def generate(self, prompt: str, size: tuple[int, int]) -> tuple[bytes, float | None]:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', self.model):
            raise ModelConfigurationError('image_generation: invalid Google model name')
        with httpx.Client(timeout=self.settings.image_timeout_seconds, follow_redirects=False) as client:
            response = client.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent',
                headers={'x-goog-api-key': self.key},
                json={'contents': [{'parts': [{'text': prompt}]}],
                      'generationConfig': {'responseModalities': ['IMAGE'],
                                           'imageConfig': {'aspectRatio': '1:1', 'imageSize': '2K'}}})
            response.raise_for_status()
            parts = response.json()['candidates'][0]['content']['parts']
            images = [p['inlineData']['data'] for p in parts if not p.get('thought')
                      and 'inlineData' in p and p['inlineData'].get('mimeType', '').startswith('image/')]
            if len(images) != 1 or len(images[0]) > 30_000_000:
                raise ValueError('Google image response must contain one valid image')
            return base64.b64decode(images[0], validate=True), None


class CompatibleImageProvider(OpenAIImageProvider):
    def __init__(self, settings: Settings, model: str, key: str, base: str):
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ModelConfigurationError('image_generation: compatible image endpoint requires an HTTPS base URL')
        super().__init__(settings, model, key, base)


class ImageModelRouter:
    def __init__(self, settings: Settings, storage: BaseStorage,
                 provider_override: ImageProvider | None = None):
        self.settings, self.storage, self.provider_override = settings, storage, provider_override

    def _record_call(self, call):
        context=current_job.get()
        if context:
            call.job_id=str(context.job.id)
            call.attempt_id=context.job.attempt_id
            context.details(call_id=str(call.id))
        self.storage.log_ai_call(call)

    def generate(self, run_id: str, prompt: str, size: tuple[int, int]):
        primary = resolve_route(self.settings, 'image_generation')
        fallback = resolve_route(self.settings, 'image_generation', fallback=True)
        routes = [primary] + ([fallback] if fallback else [])
        for index, route in enumerate(routes):
            context = current_job.get()
            if context:
                context.authorize()
                context.details(component='image_generation', operation='Generate master image', provider=route.provider, model=route.model, call_id=None)
            paid_key = image_operation_key(self.settings, route.provider, route.model,
                                           route.fallback, prompt, size)
            raw_path = private_result_path(self.settings.output_dir, run_id, paid_key) if paid_key else None
            if paid_key:
                prior = self.storage.get_operation(paid_key)
                if prior and prior['status'] == 'completed':
                    if raw_path and raw_path.is_file():
                        return raw_path.read_bytes(), prior['value'].get('cost'), route
                    raise UncertainOperation('Stored image result is unavailable')
                if prior and prior['status'] == 'pending':
                    if raw_path and raw_path.is_file():
                        self.storage.finish_operation(paid_key, {'cost': None})
                        return raw_path.read_bytes(), None, route
                    raise UncertainOperation('Image request outcome is unknown')
            if not self.provider_override:
                validate_image_route(self.settings, route)
                key, base = credential(self.settings, route, image=True)
                provider = {
                    'openai': lambda: OpenAIImageProvider(self.settings, route.model, key),
                    'google': lambda: GoogleImageProvider(self.settings, route.model, key),
                    'openai_compatible': lambda: CompatibleImageProvider(self.settings, route.model, key, base),
                }[route.provider]()
            else:
                provider = self.provider_override
            self.storage.reserve_image_attempt(run_id, self.settings.max_image_attempts_per_run)
            currency_reservation = None
            if self.settings.combined_mode and not self.settings.demo_mode:
                from src.services.research_budget import micros
                if self.settings.image_request_max_usd is None:
                    raise ModelConfigurationError('Set a verified IMAGE_REQUEST_MAX_USD before paid image generation')
                currency_reservation = self.storage.reserve_research_cost(
                    run_id, micros(self.settings.image_request_max_usd), micros(self.settings.max_run_usd))
            started = time.monotonic()
            try:
                begin_paid(paid_key, self.storage, run_id)
                raw, cost = provider.generate(prompt, size)
                if currency_reservation and cost is not None:
                    self.storage.settle_research_cost(currency_reservation, micros(cost))
                if raw_path:
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = raw_path.with_suffix('.tmp')
                    temporary.write_bytes(raw)
                    temporary.replace(raw_path)
                    self.storage.finish_operation(paid_key, {'cost': cost})
                self._record_call(AICall(run_id=run_id, task='image_generation',
                    provider=route.provider, model=route.model, image_calls=1, cost=cost,
                    latency_ms=round((time.monotonic()-started)*1000),
                    fallback_used=route.fallback, status='success'))
                return raw, cost, route
            except Exception as exc:
                if isinstance(exc, AccessRevoked):
                    if currency_reservation:
                        self.storage.settle_research_cost(currency_reservation, 0)
                    raise
                if isinstance(exc, UncertainOperation):
                    raise
                if paid_key and (self.storage.get_operation(paid_key) or {}).get('status') == 'completed':
                    raise
                self._record_call(AICall(run_id=run_id, task='image_generation',
                    provider=route.provider, model=route.model, image_calls=1,
                    latency_ms=round((time.monotonic()-started)*1000),
                    fallback_used=route.fallback, status='failure', error_code=type(exc).__name__))
                transient = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or (
                    isinstance(exc, httpx.HTTPStatusError) and
                    (exc.response.status_code in {408, 429} or exc.response.status_code >= 500))
                uncertain = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or (
                    isinstance(exc, httpx.HTTPStatusError) and
                    (exc.response.status_code == 408 or exc.response.status_code >= 500))
                if paid_key and uncertain:
                    raise UncertainOperation('Image request outcome is unknown') from None
                if paid_key:
                    self.storage.fail_operation(paid_key)
                if transient and index + 1 < len(routes):
                    logging.getLogger(__name__).warning('Image fallback from %s/%s',
                                                        route.provider, route.model)
                    continue
                error=ImageGenerationError(route.provider, route.model, type(exc).__name__)
                if isinstance(exc,httpx.HTTPStatusError):error.status_code=exc.response.status_code
                raise error from None
        raise RuntimeError('Image generation failed')

class ImageService:
    def __init__(self, settings: Settings, storage: BaseStorage, provider: ImageProvider | None = None):
        self.settings, self.storage = settings, storage
        self.provider = provider

    def _source(self, state: RunState, platform: Platform) -> tuple[str, str]:
        if self.settings.combined_mode and not self.settings.demo_mode and state.status != 'exported':
            raise ValueError('Approve and export the website revision before image generation')
        if state.status not in {'seo_check_passed', 'exported'} or not state.article or not state.brand_check or not state.brand_check.passed or not state.seo_check or not state.seo_check.passed:
            raise ValueError('The website draft must pass both checks first')
        if platform == 'website':
            return f'{state.run_id}:{state.revision}:website', state.article.title + '. ' + ' '.join(state.article.introduction)[:1200]
        posts = [p for p in self.storage.list_assets(str(state.run_id), 'post')
                 if p.platform == platform and p.source_revision == state.revision]
        if not posts or posts[-1].status == 'rejected':
            raise ValueError('Create a current social post before its image')
        return str(posts[-1].id), posts[-1].text

    def _prompt(self, state: RunState, platform: Platform, content: str) -> str:
        brand = json.loads((self.settings.input_dir / 'brand-guidelines/rules.json').read_text(encoding='utf-8'))
        visual = json.loads((self.settings.input_dir / 'brand-guidelines/visual-rules.json').read_text(encoding='utf-8'))
        composition = {'website': 'Landscape website editorial hero; wide safe space around the focal point.',
                       'linkedin': 'Square professional social image with a clear central visual.',
                       'google_business_profile': 'Square local business update image with simple, well-lit illustration.'}[platform]
        return ('Create a professional image for Muster & Partner, a German tax consultancy. '
                f'Target platform: {platform}. Audience: {state.icp.name}; {state.icp.description}. '
                f'Topic: {state.article.title}. Content context: {content[:1500]}. '
                f'Brand palette: {visual["palette"]}. Brand tone: {brand["tone"]}. '
                f'Visual rules version {visual["version"]}: {visual["style"]} {visual["composition"]} '
                f'{composition} Avoid: {", ".join(visual["prohibited"])}. '
                'No embedded text, logo, tax statement, identifiable person, client data, or promise. '
                'The application adds a small KI-generiert badge later; do not draw one.')

    @diagnostic_boundary('Image Generation', 'Generate channel image')
    def generate(self, state: RunState, platform: Platform, parent_id: str | None = None,
                 png_requested: bool = False, operation_id: UUID | None = None) -> ImageAsset:
        if not self.settings.image_generation_enabled:
            raise ValueError('Image generation is disabled')
        if png_requested and platform != 'google_business_profile':
            raise ValueError('PNG selection is only available for Google Business Profile')
        if operation_id:
            existing = self.storage.get_asset(str(uuid5(operation_id, platform)), 'image')
            if existing:
                return existing if existing.optimization_status == 'completed' else self.retry_processing(state, str(existing.id))
        content_id, content = self._source(state, platform)
        previous = [i for i in self.storage.list_assets(str(state.run_id), 'image')
                    if i.platform == platform]
        parent = None
        if parent_id:
            parent = self.storage.get_asset(parent_id, 'image')
            if not parent or parent.run_id != state.run_id or parent.content_id != content_id or parent.platform != platform:
                raise ValueError('Image version is not current for this content')
        try:
            prompt = self._prompt(state, platform, content)
            raw, cost, route = ImageModelRouter(self.settings, self.storage, self.provider).generate(
                str(state.run_id), prompt, SIZES[platform][0])
            if len(raw) > 20_000_000:
                raise ValueError('Generated image exceeds size limit')
        except (ImageGenerationError, ModelConfigurationError, AccessRevoked):
            raise
        except Exception as exc:
            logging.getLogger(__name__).error('Image generation failed (%s) for run %s', type(exc).__name__, state.run_id)
            raise RuntimeError('Image generation failed') from None
        keyword = state.briefing.primary_keyword.strip() if state.briefing else ''
        fallback = not keyword_slug(keyword)
        if fallback:
            keyword = state.article.title
        version = max((i.version for i in previous), default=0) + 1
        initial_format = 'webp' if platform != 'google_business_profile' else 'png' if png_requested else 'jpeg'
        asset = ImageAsset(**({'id': uuid5(operation_id, platform)} if operation_id else {}),
            run_id=state.run_id, content_id=content_id, source_revision=state.revision,
            icp_id=state.icp.id, platform=platform, prompt=prompt, provider=route.provider,
            model=route.model, version=version, file_path='', generation_cost=cost,
            badge_version=BADGE_VERSION, parent_image_id=UUID(parent_id) if parent else None,
            focus_keyword=keyword, focus_keyword_fallback=fallback,
            seo_filename=public_filename(keyword, platform, version, initial_format),
            final_format=initial_format, original_file_size=len(raw),
            optimization_status='processing', png_requested=png_requested)
        raw_path = raw_image_path(self.settings, asset)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = raw_path.with_suffix('.tmp')
        temporary.write_bytes(raw)
        temporary.replace(raw_path)
        self.storage.save_asset(asset)
        return self.retry_processing(state, str(asset.id))

    @diagnostic_boundary('Image Processing', 'Process saved image')
    def retry_processing(self, state: RunState, image_id: str,
                         decoded: DecodedImage | None = None) -> ImageAsset:
        asset = self.storage.get_asset(image_id, 'image')
        if not asset or asset.run_id != state.run_id:
            raise KeyError('Image not found')
        current_id, _ = self._source(state, asset.platform)
        if asset.content_id != current_id or asset.source_revision != state.revision:
            raise ValueError('Image belongs to an outdated content version')
        if asset.optimization_status not in {'processing', 'failed'} or asset.status != 'pending':
            raise ValueError('Image does not need post-processing')
        master = self.storage.get_asset(str(asset.master_id), 'master') if asset.master_id else None
        if asset.master_id and (not master or master.run_id != state.run_id):
            raise ValueError('Image master is unavailable')
        raw_path = master_image_path(self.settings, master) if master else raw_image_path(self.settings, asset)
        if not raw_path.is_file():
            raise ValueError('Original image is unavailable for retry')
        try:
            processed = process_image(decoded or raw_path.read_bytes(), asset.platform,
                                      asset.png_requested, self.settings,
                                      allow_upscale=not bool(master))
            asset.original_format = processed.original_format
            asset.original_dimensions = processed.original_dimensions
            asset.original_file_size = processed.original_file_size
            asset.final_format = processed.final_format
            asset.final_dimensions = processed.final_dimensions
            asset.optimized_file_size = processed.optimized_file_size
            asset.size_reduction_percent = processed.size_reduction_percent
            asset.seo_filename = public_filename(asset.focus_keyword or '', asset.platform,
                                                 asset.version, processed.final_format)
            destination = image_file_path(self.settings, asset)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix('.tmp')
            temporary.write_bytes(processed.data)
            temporary.replace(destination)
            asset.file_path = str(destination.relative_to(self.settings.output_dir)).replace('\\', '/')
            asset.optimization_status = 'completed'
            asset.optimization_error = None
            self.storage.save_asset(asset)
            if not master:
                try:
                    raw_path.unlink()
                except OSError:
                    logging.getLogger(__name__).warning('Raw image cleanup failed for image %s', image_id)
            return asset
        except Exception as exc:
            logging.getLogger(__name__).error('Image post-processing failed (%s) for image %s', type(exc).__name__, image_id)
            asset.optimization_status = 'failed'
            asset.optimization_error = type(exc).__name__
            self.storage.save_asset(asset)
            raise ImageProcessingError(asset.id) from None

    def review(self, state: RunState, image_id: str, action: str, reviewer: str, reviewer_id: str | None = None) -> ImageAsset:
        if action not in {'approved', 'rejected'}:
            raise ValueError('Unsupported image review action')
        asset = self.storage.get_asset(image_id, 'image')
        if not asset or asset.run_id != state.run_id:
            raise KeyError('Image not found')
        current_id, _ = self._source(state, asset.platform)
        if asset.source_revision != state.revision or asset.content_id != current_id:
            raise ValueError('Image belongs to an outdated content version')
        if asset.status != 'pending':
            raise ValueError('Image has already been reviewed')
        if asset.optimization_status not in {'completed', 'legacy'} or not image_file_path(self.settings, asset).is_file():
            raise ValueError('Image post-processing is incomplete')
        asset.status = action
        asset.reviewed_by = reviewer
        asset.reviewer_id = reviewer_id
        self.storage.save_asset(asset)
        return asset
