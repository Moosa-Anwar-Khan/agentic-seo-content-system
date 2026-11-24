"""Unapproved evaluation contracts; never professional attestation."""
from typing import Literal
from pydantic import Field, model_validator
from src.schemas.common import StrictModel


class EvaluationFinding(StrictModel):
    category: Literal['suggestion', 'material', 'unverifiable']
    rule: Literal['style', 'contradiction', 'unsupported_claim', 'spelling', 'readability', 'source_gap']
    section: str = Field(min_length=1)
    quote: str = ''
    evidence_ref: str = ''
    evidence_quote: str = ''
    message_en: str = Field(min_length=5)
    action_en: str = Field(min_length=5)

    @model_validator(mode='after')
    def grounded(self):
        if self.category == 'material' and (self.rule in {'style','spelling','readability','source_gap'} or
                not self.quote or not self.evidence_ref or not self.evidence_quote):
            raise ValueError('Material findings require a material rule, passage and supporting evidence')
        return self


class EvaluationReview(StrictModel):
    coverage: list[Literal['text', 'spelling', 'consistency', 'support', 'channel_rules']]
    findings: list[EvaluationFinding] = Field(max_length=12)
    limitations: list[str] = Field(max_length=8)
    # Image transcription is observed text, never a substitute for the attachment.
    visible_text: str = ''
    unreadable_areas: list[str] = Field(default_factory=list,max_length=12)

    @model_validator(mode='after')
    def coverage_complete(self):
        expected={'text','spelling','consistency','support','channel_rules'}
        if len(self.coverage)!=len(expected) or set(self.coverage)!=expected:
            raise ValueError('Every evaluation criterion needs an assessment')
        return self
