"""Small, explicit Markdown reader for drafts: module markers, headings, links, words."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MODULE_RE = re.compile(r"^<!--\s*([SL]\d{2}[a-z]?)\s*-->\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MD_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(\s*<?([^\s)>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
HTML_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
BARE_URL_RE = re.compile(r"(?<![(\"'<=])\bhttps?://[^\s)\]>\"']+")
GRAPHIC_RE = re.compile(r"<!--\s*GRAPHIC:.*?<!--\s*/GRAPHIC\s*-->", re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9][\wÄÖÜäöüß-]*")


@dataclass
class Heading:
    level: int
    text: str
    module: str | None
    line: int


@dataclass
class Link:
    url: str
    text: str
    kind: str  # markdown | html | autolink | bare
    module: str | None
    line: int


@dataclass
class Section:
    module: str
    start_line: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class ParsedDraft:
    sections: list[Section]
    preamble: str
    headings: list[Heading]
    links: list[Link]

    def section(self, module: str) -> Section | None:
        return next((s for s in self.sections if s.module == module), None)

    def module_order(self) -> list[str]:
        return [s.module for s in self.sections]


def parse(markdown: str) -> ParsedDraft:
    sections: list[Section] = []
    preamble: list[str] = []
    headings: list[Heading] = []
    links: list[Link] = []
    current: Section | None = None
    in_fence = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        marker = MODULE_RE.match(line.strip()) if not in_fence else None
        if marker:
            current = Section(marker.group(1), number)
            sections.append(current)
            continue
        (current.lines if current else preamble).append(line)
        module = current.module if current else None
        if in_fence:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            headings.append(Heading(len(heading.group(1)), heading.group(2), module, number))
        links.extend(_links(line, module, number))
    return ParsedDraft(sections, "\n".join(preamble).strip(), headings, links)


def _links(line: str, module: str | None, number: int) -> list[Link]:
    found: list[Link] = []
    rest = line
    for m in MD_LINK_RE.finditer(line):
        if m.group(1) != "!":  # images are not links
            found.append(Link(m.group(3), m.group(2), "markdown", module, number))
        rest = rest.replace(m.group(0), " ")
    for m in HTML_HREF_RE.finditer(rest):
        found.append(Link(m.group(1), "", "html", module, number))
    rest = HTML_HREF_RE.sub(" ", rest)
    for m in AUTOLINK_RE.finditer(rest):
        found.append(Link(m.group(1), m.group(1), "autolink", module, number))
    rest = AUTOLINK_RE.sub(" ", rest)
    for m in BARE_URL_RE.finditer(rest):
        found.append(Link(m.group(0).rstrip(".,;:"), m.group(0), "bare", module, number))
    return found


def plain_text(markdown: str) -> str:
    """Text without comments, graphic briefs, link targets and Markdown syntax."""
    text = GRAPHIC_RE.sub(" ", markdown)
    text = COMMENT_RE.sub(" ", text)
    text = MD_LINK_RE.sub(lambda m: m.group(2), text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#*_`>|]", " ", text)
    return text


def word_count(markdown: str) -> int:
    return len(WORD_RE.findall(plain_text(markdown)))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", plain_text(text))
    return [p.strip() for p in parts if p.strip()]


def strip_markers(markdown: str) -> str:
    """Markdown for export: module markers removed, graphic briefs kept separately."""
    lines = [line for line in markdown.splitlines() if not MODULE_RE.match(line.strip())]
    text = GRAPHIC_RE.sub("", "\n".join(lines))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
