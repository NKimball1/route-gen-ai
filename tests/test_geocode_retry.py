"""Nominatim retry behavior (routes/geocode.py) — offline, mocked HTTP."""
import pytest
import requests

from routes import geocode as g


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


HIT = [{"lat": "43.05", "lon": "-89.45", "display_name": "Madison, WI"}]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(g.time, "sleep", lambda s: None)


def test_recovers_from_transient_429(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return FakeResp(429) if len(calls) < 3 else FakeResp(200, HIT)

    monkeypatch.setattr(g.requests, "get", fake_get)
    assert g.geocode("Madison WI") == (43.05, -89.45, "Madison, WI")
    assert len(calls) == 3


def test_recovers_from_a_timeout(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise requests.Timeout("read timed out")
        return FakeResp(200, HIT)

    monkeypatch.setattr(g.requests, "get", fake_get)
    assert g.geocode("Madison WI")[2] == "Madison, WI"


def test_gives_up_after_max_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(g.requests, "get",
                        lambda url, **kw: calls.append(url) or FakeResp(503))
    with pytest.raises(RuntimeError, match="503"):
        g.geocode("Madison WI")
    assert len(calls) == g.RETRIES


def test_an_unreadable_query_is_not_found_not_an_outage(monkeypatch):
    """HTTP 400 is Nominatim refusing this text, not Nominatim being down:
    it used to reach the rider as "temporarily unavailable, try again in a
    moment", which no amount of trying again fixes (live 10-21)."""
    calls = []
    monkeypatch.setattr(g.requests, "get",
                        lambda url, **kw: calls.append(url) or FakeResp(400))
    with pytest.raises(g.GeocodeNotFound, match="couldn't read"):
        g.geocode("Madison WI")
    assert len(calls) == 1  # a bad request stays bad — no point retrying


def test_other_client_errors_are_still_the_service(monkeypatch):
    """403 means this server is blocked, 404 a wrong URL: problems on our
    side that rephrasing cannot fix."""
    for status in (403, 404):
        monkeypatch.setattr(g.requests, "get", lambda url, **kw: FakeResp(status))
        with pytest.raises(g.GeocodeUnavailable, match=str(status)):
            g.geocode("Madison WI")


def test_a_rejected_full_text_falls_back_to_a_simpler_query(monkeypatch):
    """Not-found keeps geocode_flexible's fallback chain going; an outage
    used to end it at the first rejected query."""
    queries = []

    def get(url, params=None, **kw):
        queries.append(params["q"])
        return FakeResp(400) if "," in params["q"] else FakeResp(200, HIT)
    monkeypatch.setattr(g.requests, "get", get)
    assert g.geocode_flexible("Monona Terrace, ~~~")[2] == "Madison, WI"
    assert queries == ["Monona Terrace, ~~~", "Monona Terrace"]
