from src.services.failures import diagnostic_boundary
import json
import re
from uuid import UUID, uuid5
from pydantic import Field
from src.schemas.common import StrictModel
from src.schemas.assets import SocialPost
from src.orchestrator.state import RunState
from src.services.llm import ModelRouter
from src.storage.base import BaseStorage
from src.config.settings import Settings
from src.security.sanitize import sanitize_text

class SocialDrafts(StrictModel):
    linkedin: str = Field(min_length=30, max_length=1800)
    google_business_profile: str = Field(min_length=30, max_length=900)

class SocialService:
    def __init__(self, settings: Settings, storage: BaseStorage):
        self.settings, self.storage = settings, storage

    @diagnostic_boundary('Social Drafts','Prepare channel drafts')
    def prepare(self, state: RunState, operation_id: UUID | None = None) -> list[SocialPost]:
        if state.combined_version and not self.settings.demo_mode and state.status != 'exported':
            raise ValueError('Approve the main article before preparing social adaptations; publishing is not required')
        if state.status not in {'seo_check_passed', 'exported'} or not state.article or not state.brand_check or not state.brand_check.passed or not state.seo_check or not state.seo_check.passed:
            raise ValueError('The website draft must pass both checks first')
        if operation_id:
            existing = [self.storage.get_asset(str(uuid5(operation_id, platform)), 'post')
                        for platform in ('linkedin', 'google_business_profile')]
            if all(existing):
                return existing
        brand = json.loads((self.settings.input_dir / 'brand-guidelines/rules.json').read_text(encoding='utf-8'))
        if self.settings.demo_mode:
            drafts = SocialDrafts(
                linkedin=f"{state.article.title}: Welche Fragen sollten private Immobilieninvestoren vor einem Beratungsgespräch klären? Der Beitrag gibt einen allgemeinen Überblick. Eine individuelle steuerliche Beurteilung erfordert ein persönliches Gespräch.",
                google_business_profile=f"{state.article.title}: Informieren Sie sich über wichtige Fragen rund um vermietete Immobilien. Für eine individuelle steuerliche Einschätzung vereinbaren Sie bitte ein persönliches Gespräch.")
        else:
            drafts = ModelRouter(self.settings, self.storage).generate(
                str(state.run_id), SocialDrafts,
                'Create two distinct German social drafts from the approved article: a professional LinkedIn post and a concise Google Business Profile update. Use formal Sie. Do not include URLs; code appends the approved enquiry link. Do not add new tax claims, guarantees, contact details or fabricated services. Keep Google text under 750 characters and LinkedIn text under 1600 characters to leave room for the CTA.',
                {'icp': state.icp.model_dump(), 'article_title': state.article.title,
                 'article_introduction': state.article.introduction,
                 'article_sections': [{'heading': s.heading, 'paragraphs': s.paragraphs[:1]} for s in state.article.sections],
                 'verified_editorial':state.editorial if state.combined_version else {},
                 'brand': brand, 'supplied_source_urls': state.article.source_urls}, model_role='social_content')
        for value in (drafts.linkedin, drafts.google_business_profile):
            sanitize_text(value, 1800)
            if re.search(r'\b(du|dich|dein|deine|euch|euer)\b|garantiert|100\s*%\s*rechtssicher', value, re.I):
                raise ValueError('Social post failed brand rules')
            if re.search(r'https?://|www\.', value, re.I):
                raise ValueError('Social URLs must be added by code, not invented by the model')
        cta_url = self.settings.wordpress_base_url.rstrip('/') + '/mandant-werden/'
        posts = [(self.storage.get_asset(str(uuid5(operation_id, platform)), 'post') if operation_id else None)
                 or SocialPost(**({'id': uuid5(operation_id, platform)} if operation_id else {}),
                               run_id=state.run_id, source_revision=state.revision,
                               platform=platform, text=getattr(drafts, platform) + '\n\nKostenfreies Erstgespräch: ' + cta_url,
                               link_target='enquiry', link_url=cta_url)
                 for platform in ('linkedin', 'google_business_profile')]
        for post in posts:
            if len(post.text) > (900 if post.platform == 'google_business_profile' else 1800):
                raise ValueError('Social post exceeds the channel limit after adding its CTA')
            self.storage.save_asset(post)
        return posts

    def review(self, state: RunState, post_id: str, action: str, reviewer: str, reviewer_id: str | None = None) -> SocialPost:
        if self.settings.combined_mode and reviewer not in {'Alex','Sam'}:
            raise PermissionError('Social approval is restricted to Alex or Sam')
        if state.combined_version and action=='approved' and state.status!='exported':
            raise ValueError('Approve the source website draft before approving social adaptations')
        if action not in {'approved', 'rejected'}:
            raise ValueError('Unsupported social review action')
        post = self.storage.get_asset(post_id, 'post')
        if not post or post.run_id != state.run_id:
            raise KeyError('Social post not found')
        if post.source_revision != state.revision or state.status not in {'seo_check_passed', 'exported'}:
            raise ValueError('Social post belongs to an outdated draft')
        if post.status != 'pending':
            raise ValueError('Social post has already been reviewed')
        post.status = action
        post.reviewed_by = reviewer
        post.reviewer_id = reviewer_id
        self.storage.save_asset(post)
        return post
