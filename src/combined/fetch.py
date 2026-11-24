"""Guarded page fetching for the inventory and competitor research (§8, §9, ADR-0010).

Every request target is checked before it is contacted: https only, default port, optional
domain restriction, and every resolved address must be public. Redirects are followed by hand
and each hop is checked again. robots.txt, a size cap and a timeout apply. With IP pinning the
connection goes to the address that was checked, with the original host for TLS (SNI) and the
``Host`` header, so a DNS answer cannot change between check and connect.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx2

from src.combined.contracts import canonical_url, host_in_domain, sha256_hex, utc_now
from src.combined.enums import FetchStatus
from src.combined.extract import Extraction, extract_html

USER_AGENT = "seo-os/0.1 (local SEO research tool)"
ROBOTS_AGENT = "seo-os"

Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class FetchBlocked(Exception):
    """The target is not allowed. The message says why."""


@dataclass(frozen=True)
class FetchPolicy:
    allowed_domain: str | None = None
    max_bytes: int = 2_000_000
    timeout_seconds: float = 15.0
    max_redirects: int = 5
    respect_robots: bool = True


@dataclass
class FetchResult:
    requested_url: str
    final_url: str | None
    status: FetchStatus
    fetched_at: datetime
    http_status: int | None = None
    content_type: str | None = None
    note: str | None = None
    redirects: list[str] = field(default_factory=list)
    body: bytes | None = None
    truncated: bool = False
    extraction: Extraction | None = None

    @property
    def text(self) -> str | None:
        if self.status.has_content and self.extraction:
            return self.extraction.text
        return None

    @property
    def text_sha256(self) -> str | None:
        text = self.text
        return sha256_hex(text.encode("utf-8")) if text is not None else None


def is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class Fetcher:
    def __init__(
        self,
        transport: httpx2.BaseTransport | None = None,
        resolver: Resolver = system_resolver,
        clock: Callable[[], datetime] = utc_now,
        pin_ips: bool | None = None,
    ) -> None:
        self._client = httpx2.Client(
            transport=transport or httpx2.HTTPTransport(retries=0),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._resolver = resolver
        self._clock = clock
        # Pinning needs a real network stack; injected test transports see hostnames.
        self._pin = pin_ips if pin_ips is not None else transport is None
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def close(self) -> None:
        self._client.close()

    # --- public --------------------------------------------------------------------------

    def check_target(self, url: str, policy: FetchPolicy) -> tuple[str, str]:
        """Return (canonical url, checked ip) or raise :class:`FetchBlocked`."""
        try:
            url = canonical_url(url)
        except ValueError as exc:
            raise FetchBlocked(str(exc)) from None
        host = urlsplit(url).hostname or ""
        if policy.allowed_domain and not host_in_domain(host, policy.allowed_domain):
            raise FetchBlocked(f"{host} is outside the project domain {policy.allowed_domain}")
        try:
            addresses = [host] if _is_ip_literal(host) else self._resolver(host)
        except OSError as exc:
            raise FetchBlocked(f"DNS lookup for {host} failed: {exc}") from None
        if not addresses:
            raise FetchBlocked(f"{host} has no address")
        for address in addresses:
            if not is_public_ip(address):
                raise FetchBlocked(f"{host} resolves to a non-public address")
        return url, addresses[0]

    def fetch(self, url: str, policy: FetchPolicy) -> FetchResult:
        """Fetch a page and classify it. Never raises for network or policy problems."""
        started = self._clock()
        try:
            canonical, _ = self.check_target(url, policy)
        except FetchBlocked as exc:
            return FetchResult(url, None, FetchStatus.BLOCKED, started, note=str(exc))

        if policy.respect_robots and not self.robots_allows(canonical, policy):
            return FetchResult(canonical, None, FetchStatus.BLOCKED, started,
                               note="disallowed by robots.txt")
        try:
            response, body, truncated, hops = self._get(canonical, policy)
        except FetchBlocked as exc:
            return FetchResult(canonical, None, FetchStatus.BLOCKED, started, note=str(exc))
        except httpx2.TimeoutException:
            return FetchResult(canonical, None, FetchStatus.FAILED, started, note="timeout")
        except httpx2.HTTPError as exc:
            return FetchResult(canonical, None, FetchStatus.FAILED, started,
                               note=f"network error: {type(exc).__name__}")

        final_url = hops[-1] if hops else canonical
        result = FetchResult(
            requested_url=canonical, final_url=final_url, status=FetchStatus.FAILED,
            fetched_at=started, http_status=response.status_code,
            content_type=response.headers.get("content-type"), redirects=hops[:-1] if hops else [],
            body=body, truncated=truncated,
        )
        if response.status_code >= 400:
            result.note = f"HTTP {response.status_code}"
            result.body = None
            return result
        ctype = (result.content_type or "").split(";")[0].strip().lower()
        if ctype == "application/pdf" or final_url.lower().endswith(".pdf"):
            result.status, result.note = FetchStatus.PDF, "PDF, no text extraction"
            return result
        if ctype not in ("text/html", "application/xhtml+xml"):
            result.status, result.note = FetchStatus.NON_HTML, f"content type {ctype or 'unknown'}"
            return result

        extraction = extract_html(body)
        result.extraction = extraction
        if extraction.js_shell or not extraction.text:
            result.status = FetchStatus.JS_SHELL
            result.note = "page content needs JavaScript; treated as not read"
        elif truncated or extraction.truncated:
            result.status, result.note = FetchStatus.PARTIAL, "text cut at the size limit"
        elif extraction.method == "body":
            result.status = FetchStatus.HEURISTIC
            result.note = "no <main>/<article>; text taken from <body> by heuristic"
        else:
            result.status = FetchStatus.READ
        return result

    def fetch_raw(self, url: str, policy: FetchPolicy) -> tuple[int, bytes, bool, str]:
        """Status, body, truncated flag and final URL, for sitemaps. Raises on problems."""
        canonical, _ = self.check_target(url, policy)
        response, body, truncated, hops = self._get(canonical, policy)
        return response.status_code, body, truncated, (hops[-1] if hops else canonical)

    def robots_allows(self, url: str, policy: FetchPolicy) -> bool:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host not in self._robots:
            self._robots[host] = self._load_robots(host, policy)
        parser = self._robots[host]
        return parser is not None and parser.can_fetch(ROBOTS_AGENT, url)

    def robots_sitemaps(self, host: str, policy: FetchPolicy) -> list[str]:
        if host not in self._robots:
            self._robots[host] = self._load_robots(host, policy)
        parser = self._robots[host]
        return list(parser.site_maps() or []) if parser else []

    # --- internals -----------------------------------------------------------------------

    def _load_robots(
        self, host: str, policy: FetchPolicy
    ) -> urllib.robotparser.RobotFileParser | None:
        """Parsed robots.txt. Missing (4xx) allows all; unreachable (5xx/network) blocks."""
        parser = urllib.robotparser.RobotFileParser()
        robots_policy = FetchPolicy(
            allowed_domain=policy.allowed_domain, max_bytes=500_000,
            timeout_seconds=policy.timeout_seconds, max_redirects=policy.max_redirects,
            respect_robots=False,
        )
        try:
            canonical, _ = self.check_target(f"https://{host}/robots.txt", robots_policy)
            response, body, _, _ = self._get(canonical, robots_policy)
        except (FetchBlocked, httpx2.HTTPError):
            return None
        if response.status_code >= 500:
            return None
        if response.status_code in (401, 403):
            parser.disallow_all = True
            return parser
        if response.status_code >= 400:
            parser.allow_all = True
            return parser
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        return parser

    def _get(
        self, url: str, policy: FetchPolicy
    ) -> tuple[httpx2.Response, bytes, bool, list[str]]:
        hops: list[str] = []
        current = url
        for _ in range(policy.max_redirects + 1):
            canonical, ip = self.check_target(current, policy)
            request = self._build_request(canonical, ip, policy.timeout_seconds)
            response = self._client.send(request, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchBlocked("redirect without Location header")
                    hops.append(canonical)
                    current = urljoin(canonical, location)
                    continue
                body, truncated = _read_capped(response, policy.max_bytes)
            finally:
                response.close()
            hops.append(canonical)
            return response, body, truncated, hops
        raise FetchBlocked(f"more than {policy.max_redirects} redirects")

    def _build_request(self, url: str, ip: str, timeout: float) -> httpx2.Request:
        if not self._pin or _is_ip_literal(urlsplit(url).hostname or ""):
            return self._client.build_request("GET", url, timeout=timeout)
        parts = urlsplit(url)
        host_for_url = f"[{ip}]" if ":" in ip else ip
        pinned = urlunsplit(("https", host_for_url, parts.path, parts.query, ""))
        return self._client.build_request(
            "GET", pinned, headers={"Host": parts.hostname or ""},
            extensions={"sni_hostname": parts.hostname}, timeout=timeout,
        )


def _read_capped(response: httpx2.Response, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            return b"".join(chunks)[:limit], True
    return b"".join(chunks), False


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
