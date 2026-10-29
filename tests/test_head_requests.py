"""HEAD gets the same answer as GET, without the body.

Live 10-28: the preview tool's first probe, `HEAD /`, got 405 Method Not
Allowed while GET / served the page. Uptime monitors and `curl -I` check
a site with HEAD, so a deployed box would have looked down while serving
fine -- and /api/health, the endpoint built for monitors, refused it too.
Only the /assets mount (StaticFiles) answered HEAD.
"""
import pytest
from fastapi.testclient import TestClient

import api
from routes import nl


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("routes.providers.brouter_reachable", lambda url, timeout_s=1.0: True)
    monkeypatch.setattr(nl, "parser_status", lambda client=None: "ok")
    return TestClient(api.app)


@pytest.mark.parametrize("url", ["/", "/about", "/assets/app.js", "/api/health", "/no-such-page"])
def test_head_answers_like_get_without_a_body(client, url):
    got = client.get(url)
    head = client.head(url)
    assert head.status_code == got.status_code
    assert head.content == b""
    assert head.headers.get("content-type") == got.headers.get("content-type")


def test_an_unhealthy_server_says_so_to_head_too(client, monkeypatch):
    monkeypatch.setattr(nl, "parser_status", lambda client=None: "key_rejected")
    assert client.head("/api/health").status_code == 503
