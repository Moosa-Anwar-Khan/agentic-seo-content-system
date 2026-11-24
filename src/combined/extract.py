"""Main-text extraction with a transparent heuristic (ADR-0010).

The result says how the text was found (``main``, ``article`` or ``body``) so callers can mark
heuristic extraction, and flags pages that are only a JavaScript shell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, Tag

MAX_TEXT_CHARS = 60_000
MAX_HEADINGS = 40
JS_SHELL_MAX_TEXT = 200
# A <main>/<article> must hold at least this share of the body text to count as the main text.
MIN_CONTAINER_SHARE = 0.3

_DROP = ("script", "style", "noscript", "template", "svg", "iframe", "form", "nav", "header",
         "footer", "aside")


@dataclass
class Extraction:
    title: str | None
    text: str
    headings: list[str] = field(default_factory=list)
    method: Literal["main", "article", "body", "none"] = "none"
    js_shell: bool = False
    truncated: bool = False
    canonical: str | None = None
    noindex: bool = False


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def extract_html(data: bytes | str) -> Extraction:
    soup = BeautifulSoup(data, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None

    canonical = None
    link = soup.find("link", rel=lambda v: v and "canonical" in v)
    if isinstance(link, Tag) and link.get("href"):
        canonical = str(link["href"]).strip()
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = isinstance(robots, Tag) and "noindex" in str(robots.get("content", "")).lower()

    script_count = len(soup.find_all("script"))
    for name in _DROP:
        for node in soup.find_all(name):
            node.decompose()

    body = soup.body if isinstance(soup.body, Tag) else None
    body_len = len(body.get_text(" ", strip=True)) if body else 0
    container: Tag | None = None
    method: Literal["main", "article", "body", "none"] = "none"
    for label in ("main", "article"):
        found = soup.find_all(label)
        # Several <article> cards are a listing, not the page's main text.
        if len(found) == 1 and isinstance(found[0], Tag):
            share = len(found[0].get_text(" ", strip=True)) / body_len if body_len else 1.0
            if share >= MIN_CONTAINER_SHARE:
                container, method = found[0], label  # type: ignore[assignment]
                break
    if container is None and body is not None:
        container, method = body, "body"
    if container is None:
        return Extraction(title=title or None, text="", method="none", canonical=canonical,
                          noindex=noindex, js_shell=script_count > 0)

    headings = [
        h.get_text(" ", strip=True)
        for h in container.find_all(["h1", "h2", "h3"])
        if h.get_text(strip=True)
    ][:MAX_HEADINGS]
    text = _clean(container.get_text("\n", strip=True))
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]
    js_shell = len(text) < JS_SHELL_MAX_TEXT and script_count > 0
    return Extraction(
        title=title or None, text=text, headings=headings, method=method, js_shell=js_shell,
        truncated=truncated, canonical=canonical, noindex=noindex,
    )
