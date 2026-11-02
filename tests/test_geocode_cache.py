"""Nominatim answers are cached (routes/geocode.py) -- offline, mocked HTTP.

Nominatim's usage policy requires clients to cache results. Nothing was
cached: every page load looked the saved start address up again, and so
did every ride "from home" -- the same question, the same answer, another
request against a service that blocks heavy users (and another slot of
the rider's own hourly lookup limit).
"""
import pytest
import requests

from routes import geocode as g

HIT = [{"lat": "43.0747", "lon": "-89.3842", "display_name": "Capitol Square, Madison, WI"}]


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def nominatim(monkeypatch):
    """Answers queued per call; records each request's query text."""
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    monkeypatch.setattr(g, "wait_for_nominatim", lambda: None)
    calls, answers = [], []

    def fake_get(url, params=None, **kw):
        calls.append(params["q"])
        return answers.pop(0) if answers else FakeResp(200, HIT)
    monkeypatch.setattr(g.requests, "get", fake_get)
    return calls, answers


def test_the_same_place_is_asked_once(nominatim):
    calls, _ = nominatim
    first = g.geocode("Capitol Square, Madison WI")
    assert g.geocode("Capitol Square, Madison WI") == first
    assert calls == ["Capitol Square, Madison WI"]


def test_a_place_that_does_not_exist_is_remembered_too(nominatim):
    calls, answers = nominatim
    answers.append(FakeResp(200, []))
    for _ in range(2):
        with pytest.raises(g.GeocodeNotFound):
            g.geocode("Nowhere Road, Atlantis")
    assert len(calls) == 1


def test_a_failed_lookup_is_not_remembered(nominatim):
    calls, answers = nominatim
    answers.extend([FakeResp(503)] * g.RETRIES)
    with pytest.raises(g.GeocodeUnavailable):
        g.geocode("Capitol Square, Madison WI")
    assert g.geocode("Capitol Square, Madison WI")[2] == "Capitol Square, Madison, WI"
    assert len(calls) == g.RETRIES + 1


def test_different_queries_are_different_questions(nominatim):
    calls, _ = nominatim
    g.geocode("Capitol Square, Madison WI")
    g.geocode("Monona Terrace, Madison WI")
    assert len(calls) == 2


def test_answers_expire(nominatim, monkeypatch):
    calls, _ = nominatim
    monkeypatch.setattr(g, "ANSWER_TTL_S", 0.0)
    g.geocode("Capitol Square, Madison WI")
    g.geocode("Capitol Square, Madison WI")
    assert len(calls) == 2


def test_the_cache_stays_bounded(nominatim, monkeypatch):
    monkeypatch.setattr(g, "ANSWER_KEEP", 2)
    for place in ("Capitol Square", "Monona Terrace", "Olbrich Gardens"):
        g.geocode(place + ", Madison WI")
    assert len(g._ANSWERS) == 2
