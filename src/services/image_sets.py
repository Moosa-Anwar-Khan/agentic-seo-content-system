from src.services.failures import diagnostic_boundary
import json
from uuid import UUID, uuid5
from pydantic import Field
from src.config.settings import Settings
from src.orchestrator.state import RunState
from src.schemas.assets import ImageAsset, ImageCreative, Platform
from src.schemas.common import StrictModel
from src.services.image_processing import (BADGE_VERSION, decode_image, keyword_slug,
    master_image_path, public_filename)
from src.services.images import ImageModelRouter, ImageProcessingError, ImageProvider, ImageService, image_operation_key
from src.services.llm import ModelRouter
from src.storage.base import BaseStorage
from src.services.recovery import private_result_path

MASTER_SIZE = (2048, 2048)
PLATFORMS: tuple[Platform, ...] = ('website', 'linkedin', 'google_business_profile')


class ImagePrompt(StrictModel):
    prompt: str = Field(min_length=80, max_length=3500)


class ImageMasterError(RuntimeError):
    def __init__(self, provider: str, model: str):
        super().__init__('Image provider returned an invalid master')
        self.provider, self.model = provider, model


class ImageSetService:
    def __init__(self, settings: Settings, storage: BaseStorage,
                 provider: ImageProvider | None = None):
        self.settings, self.storage = settings, storage
        self.image_service = ImageService(settings, storage, provider)
        self.provider = provider

    def _sources(self, state: RunState) -> dict[Platform, tuple[str, str]]:
        return {platform: self.image_service._source(state, platform) for platform in PLATFORMS}

    def _prompt(self, state: RunState, sources: dict[Platform, tuple[str, str]]) -> str:
        brand = json.loads((self.settings.input_dir / 'brand-guidelines/rules.json').read_text(encoding='utf-8'))
        visual = json.loads((self.settings.input_dir / 'brand-guidelines/visual-rules.json').read_text(encoding='utf-8'))
        context = ' '.join(value[1][:550] for value in sources.values())
        fixed = ('Create one professional, restrained visual concept for Muster & Partner, '
                 'a German tax consultancy. Make a high-resolution SQUARE image with the main subject '
                 'inside the central 60 percent. Leave generous safe margins so the same image can be '
                 'cropped to both 3:2 landscape and square without cutting off the subject. '
                 f'Audience: {state.icp.name}; {state.icp.description}. Topic: {state.article.title}. '
                 f'Content context: {context}. Brand palette: {visual["palette"]}. '
                 f'Brand tone: {brand["tone"]}. Visual rules version {visual["version"]}: '
                 f'{visual["style"]} {visual["composition"]}.')
        if self.settings.image_prompt_enabled:
            candidate = ModelRouter(self.settings, self.storage).generate(
                str(state.run_id), ImagePrompt,
                'Write one visually precise image-generation prompt for a reusable square master. '
                'Keep the subject in the central 60 percent and leave room for a landscape crop. '
                'Do not add words, claims, logos, people, or fabricated client details.',
                {'concept': fixed, 'prohibited': visual['prohibited'], 'supplied_source_urls': []},
                model_role='image_prompt')
            fixed += ' Visual concept: ' + candidate.prompt
        return (fixed + f' Avoid: {", ".join(visual["prohibited"])}. '
                'No embedded text, fabricated logo, client data, tax statement, wealth promise, '
                'or identifiable person. Do not draw a badge; the application adds KI-generiert later.')

    @diagnostic_boundary('Image Generation', 'Generate master and channel variants')
    def create(self, state: RunState, png_requested: bool = False,
               parent_id: str | None = None, operation_id: UUID | None = None) -> dict:
        if not self.settings.image_generation_enabled:
            raise ValueError('Image generation is disabled')
        sources = self._sources(state)
        parent = self.storage.get_asset(parent_id, 'master') if parent_id else None
        if parent_id and (not parent or parent.run_id != state.run_id or
                          parent.source_revision != state.revision or
                          parent.content_ids != {p: item[0] for p, item in sources.items()}):
            raise ValueError('Image master is not current for this content')
        existing_master = self.storage.get_asset(str(uuid5(operation_id, 'master')), 'master') if operation_id else None
        if existing_master:
            raw = master_image_path(self.settings, existing_master).read_bytes()
            prompt = existing_master.prompt
            cost = existing_master.generation_cost
            route = None
        else:
            prompt = self._prompt(state, sources)
            raw, cost, route = ImageModelRouter(self.settings, self.storage, self.provider).generate(
                str(state.run_id), prompt, MASTER_SIZE)
        try:
            decoded = decode_image(raw)
            if decoded.image.width != decoded.image.height or min(decoded.image.size) < 1536:
                raise ValueError('Image provider did not return a sufficiently large square master')
        except Exception:
            if route is not None:
                paid_key = image_operation_key(self.settings, route.provider, route.model,
                                               route.fallback, prompt, MASTER_SIZE)
                if paid_key:
                    self.storage.fail_operation(paid_key)
                    private_result_path(self.settings.output_dir, str(state.run_id), paid_key).unlink(missing_ok=True)
            raise ImageMasterError(route.provider if route else existing_master.provider,
                                   route.model if route else existing_master.model) from None
        previous = self.storage.list_assets(str(state.run_id), 'image')
        version = existing_master.version if existing_master else max([i.version for i in previous] + [0]) + 1
        master = existing_master or ImageCreative(**({'id': uuid5(operation_id, 'master')} if operation_id else {}),
            run_id=state.run_id, source_revision=state.revision,
            content_ids={p: item[0] for p, item in sources.items()}, version=version,
            parent_master_id=UUID(parent_id) if parent else None,
            prompt=prompt, provider=route.provider, model=route.model,
            file_path='', original_format=decoded.original_format,
            dimensions=decoded.original_dimensions, file_size=len(raw),
            generation_cost=cost, fallback_used=route.fallback, png_requested=png_requested)
        if not existing_master:
            destination = master_image_path(self.settings, master)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix('.tmp')
            temporary.write_bytes(raw)
            temporary.replace(destination)
            master.file_path = str(destination.relative_to(self.settings.output_dir)).replace('\\', '/')
            self.storage.save_asset(master)
        paid_key = image_operation_key(self.settings, master.provider, master.model,
                                       master.fallback_used, prompt, MASTER_SIZE)
        if paid_key and master_image_path(self.settings, master).is_file():
            private_result_path(self.settings.output_dir, str(state.run_id), paid_key).unlink(missing_ok=True)
        keyword = state.briefing.primary_keyword.strip() if state.briefing else ''
        fallback = not keyword_slug(keyword)
        if fallback:
            keyword = state.article.title
        parent_images = {i.platform: i for i in previous if parent and i.master_id == parent.id}
        variants = []
        for platform in PLATFORMS:
            existing_variant = self.storage.get_asset(str(uuid5(operation_id, platform)), 'image') if operation_id else None
            if existing_variant:
                if existing_variant.optimization_status == 'completed':
                    variants.append(existing_variant)
                    continue
                try:
                    variants.append(self.image_service.retry_processing(state, str(existing_variant.id), decoded))
                except ImageProcessingError:
                    variants.append(self.storage.get_asset(str(existing_variant.id), 'image'))
                continue
            requested = png_requested and platform == 'google_business_profile'
            expected_format = 'png' if requested or (platform == 'google_business_profile' and decoded.has_alpha) else 'jpeg' if platform == 'google_business_profile' else 'webp'
            asset = ImageAsset(**({'id': uuid5(operation_id, platform)} if operation_id else {}),
                run_id=state.run_id, content_id=sources[platform][0],
                source_revision=state.revision, icp_id=state.icp.id, platform=platform,
                prompt=prompt, provider=master.provider, model=master.model, version=version,
                file_path='', generation_cost=cost, badge_version=BADGE_VERSION,
                parent_image_id=parent_images[platform].id if platform in parent_images else None,
                focus_keyword=keyword, focus_keyword_fallback=fallback,
                seo_filename=public_filename(keyword, platform, version, expected_format),
                original_format=decoded.original_format, original_dimensions=decoded.original_dimensions,
                original_file_size=len(raw), final_format=expected_format,
                optimization_status='processing', png_requested=requested, master_id=master.id)
            self.storage.save_asset(asset)
            try:
                asset = self.image_service.retry_processing(state, str(asset.id), decoded)
            except ImageProcessingError:
                asset = self.storage.get_asset(str(asset.id), 'image')
            variants.append(asset)
        return {'master': master, 'variants': variants}

    def list(self, state: RunState) -> list[dict]:
        images = self.storage.list_assets(str(state.run_id), 'image')
        return [{'master': master, 'variants': [i for i in images if i.master_id == master.id]}
                for master in self.storage.list_assets(str(state.run_id), 'master')]

    @diagnostic_boundary('Image Processing', 'Process saved channel variant')
    def retry_variant(self, state: RunState, master_id: str, platform: Platform) -> ImageAsset:
        master = self.storage.get_asset(master_id, 'master')
        if not master or master.run_id != state.run_id:
            raise KeyError('Image master not found')
        matches = [i for i in self.storage.list_assets(str(state.run_id), 'image')
                   if i.master_id == master.id and i.platform == platform]
        if len(matches) != 1:
            raise KeyError('Image variant not found')
        return self.image_service.retry_processing(state, str(matches[0].id))
