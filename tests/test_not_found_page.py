"""A mistyped or stale link shows a page with a way back, not raw JSON.

A rider following an old link or mistyping /about got `{"error":"not
found"}` -- or `{"detail":"Not Found"}` one level deeper -- as the whole
page, with nothing to click. The API's 404s stay JSON for the page's code.
"""
from fastapi.testclient import TestClient
import pytest

import api


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.mark.parametrize("path", ["/abuot", "/About", "/rides/2026/loop", "/about.html"])
def test_a_page_that_does_not_exist_is_a_page(client, path):
    response = client.get(path)
    assert response.status_code == 404                 # still a 404 to crawlers
    assert response.headers["content-type"].startswith("text/html")
    assert 'href="/"' in response.text and 'href="/about"' in response.text
    assert 'name="robots" content="noindex"' in response.text


@pytest.mark.parametrize("path", ["/api/nope", "/api/job"])
def test_the_api_still_answers_in_json(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_a_missing_asset_is_not_dressed_as_a_page(client):
    # A script or stylesheet tag gains nothing from HTML; keep it small.
    response = client.get("/assets/nope.js")
    assert response.status_code == 404
    assert not response.headers["content-type"].startswith("text/html")


def test_real_pages_are_untouched(client):
    assert client.get("/about").status_code == 200
    assert client.get("/").status_code == 200
