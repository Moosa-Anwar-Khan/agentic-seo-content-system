from src.services.failures import diagnostic_boundary
from urllib.parse import urlsplit
import json
import logging
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import httpx
from src.config.settings import Settings
from src.integrations.base import PublicContentSource
from src.security.sanitize import sanitize_text

class WordPressResponseError(ValueError):
    """The public REST response does not contain a valid page collection."""


def decode_public_collection(response) -> list[dict]:
    """Recover JSON after known Elementor CSS output without executing that output."""
    try:
        value = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = response.text
        if not re.match(r"\s*<style\s+id=[\"']elementor-post-\d+[\"']\s*>", text):
            raise WordPressResponseError('WordPress REST response is not valid JSON') from None
        # Only a complete JSON suffix is accepted; arbitrary HTML and trailing data fail.
        candidates = list(re.finditer(r'\[\s*(?:\{\s*"id"\s*:|\])', text))
        value = None
        for candidate in candidates[:32]:
            prefix = text[:candidate.start()]
            tags = re.findall(r'<\s*/?\s*([a-zA-Z][a-zA-Z0-9-]*)\b', prefix)
            if (len(prefix) > 2 * 1024 * 1024 or not tags
                    or any(tag.lower() != 'style' for tag in tags)
                    or '<!' in prefix or '<?' in prefix):
                continue
            try:
                parsed = json.loads(text[candidate.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
                value = parsed
                logging.getLogger(__name__).warning(
                    'Ignored leading Elementor stylesheet output in public WordPress REST response')
                break
        if value is None:
            raise WordPressResponseError('WordPress REST response has unsupported output before JSON') from None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WordPressResponseError('WordPress REST response is not a page collection')
    return value


class WordPressReadOnly(PublicContentSource):
    def __init__(self, settings: Settings):
        self.base = settings.wordpress_base_url.rstrip("/")
        self.coverage = {'inventory_complete': False, 'pages_retrieved': 0, 'limitations': []}

    @diagnostic_boundary('Website Evidence', 'Read public WordPress evidence')
    def request(self, method: str, route: str, params: dict | None = None):
        if method.upper() != "GET" or route not in {"posts", "pages"}:
            raise PermissionError("WordPress allows only GET posts/pages")
        # Fixed public context; caller cannot request edit context or private statuses.
        allowed = {"page", "per_page"}
        if params and not set(params).issubset(allowed):
            raise PermissionError("Unsupported WordPress query parameters")
        query = {**(params or {}), "context": "view", "status": "publish", "_fields": "id,link,title,content,modified"}
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.get(self.base + "/wp-json/wp/v2/" + route, params=query)
            response.raise_for_status()
            return decode_public_collection(response), int(response.headers.get("X-WP-TotalPages", "1"))

    def pages(self) -> list[dict]:
        output = []
        complete = True
        limitations = []
        for route in ["pages", "posts"]:
            for page in range(1, 4):
                items, total = self.request("GET", route, {"page": page, "per_page": 25})
                for item in items:
                    parsed = urlsplit(item['link'])
                    if parsed.scheme != 'https' or parsed.hostname != urlsplit(self.base).hostname or parsed.username or parsed.password:
                        complete = False
                        continue
                    try:
                        raw = item['content']['rendered']
                        soup = BeautifulSoup(raw[:24000], 'html.parser')
                        for tag in soup(['script', 'style', 'iframe', 'form']):
                            tag.decompose()
                        plain = soup.get_text(' ', strip=True)
                        headings = [{'level': int(h.name[1]), 'text': sanitize_text(h.get_text(' ', strip=True)[:500], 500)}
                                    for h in soup.find_all(['h1', 'h2', 'h3'])[:80]]
                        truncated = len(raw) > 24000 or len(plain) > 6000 or len(soup.find_all(['h1', 'h2', 'h3'])) > 80
                        output.append({'url': item['link'], 'title': sanitize_text(item['title']['rendered'], 500),
                                       'excerpt': sanitize_text(plain[:6000], 6000), 'headings': headings,
                                       'content_truncated': truncated, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                                       'modified_at': item.get('modified')})
                    except ValueError:
                        complete = False
                        continue
                if page >= total:
                    break
            if total > 3:
                complete = False
                limitations.append('WordPress-Inventar begrenzt: höchstens 75 Seiten je Inhaltstyp; weitere Inhalte sind nicht geprüft.')
        if any(p['content_truncated'] for p in output):
            limitations.append('Einige Seiteninhalte oder Überschriften wurden gekürzt; fehlende Erwähnungen sind kein Nachweis fehlender Abdeckung.')
        if not complete:
            limitations.append('Die vorhandenen Inhalte sind nicht vollständig erfasst; neue Themen müssen zusätzlich geprüft werden.')
        self.coverage = {'inventory_complete': complete, 'pages_retrieved': len(output), 'limitations': list(dict.fromkeys(limitations))}
        return output
