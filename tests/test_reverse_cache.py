"""Reverse lookups ("which road is this?") are cached too -- offline, mocked HTTP.

Forward geocodes were cached since 10-30; reverse ones were not. Every
interval spot names its road with three reverse lookups at Nominatim's
one-per-second pace, so asking for the same intervals again repeated all
of them: about three seconds a spot, and requests the usage policy says
a client should have kept.
"""
import pytest
import requests

from routes import geocode, places

ADDRESS = {"address": {"road": "County Highway Y", "village": "Mazomanie"}}


class Answer:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = ADDRESS if payload is None else payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def nominatim(monkeypatch):
    """Answers queued per call; records each request's coordinates."""
    monkeypatch.setattr(places, "wait_for_nominatim", lambda: None)
    calls, answers = [], []

    def fake_get(url, params=None, **kw):
        calls.append((params["lat"], params["lon"]))
        return answers.pop(0) if answers else Answer()
    monkeypatch.setattr(places.requests, "get", fake_get)
    return calls, answers


def test_the_same_point_is_asked_once(nominatim):
    calls, _ = nominatim
    assert places.road_at(43.15, -89.79) == ("County Highway Y", "Mazomanie")
    assert places.road_at(43.15, -89.79) == ("County Highway Y", "Mazomanie")
    assert len(calls) == 1


def test_a_point_a_hair_away_is_the_same_question(nominatim):
    calls, _ = nominatim
    places.road_at(43.150001, -89.790001)     # ~0.1 m: float noise, not a new place
    places.road_at(43.150002, -89.790002)
    assert len(calls) == 1


def test_a_point_on_no_road_is_remembered_too(nominatim):
    calls, answers = nominatim
    answers.append(Answer(payload={"address": {"county": "Dane County"}}))
    assert places.road_at(43.15, -89.79) is None
    assert places.road_at(43.15, -89.79) is None
    assert len(calls) == 1


def test_a_failed_lookup_is_not_remembered(nominatim):
    calls, answers = nominatim
    answers.append(Answer(503))
    assert places.road_at(43.15, -89.79) is None
    assert places.road_at(43.15, -89.79) == ("County Highway Y", "Mazomanie")
    assert len(calls) == 2


def test_different_points_are_different_questions(nominatim):
    calls, _ = nominatim
    places.road_at(43.15, -89.79)
    places.road_at(43.16, -89.79)
    assert len(calls) == 2


def test_reverse_answers_share_the_bounded_cache(nominatim, monkeypatch):
    monkeypatch.setattr(geocode, "ANSWER_KEEP", 2)
    for lat in (43.15, 43.16, 43.17):
        places.road_at(lat, -89.79)
    assert len(geocode._ANSWERS) == 2
