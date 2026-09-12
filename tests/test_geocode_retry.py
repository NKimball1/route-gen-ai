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


def test_hard_client_error_does_not_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(g.requests, "get",
                        lambda url, **kw: calls.append(url) or FakeResp(400))
    with pytest.raises(requests.HTTPError):
        g.geocode("Madison WI")
    assert len(calls) == 1  # a bad request stays bad — no point retrying
