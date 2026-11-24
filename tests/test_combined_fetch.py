"""Guarded fetching (ADR-0010): classification, SSRF protection, redirects, robots, limits."""

import httpx2
import pytest

from src.combined.enums import FetchStatus
from src.combined.fetch import FetchPolicy, is_public_ip

from tests.combined_fakeweb import FakeWeb, article

SITE = "https://steuerkanzlei.example"
OWN = FetchPolicy(allowed_domain="steuerkanzlei.example")
ANY = FetchPolicy()


@pytest.fixture
def web() -> FakeWeb:
    w = FakeWeb()
    w.page(f"{SITE}/robots.txt", "User-agent: *\nDisallow: /intern/\n", content_type="text/plain")
    return w


def test_article_with_main_is_read(web):
    web.page(f"{SITE}/blog/a", article("AfA"))
    result = web.fetcher().fetch(f"{SITE}/blog/a", OWN)
    assert result.status is FetchStatus.READ
    assert "Inhalt" in result.text and "Menü" not in result.text and "Impressum" not in result.text
    assert result.extraction.headings == ["AfA", "Abschnitt"]
    assert result.text_sha256 and len(result.text_sha256) == 64


def test_body_only_page_is_marked_heuristic(web):
    web.page(f"{SITE}/b", article("B", tag="div"))
    result = web.fetcher().fetch(f"{SITE}/b", OWN)
    assert result.status is FetchStatus.HEURISTIC
    assert "heuristic" in result.note


def test_javascript_shell_is_not_read(web):
    web.page(f"{SITE}/app", '<html><body><div id="root"></div><script src="a.js"></script></body>')
    result = web.fetcher().fetch(f"{SITE}/app", OWN)
    assert result.status is FetchStatus.JS_SHELL
    assert result.text is None


@pytest.mark.parametrize("ctype,status", [("application/pdf", FetchStatus.PDF),
                                          ("image/png", FetchStatus.NON_HTML)])
def test_non_html_is_marked(web, ctype, status):
    web.page(f"{SITE}/file", b"\x00\x01", content_type=ctype)
    result = web.fetcher().fetch(f"{SITE}/file", OWN)
    assert result.status is status and result.text is None


def test_http_error_is_failed(web):
    result = web.fetcher().fetch(f"{SITE}/missing", OWN)
    assert result.status is FetchStatus.FAILED and result.note == "HTTP 404"


def test_timeout_is_failed(web):
    web.fail(f"{SITE}/slow", lambda r: httpx2.ReadTimeout("slow", request=r))
    result = web.fetcher().fetch(f"{SITE}/slow", OWN)
    assert result.status is FetchStatus.FAILED and result.note == "timeout"


@pytest.mark.parametrize("url", [
    "http://steuerkanzlei.example/a",
    "ftp://steuerkanzlei.example/a",
    "https://steuerkanzlei.example:8443/a",
    "https://user:pw@steuerkanzlei.example/a",
    "https://example.com/a",
])
def test_disallowed_targets_are_blocked_without_a_request(web, url):
    result = web.fetcher().fetch(url, OWN)
    assert result.status is FetchStatus.BLOCKED
    assert web.requests == []


@pytest.mark.parametrize("host_ip", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1",
                                     "::1", "fd00::1", "::ffff:10.0.0.1", "100.64.0.1"])
def test_private_addresses_are_blocked(web, host_ip):
    web.dns["intranet.example.com"] = host_ip
    result = web.fetcher().fetch("https://intranet.example.com/", ANY)
    assert result.status is FetchStatus.BLOCKED and "non-public" in result.note
    assert web.requests == []


def test_ip_literal_targets_are_checked():
    assert not is_public_ip("127.0.0.1")
    assert is_public_ip("93.184.216.34")


def test_redirect_to_foreign_domain_is_blocked_before_contact(web):
    web.redirect(f"{SITE}/weiter", "https://example.com/landing")
    web.page("https://example.com/landing", article("fremd"))
    result = web.fetcher().fetch(f"{SITE}/weiter", OWN)
    assert result.status is FetchStatus.BLOCKED and "outside the project domain" in result.note
    assert "https://example.com/landing" not in web.requested_urls


