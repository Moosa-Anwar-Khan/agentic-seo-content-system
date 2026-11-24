import json
import os
from dataclasses import dataclass
from src.config.settings import Settings

os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
os.environ.setdefault('LITELLM_MODE', 'PRODUCTION')

TEXT_TASKS = ('seo_strategy', 'topic_map', 'briefing', 'content', 'brand',
              'seo_qa', 'social_content', 'image_prompt')
PROVIDER_PREFIX = {'google': 'gemini', 'openai_compatible': 'openai'}


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ModelRoute:
    task: str
    provider: str
    model: str
    fallback: bool = False
    legacy: bool = False

    @property
    def litellm_model(self) -> str:
        prefix = PROVIDER_PREFIX.get(self.provider, self.provider)
        if self.provider == 'openai_compatible':
            return f'openai/{self.model}'
        return self.model if self.model.startswith(prefix + '/') else f'{prefix}/{self.model}'


def legacy_text_model(settings: Settings, task: str) -> str:
    return {
        'seo_strategy': settings.llm_model_planning,
        'topic_map': settings.llm_model_planning,
        'briefing': settings.llm_model_briefing or settings.llm_model_drafting,
        'content': settings.llm_model_drafting,
        'brand': settings.llm_model_brand or settings.llm_model_planning,
        'seo_qa': settings.llm_model_planning,
        'social_content': settings.llm_model_planning,
        'image_prompt': settings.llm_model_planning,
    }[task]


def resolve_route(settings: Settings, task: str, fallback: bool = False) -> ModelRoute | None:
    if task != 'image_generation' and task not in TEXT_TASKS:
        try:
            configured = json.loads(settings.ai_task_routes)
            item = configured[task]
            if not isinstance(item, dict):
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise ModelConfigurationError(f'Unknown AI task: {task}') from None
        stem = 'fallback_' if fallback else ''
        provider, model = item.get(stem + 'provider'), item.get(stem + 'model')
        if fallback and not provider and not model:
            return None
        if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model:
            raise ModelConfigurationError(f'{task}: provider and model must both be configured')
        return ModelRoute(task, provider.lower(), model, fallback=fallback)
    stem = 'image_generation' if task == 'image_generation' else task
    provider = getattr(settings, stem + ('_fallback_provider' if fallback else '_provider'))
    model = getattr(settings, stem + ('_fallback_model' if fallback else '_model'))
    if fallback:
        if not provider and not model:
            return None
        if not provider or not model:
            raise ModelConfigurationError(f'{task}: fallback provider and model must both be configured')
        return ModelRoute(task, provider.lower(), model, fallback=True)
    if task == 'image_generation':
        if provider and not model:
            raise ModelConfigurationError(f'{task}: model is required when provider is selected')
        provider = provider or settings.image_provider
        model = model or settings.image_model
        legacy = not bool(settings.image_generation_provider or settings.image_generation_model)
    else:
        legacy_model = legacy_text_model(settings, task)
        legacy = not bool(provider or model)
        model = model or legacy_model
        if provider and not getattr(settings, stem + '_model'):
            raise ModelConfigurationError(f'{task}: model is required when provider is selected')
        provider = provider or (model.split('/', 1)[0] if '/' in model else 'openai')
    if not model or not provider:
        raise ModelConfigurationError(f'{task}: provider and model are required')
    if '/' in model and provider != 'openai_compatible':
        prefix = PROVIDER_PREFIX.get(provider, provider)
        if model.split('/', 1)[0] != prefix:
            raise ModelConfigurationError(f'{task}: provider {provider} does not match model {model}')
        model = model.split('/', 1)[1]
    return ModelRoute(task, provider.lower(), model, legacy=legacy)


def credential(settings: Settings, route: ModelRoute, image: bool = False) -> tuple[str, str | None]:
    mapping = {'openai': settings.openai_api_key, 'anthropic': settings.anthropic_api_key,
               'google': settings.gemini_api_key, 'mistral': settings.mistral_api_key,
               'deepseek': settings.deepseek_api_key, 'xai': settings.xai_api_key,
               'openai_compatible': settings.openai_compatible_api_key}
    if route.provider not in mapping:
        raise ModelConfigurationError(f'{route.task}: unsupported provider {route.provider}/{route.model}')
    key = mapping[route.provider].get_secret_value()
    if route.legacy and not key:
        key = (settings.image_api_key if image else settings.llm_api_key).get_secret_value()
    base = settings.openai_compatible_api_base if route.provider == 'openai_compatible' else settings.llm_api_base if route.legacy and not image else None
    if not key:
        raise ModelConfigurationError(f'{route.task}: API key missing for {route.provider}/{route.model}')
    if route.provider == 'openai_compatible' and not base:
        raise ModelConfigurationError(f'{route.task}: OpenAI-compatible API base missing for {route.provider}/{route.model}')
    return key, base


