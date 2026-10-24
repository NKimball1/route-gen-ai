"""Browsers check for a newer page and script on every load.

Live 10-23: after app.js changed, the browser kept running its cached copy.
The app sent no Cache-Control, so browsers guessed freshness from file age
(commonly a tenth of it: the three-week-old index page could be reused for
two days without asking). "no-cache" makes them revalidate each load, which
costs a 304 with no body when nothing changed.
"""
import pytest
from fastapi.testclient import TestClient

import api


@pytest.mark.parametrize("url", ["/", "/assets/app.js", "/about"])
def test_frontend_files_are_revalidated_every_load(url):
    response = TestClient(api.app).get(url)
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-cache"


def test_an_unchanged_script_still_costs_only_a_304():
    client = TestClient(api.app)
    etag = client.get("/assets/app.js").headers["etag"]
    again = client.get("/assets/app.js", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
