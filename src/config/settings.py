from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    """All secrets enter through Settings; runtime flags cannot enable WP writes."""
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="forbid", validate_assignment=True)
    demo_mode: bool = True
    combined_mode: bool = True
    max_run_usd: float = Field(1.0, gt=0, le=5)
    max_monthly_usd: float = Field(20.0, gt=0, le=10000)
    official_discovery_enabled: bool = True
    tavily_api_key: SecretStr = SecretStr("")
    tavily_request_max_usd: float | None = Field(None, gt=0, le=1)
    official_discovery_results: int = Field(5, ge=1, le=8)
    firecrawl_enabled: bool = False
    failure_checklist_auto: bool = False
    firecrawl_api_key: SecretStr = SecretStr("")
    firecrawl_request_max_usd: float | None = Field(None, gt=0, le=1)
    image_request_max_usd: float | None = Field(None, gt=0, le=1)
    ai_visibility_model: str = 'gemini-2.5-flash'
    competitor_pages: int = Field(5, ge=1, le=10)
    research_cache_hours: int = Field(24, ge=1, le=168)
    tax_year: int = Field(2026, ge=2000, le=2100)
    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    database_url: SecretStr = SecretStr("")
    input_dir: Path = ROOT / "inputs"
    output_dir: Path = ROOT / "outputs"
    data_dir: Path = ROOT / "var"
    wordpress_read_only: Literal[True] = True
    allow_wordpress_publishing: Literal[False] = False
    max_run_tokens: int = Field(120000, ge=1000, le=1400000)
    max_output_tokens: int = Field(12000, ge=500, le=16000)
    max_auto_revisions: int = Field(2, ge=0, le=5)
    max_human_revisions: int = Field(5, ge=1, le=20)
    llm_model_planning: str = "openai/gpt-5.4-mini"
    llm_model_drafting: str = "openai/gpt-5.4-mini"
    llm_model_briefing: str | None = None
    llm_model_brand: str | None = None
    llm_api_key: SecretStr = SecretStr("")
    llm_api_base: str | None = None
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    mistral_api_key: SecretStr = SecretStr("")
    deepseek_api_key: SecretStr = SecretStr("")
    xai_api_key: SecretStr = SecretStr("")
    openai_compatible_api_key: SecretStr = SecretStr("")
    openai_compatible_api_base: str | None = None
    ai_model_capabilities: str = "{}"
    ai_task_routes: str = "{}"
    ai_task_options: str = "{}"
    research_price_rates: str = "{}"
    image_prompt_enabled: bool = False
    seo_strategy_provider: str | None = None
    seo_strategy_model: str | None = None
    seo_strategy_fallback_provider: str | None = None
    seo_strategy_fallback_model: str | None = None
    topic_map_provider: str | None = None
    topic_map_model: str | None = None
    topic_map_fallback_provider: str | None = None
    topic_map_fallback_model: str | None = None
    briefing_provider: str | None = None
    briefing_model: str | None = None
    briefing_fallback_provider: str | None = None
    briefing_fallback_model: str | None = None
    content_provider: str | None = None
    content_model: str | None = None
    content_fallback_provider: str | None = None
    content_fallback_model: str | None = None
    brand_provider: str | None = None
    brand_model: str | None = None
    brand_fallback_provider: str | None = None
    brand_fallback_model: str | None = None
    seo_qa_provider: str | None = None
    seo_qa_model: str | None = None
    seo_qa_fallback_provider: str | None = None
    seo_qa_fallback_model: str | None = None
    social_content_provider: str | None = None
    social_content_model: str | None = None
    social_content_fallback_provider: str | None = None
    social_content_fallback_model: str | None = None
    image_prompt_provider: str | None = None
    image_prompt_model: str | None = None
    image_prompt_fallback_provider: str | None = None
    image_prompt_fallback_model: str | None = None
    image_generation_enabled: bool = False
    image_provider: Literal["openai"] = "openai"
    image_model: str = "gpt-image-2.5-flare"
    image_quality: Literal['low', 'medium', 'high'] | None = None
    image_generation_provider: str | None = None
    image_generation_model: str | None = None
    image_generation_fallback_provider: str | None = None
    image_generation_fallback_model: str | None = None
    image_api_key: SecretStr = SecretStr("")
    image_timeout_seconds: int = Field(120, ge=10, le=300)
    max_image_attempts_per_run: int = Field(10, ge=1, le=100)
    image_webp_quality: int = Field(86, ge=75, le=95)
    image_jpeg_quality: int = Field(88, ge=75, le=95)
    dataforseo_login: SecretStr = SecretStr("")
    dataforseo_password: SecretStr = SecretStr("")
    dataforseo_location_code: int = 2276
    dataforseo_language_code: str = "de"
    gsc_site_url: str = "https://steuerkanzlei.example/"
    gsc_access_token: SecretStr = SecretStr("")
    gsc_service_account_file: Path | None = None
    gsc_days: int = Field(90, ge=1, le=450)
    seo_decisions_enabled: bool = True
    seo_min_impressions: int = Field(100, ge=1, le=1000000)
    wordpress_base_url: str = "https://steuerkanzlei.example"
    auth_encryption_key: SecretStr = SecretStr("")
    auth_public_origin: str = "http://127.0.0.1:3000"

    @field_validator("llm_api_base", "gsc_service_account_file", "llm_model_briefing", "llm_model_brand",
                     "tavily_request_max_usd", "firecrawl_request_max_usd", "image_request_max_usd", "image_quality", mode="before")
    @classmethod
    def empty_is_none(cls, value):
        return None if value == "" else value

    @field_validator("input_dir", "output_dir", "data_dir", "gsc_service_account_file")
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        return (ROOT / value).resolve() if value is not None and not value.is_absolute() else value

    @field_validator("wordpress_base_url", "gsc_site_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Public site URLs must use HTTPS without embedded credentials")
        return value

    @model_validator(mode="after")
    def valid_auth_configuration(self):
        import json
        try:
            options = json.loads(self.ai_task_options)
            if not isinstance(options, dict):
                raise ValueError()
            for task, item in options.items():
                if not isinstance(task, str) or not isinstance(item, dict):
                    raise ValueError()
                if set(item) - {'reasoning_effort', 'max_output_tokens'}:
                    raise ValueError()
                if 'reasoning_effort' in item and item['reasoning_effort'] not in {
                    'none', 'low', 'medium', 'high', 'xhigh', 'max'}:
                    raise ValueError()
                if 'max_output_tokens' in item and (
                    type(item['max_output_tokens']) is not int or
                    not 500 <= item['max_output_tokens'] <= self.max_output_tokens):
                    raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('AI_TASK_OPTIONS must contain supported reasoning and output limits') from None
        from urllib.parse import urlsplit
        origin = urlsplit(self.auth_public_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname or origin.path not in {"", "/"} or origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError("AUTH_PUBLIC_ORIGIN must be an HTTP(S) origin")
        if origin.scheme == "http" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Hosted authentication requires HTTPS")
        return self