def test_redirect_to_private_address_is_blocked(web):
    web.dns["evil.example.net"] = "10.1.2.3"
    web.page("https://example.org/robots.txt", "", content_type="text/plain")
    web.redirect("https://example.org/x", "https://evil.example.net/admin")
    result = web.fetcher().fetch("https://example.org/x", ANY)
    assert result.status is FetchStatus.BLOCKED
    assert not any("evil" in u for u in web.requested_urls)


def test_redirect_downgrade_to_http_is_blocked(web):
    web.redirect(f"{SITE}/alt", "http://steuerkanzlei.example/neu")
    assert web.fetcher().fetch(f"{SITE}/alt", OWN).status is FetchStatus.BLOCKED


def test_redirect_within_domain_is_followed(web):
    web.redirect(f"{SITE}/alt", "/neu")
    web.page(f"{SITE}/neu", article("Neu"))
    result = web.fetcher().fetch(f"{SITE}/alt", OWN)
    assert result.status is FetchStatus.READ
    assert result.final_url == f"{SITE}/neu" and result.redirects == [f"{SITE}/alt"]


def test_redirect_loop_is_stopped(web):
    web.redirect(f"{SITE}/a", f"{SITE}/b")
    web.redirect(f"{SITE}/b", f"{SITE}/a")
    result = web.fetcher().fetch(f"{SITE}/a", OWN)
    assert result.status is FetchStatus.BLOCKED and "redirects" in result.note


def test_robots_disallow_is_respected(web):
    web.page(f"{SITE}/intern/x", article("geheim"))
    result = web.fetcher().fetch(f"{SITE}/intern/x", OWN)
    assert result.status is FetchStatus.BLOCKED and "robots" in result.note
    assert f"{SITE}/intern/x" not in web.requested_urls


def test_unreachable_robots_blocks_conservatively():
    web = FakeWeb()
    web.page(f"{SITE}/robots.txt", "", status=503)
    web.page(f"{SITE}/a", article("A"))
    assert web.fetcher().fetch(f"{SITE}/a", OWN).status is FetchStatus.BLOCKED


def test_missing_robots_allows():
    web = FakeWeb()
    web.page(f"{SITE}/a", article("A"))
    assert web.fetcher().fetch(f"{SITE}/a", OWN).status is FetchStatus.READ


def test_size_limit_marks_partial(web):
    web.page(f"{SITE}/long", article("Lang", text="Wort " * 5000))
    small = FetchPolicy(allowed_domain="steuerkanzlei.example", max_bytes=2000)
    result = web.fetcher().fetch(f"{SITE}/long", small)
    assert result.truncated
    assert result.status is FetchStatus.PARTIAL


def test_pinning_connects_to_the_checked_ip_with_original_host(web):
    web.page(f"{SITE}/robots.txt", "", content_type="text/plain")
    web.routes["https://93.184.216.34/robots.txt"] = web.routes[f"{SITE}/robots.txt"]
    fetcher = web.fetcher(pin_ips=True)
    fetcher.fetch(f"{SITE}/a", OWN)
    request = web.requests[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "steuerkanzlei.example"
    assert request.extensions["sni_hostname"] == "steuerkanzlei.example"


def test_article_cards_are_not_taken_as_main_text(web):
    cards = "".join(f"<article><h3>Karte {i}</h3><p>Kurz.</p></article>" for i in range(8))
    html = f"<html><body><section><h1>Immobilien</h1><p>{'Text ' * 300}</p></section>{cards}</body>"
    web.page(f"{SITE}/branche", html)
    result = web.fetcher().fetch(f"{SITE}/branche", OWN)
    assert result.status is FetchStatus.HEURISTIC
    assert "Immobilien" in result.text and "Karte 7" in result.text


def test_tiny_main_is_not_trusted(web):
    html = f"<html><body><main><p>Cookie-Hinweis</p></main><div>{'Inhalt ' * 300}</div></body>"
    web.page(f"{SITE}/x", html)
    result = web.fetcher().fetch(f"{SITE}/x", OWN)
    assert result.status is FetchStatus.HEURISTIC and "Inhalt" in result.text
