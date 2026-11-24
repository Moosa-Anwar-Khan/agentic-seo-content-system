import json

import httpx
import pytest

from src.integrations.wordpress import (WordPressReadOnly, WordPressResponseError,
                                       decode_public_collection)


PAGES = [{'id': 10, 'link': 'https://example.org/page',
          'title': {'rendered': 'Page'}, 'content': {'rendered': '<h2>Overview</h2>'},
          'modified': '2026-10-12T10:00:00'}]
PREFIX = '<style id="elementor-post-10">.elementor-10{display:flex}</style>'


def response(text):
    return httpx.Response(200, text=text, headers={'Content-Type': 'application/json'},
                          request=httpx.Request('GET', 'https://example.org/wp-json/wp/v2/pages'))


def test_standard_json_collection_is_unchanged():
    assert decode_public_collection(response(json.dumps(PAGES))) == PAGES
    assert decode_public_collection(response('[]')) == []


def test_elementor_prefix_is_discarded_and_reported(caplog):
    text = PREFIX + '<style id="elementor-post-11">.other{color:#123}</style>'
    # The real endpoint also emits custom font CSS outside its opening style tag.
    text += "/* End custom CSS */\n@font-face{font-family:'Cabin';src:;} </style>"
    assert decode_public_collection(response(text + json.dumps(PAGES))) == PAGES
    assert 'Ignored leading Elementor stylesheet output' in caplog.text


def test_css_brackets_do_not_become_page_data():
    prefix = '<style id="elementor-post-10">.a[data-x="[]"]{display:grid}</style>'
    assert decode_public_collection(response(prefix + json.dumps(PAGES))) == PAGES


@pytest.mark.parametrize('text', [
    '<html><body>Service unavailable</body></html>',
    '<script>alert(1)</script>' + json.dumps(PAGES),
    PREFIX + '<script>alert(1)</script>' + json.dumps(PAGES),
    PREFIX + '<iframe src="https://example.org"></iframe>' + json.dumps(PAGES),
    PREFIX + '<!DOCTYPE html>' + json.dumps(PAGES),
    PREFIX + '<?php bad output ?>' + json.dumps(PAGES),
    PREFIX + json.dumps(PAGES) + '<script>alert(1)</script>',
    PREFIX + '[{"id":10, malformed]',
    json.dumps({'code': 'rest_forbidden'}),
    '[1,2,3]',
])
def test_unexpected_or_incomplete_output_is_rejected(text):
    with pytest.raises(WordPressResponseError):
        decode_public_collection(response(text))


def test_stylesheet_prefix_size_is_bounded():
    with pytest.raises(WordPressResponseError):
        decode_public_collection(response(PREFIX + ' ' * (2 * 1024 * 1024) + json.dumps(PAGES)))


def test_recovery_preserves_read_only_request_and_pagination(settings, monkeypatch):
    requests = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def get(self, url, params):
            requests.append((url, params))
            result = response(PREFIX + json.dumps(PAGES))
            result.headers['X-WP-TotalPages'] = '3'
            return result
    monkeypatch.setattr('src.integrations.wordpress.httpx.Client', Client)
    pages, count = WordPressReadOnly(settings).request('GET', 'pages', {'page': 1, 'per_page': 25})
    assert pages == PAGES and count == 3
    assert requests[0][1]['context'] == 'view'
    assert requests[0][1]['status'] == 'publish'
    with pytest.raises(PermissionError):
        WordPressReadOnly(settings).request('POST', 'pages')
