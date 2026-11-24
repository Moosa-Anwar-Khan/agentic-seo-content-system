from src.orchestrator.state import RunState
import re
from bs4 import BeautifulSoup
from src.agents.base import BaseAgent
from src.orchestrator.registry import register_agent
from src.schemas.content import CheckResult, DISCLAIMER
from src.security.sanitize import sanitize_text
from src.services.quality import validate_brand_findings
from src.services.seo_decisions import selected_context
from src.services.editorial_brand import brand_for_agents,validate_editorial_design

@register_agent("brand_agent")
class BrandAgent(BaseAgent):
    def execute(self, state: RunState) -> RunState:
        if state.combined_version:
            from src.combined.pipeline import brand
            return brand(self.context,state)
        if not state.article or not state.html:
            raise ValueError("Article missing")
        soup = BeautifulSoup(state.html,'html.parser')
        for tag in soup(['script','style']): tag.decompose()
        text = soup.get_text(' ',strip=True)
        errors=validate_editorial_design(state.html,state.article)
        warnings=['Regelprüfung ersetzt keine menschliche fachliche und sprachliche Freigabe.']
        target='content_agent'
        if re.search(r'\b(du|dich|dein|deine|euch|euer)\b',text,re.I): errors.append('Informelle Ansprache gefunden')
        if 'Sie' not in text: errors.append('Formelle Ansprache fehlt')
        if re.search(r'garantiert|100\s*%\s*rechtssicher|garantierte Steuerersparnis|Rückmeldung in 2 Stunden',text,re.I): errors.append('Unzulässiges Leistungs- oder Rechtsversprechen')
        if DISCLAIMER not in text: errors.append('Pflicht-Disclaimer fehlt')
        try: sanitize_text(text,40000)
        except ValueError: errors.append('Datenschutz- oder Eingaberegel verletzt')
        if not errors and not self.context.settings.demo_mode:
            import json
            brand=brand_for_agents(self.context.settings)
            semantic=self.context.llm.generate(str(state.run_id),CheckResult,
                'Review ONLY current_public_draft for professional formal German Sie address, misleading tax guarantees, invented service promises, and individual tax advice against the supplied brand rules. Each blocking error MUST contain a literal excerpt from current_public_draft inside double quotation marks and explain the specific rule it violates. Never cite wording from an existing website, research, old revisions, or a briefing as a defect in the current draft. Do not invent quotes. Ordinary contact invitations are not guaranteed outcomes. General professional-review reminders, suggestions and positive observations belong in warnings, not errors. If no supported blocking defect exists, return passed=true with errors=[]. Return German findings.',
                {'brand':brand,'current_public_draft':[text[i:i+9000] for i in range(0,len(text),9000)],
                 **({'selected_evidence':selected_context(state)} if state.selected_evidence else {}),
                 'review_scope':'current_public_draft_only_v2','supplied_source_urls':state.article.source_urls}, model_role='brand')
            validate_brand_findings(semantic, text)
            if not semantic.passed:
                errors.extend(semantic.errors or ['Semantische Brand-Prüfung nicht bestanden'])
                target=semantic.target
            warnings.extend(semantic.warnings)
        state.brand_check = CheckResult(passed=not errors,errors=errors,warnings=warnings,target=target)
        if not errors: state.status='brand_check_passed'
        return state
