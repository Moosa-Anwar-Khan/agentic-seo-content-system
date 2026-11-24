import json
import re
import unicodedata
from bs4 import BeautifulSoup

class PrivacyError(ValueError):
    pass

class InputSizeError(ValueError):
    """A prompt value exceeds the local safety boundary; no provider request is sent."""
    pass

# Defense in depth: free text cannot reliably prove absence of all personal data.
# Only approved public inputs / aggregated metrics are accepted in addition to these checks.
SENSITIVE = re.compile(r"(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}|\bDE\d{20}\b|\b\d{2}/\d{3}/\d{5}\b|\b(?:Steuer-ID|Identifikationsnummer|IBAN|Mandantennummer)\b)", re.I)
INJECTION = re.compile(r"(?:ignore (?:all|previous)|disregard .*instructions|system\s*prompt|developer\s*message|ignoriere .*anweisungen|api[_ -]?key|<\|.*?\|>)", re.I)

def sanitize_text(value: str, limit: int = 12000, *, public_emails: frozenset[str] = frozenset()) -> str:
    if not isinstance(value, str):
        raise ValueError("Input is not text")
    if len(value) > limit * 4:
        raise InputSizeError("Input exceeds the size limit")
    value = unicodedata.normalize("NFKC", value)
    soup = BeautifulSoup(value, "html.parser")
    for node in soup(["script", "style", "iframe", "object", "form"]):
        node.decompose()
    value = soup.get_text(" ", strip=True)
    value = "".join(c for c in value if unicodedata.category(c)[0] != "C" or c in "\n\t")
    if len(value) > limit:
        raise InputSizeError("Sanitized input exceeds the size limit")
    # Only exact, server-loaded public company addresses can be exempted. Never
    # exempt a field, domain, source document or other identifier wholesale.
    if any(match.group().casefold() not in public_emails for match in SENSITIVE.finditer(value)):
        raise PrivacyError("Input contains suspected personal data; submit public / aggregated content only")
    if INJECTION.search(value):
        raise ValueError("Input contains an instruction or secret access pattern")
    return value

def sanitize_payload(value, *, public_emails: frozenset[str] = frozenset()):
    if isinstance(value, str):
        return sanitize_text(value, public_emails=public_emails)
    if isinstance(value, list):
        if len(value) > 500:
            raise InputSizeError("Too many payload items")
        return [sanitize_payload(v, public_emails=public_emails) for v in value]
    if isinstance(value, dict):
        return {sanitize_text(str(k), 120): sanitize_payload(v, public_emails=public_emails) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("Only JSON-safe payload values are allowed")

def untrusted_context(value, *, public_emails: frozenset[str] = frozenset()) -> str:
    # JSON framing is data, never a new system message. Models receive no action tools.
    return json.dumps(sanitize_payload(value, public_emails=public_emails), ensure_ascii=False)
