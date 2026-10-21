"""One address lookup costs a handful of Nominatim requests, never hundreds.

Found 10-21: geocode_flexible retries with ever-shorter comma-prefixes of
the text, one per comma-part, and nothing bounded the parts. Nominatim
answers a 50-part string with "no results" (checked live), so that text cost
50 requests, doubled for lookups bounded near a route -- most of two minutes
at Nominatim's 1 request/second policy, holding a server worker, counted by
the rate limiter as one lookup. Empty parts (", , ,") also went out as empty
queries, and /api/geocode took a query of any length.
"""
import pytest
from fastapi.testclient import TestClient

from routes import geocode as g


@pytest.fixture
def nominatim(monkeypatch):
    calls = []
    monkeypatch.setattr(g, "_nominatim_get", lambda params: calls.append(params["q"]) or [])
    return calls


def lookup(text, **kw):
    with pytest.raises(g.GeocodeNotFound):
        g.geocode_flexible(text, **kw)


def test_a_comma_heavy_string_costs_a_bounded_number_of_requests(nominatim):
    lookup(",".join(["x"] * 300))
    assert len(nominatim) <= g.MAX_QUERY_VARIANTS
    nominatim.clear()
    lookup(",".join(["x"] * 300), near=(43.0, -89.5, 43.1, -89.3))
    assert len(nominatim) <= 2 * g.MAX_QUERY_VARIANTS


def test_empty_parts_never_become_queries(nominatim):
    lookup(", , Madison, , WI ,")
    assert nominatim and all(q.strip() for q in nominatim)
    assert nominatim[0] == "Madison, WI"


def test_a_real_address_still_gets_every_fallback(nominatim):
    lookup("Monona Terrace, Madison, WI, 53703, USA")
    assert nominatim == ["Monona Terrace, Madison, WI, 53703, USA",
                         "Monona Terrace, Madison, WI, 53703",
                         "Monona Terrace, Madison, WI", "Monona Terrace, Madison",
                         "Monona Terrace"]


@pytest.mark.parametrize("q", ["", "   ", "x" * 201])
def test_the_geocode_endpoint_refuses_empty_or_overlong_text(monkeypatch, q):
    from collections import defaultdict, deque
    import api
    from routes import limits
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    monkeypatch.setattr("routes.geocode.geocode_flexible",
                        lambda *a, **kw: pytest.fail("looked up an unusable query"))
    response = TestClient(api.app).get("/api/geocode", params={"q": q},
                                       headers={"X-Session-Id": "test-session-0001"})
    assert response.status_code == 400
    assert "address" in response.json()["error"].lower()