def capabilities(settings: Settings, route: ModelRoute) -> dict:
    try:
        overrides = json.loads(settings.ai_model_capabilities)
        if not isinstance(overrides, dict):
            raise ValueError()
    except (TypeError, ValueError):
        raise ModelConfigurationError('AI_MODEL_CAPABILITIES must be a JSON object') from None
    specified = overrides.get(f'{route.provider}/{route.model}')
    if specified is not None:
        if not isinstance(specified, dict):
            raise ModelConfigurationError(f'{route.task}: capability declaration must be an object')
        return specified
    # The pinned LiteLLM offline map predates this model and returns no context
    # limit. Official model contract verified 2026-10-20; no remote price/map fetch.
    # This adapter uses Chat Completions without tools.
    if route.provider=='openai' and route.model=='gpt-6.1-sol':
        return {'json_output':True,'structured_output':True,'context_tokens':1050000,
                'vision':True,'tools':False,'image_generation':False,'streaming':True}
    if route.provider == 'openai_compatible':
        raise ModelConfigurationError(f'{route.task}: declare capabilities for {route.provider}/{route.model}')
    import litellm
    try:
        info = litellm.get_model_info(route.litellm_model)
        supported = litellm.get_supported_openai_params(route.litellm_model) or []
    except Exception:
        raise ModelConfigurationError(f'{route.task}: declare capabilities for unknown model {route.provider}/{route.model}') from None
    return {'json_output': 'response_format' in supported,
            'structured_output': bool(info.get('supports_response_schema')),
            'tools': 'tools' in supported,
            'context_tokens': info.get('max_input_tokens') or info.get('max_tokens'),
            'vision': bool(info.get('supports_vision')),
            'image_generation': info.get('mode') == 'image_generation' or bool(info.get('supports_image_generation')),
            'streaming': 'stream' in supported}


def validate_text_route(settings: Settings, route: ModelRoute, input_tokens: int, output_tokens: int | None = None) -> None:
    found = capabilities(settings, route)
    if not found.get('json_output'):
        raise ModelConfigurationError(f'{route.task}: {route.provider}/{route.model} lacks JSON output')
    limit = found.get('context_tokens')
    if not isinstance(limit, int) or limit < input_tokens + (output_tokens or settings.max_output_tokens):
        raise ModelConfigurationError(f'{route.task}: {route.provider}/{route.model} lacks required context length')


def validate_image_route(settings: Settings, route: ModelRoute) -> None:
    if route.provider not in {'openai', 'google', 'openai_compatible'}:
        raise ModelConfigurationError(f'{route.task}: no image adapter for {route.provider}/{route.model}')
    if not capabilities(settings, route).get('image_generation'):
        raise ModelConfigurationError(f'{route.task}: {route.provider}/{route.model} lacks image generation')


OUTPUT_LIMITS = {'EditorialOutput': 12000, 'CompetitorSummary': 1500, 'SEOInterpretation': 2000, 'TopicMap': 6000, 'AssessmentBatch': 6000,
                 'Briefing': 4000, 'SelectedBriefing': 4000,
                 'Article': 8000, 'SelectedArticle': 8000, 'CheckResult': 2000,
                 'SocialDrafts': 1500, 'EvaluationReview': 2400, 'ImagePrompt': 1500}

# A full article/evidence audit needs more space than a small CheckResult task.
# Explicit task options still take precedence; the global output cap always wins.
QA_SCHEMAS = {'QAUnitResult','PageQAUnitResult','ModuleQAUnitResult','ClaimQAUnitResult','GuidedPageQAUnitResult','GuidedModuleQAUnitResult','GuidedClaimQAUnitResult'}
TASK_OUTPUT_LIMITS = {('seo_qa', 'CheckResult'): 6000,
    **{('seo_qa', name):2400 for name in QA_SCHEMAS}, ('seo_strategy','CoverageAssessment'):1600}


def text_request_options(settings: Settings, route: ModelRoute, schema_name: str) -> dict:
    """Translate validated task options at the provider boundary, never in agents."""
    import litellm
    configured = json.loads(settings.ai_task_options).get(route.task, {})
    default_limit = TASK_OUTPUT_LIMITS.get((route.task, schema_name),
                                         OUTPUT_LIMITS.get(schema_name, settings.max_output_tokens))
    limit = min(settings.max_output_tokens, configured.get('max_output_tokens', default_limit))
    supported = litellm.get_supported_openai_params(route.litellm_model) or []
    options = {'max_tokens': limit, 'response_format': {'type': 'json_object'}}
    effort = configured.get('reasoning_effort')
    if route.provider=='openai' and route.model=='gpt-6.1-sol':
        effort=effort or 'low'
        if effort=='none':raise ModelConfigurationError('GPT-6.1 Sol requires low or higher reasoning effort')
        options['service_tier']='default'  # Standard processing, no Fast/Ultrafast premium.
    # DeepSeek enables costly high-effort thinking by default, including for JSON tasks.
    if effort is None and route.provider == 'deepseek' and 'reasoning_effort' in supported:
        effort = 'none'
    if effort is not None:
        if 'reasoning_effort' not in supported:
            raise ModelConfigurationError(f'{route.task}: {route.provider}/{route.model} lacks configured reasoning control')
        options['reasoning_effort'] = effort
    # Reasoning models can reject sampling parameters when their default effort is active.
    if 'temperature' in supported and (effort == 'none' or
            (effort is None and 'reasoning_effort' not in supported)):
        options['temperature'] = 0.2
    return options
