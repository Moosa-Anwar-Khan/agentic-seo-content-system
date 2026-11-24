"""A tiny in-memory web for fetch tests: routes, DNS answers and a request log."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2

from src.combined.fetch import Fetcher

PUBLIC_IP = "93.184.216.34"


@dataclass
class Route:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    raises: Callable[[httpx2.Request], Exception] | None = None


class FakeWeb:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.dns: dict[str, str] = {}
        self.requests: list[httpx2.Request] = []

    @property
    def requested_urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def page(self, url: str, body: str | bytes = "", status: int = 200,
             content_type: str = "text/html; charset=utf-8", **headers: str) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.routes[url] = Route(status, data, content_type, dict(headers))

    def redirect(self, url: str, location: str, status: int = 301) -> None:
        self.routes[url] = Route(status, b"", "text/html", {"location": location})

    def fail(self, url: str, error: Callable[[httpx2.Request], Exception]) -> None:
        self.routes[url] = Route(raises=error)

    def resolve(self, host: str) -> list[str]:
        return [self.dns.get(host, PUBLIC_IP)]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        route = self.routes.get(str(request.url))
        if route is None:
            return httpx2.Response(404, content=b"not found",
                                   headers={"content-type": "text/plain"})
        if route.raises:
            raise route.raises(request)
        headers = {"content-type": route.content_type, **route.headers}
        return httpx2.Response(route.status, content=route.body, headers=headers)

    def fetcher(self, **kw) -> Fetcher:
        return Fetcher(transport=httpx2.MockTransport(self.handler), resolver=self.resolve, **kw)


def article(title: str, text: str = "Inhalt " * 60, tag: str = "main") -> str:
    return (f"<html><head><title>{title}</title></head><body><nav>Menü</nav>"
            f"<{tag}><h1>{title}</h1><p>{text}</p><h2>Abschnitt</h2><p>Mehr.</p></{tag}>"
            "<footer>Impressum</footer></body></html>")


def urlset(urls: list[str]) -> str:
    items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>')


def sitemapindex(urls: list[str]) -> str:
    items = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}'
            "</sitemapindex>")
