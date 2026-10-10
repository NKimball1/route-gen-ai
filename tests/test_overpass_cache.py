"""Overpass disk cache (routes/interruptions.py) -- offline, mocked HTTP.

The public Overpass servers time out often; four interval searches in one
week lost their stop counts to it. Answers are cached on disk, reused
while fresh, and the last good copy is used when every mirror is down.
"""
import json
import os
import time

import pytest
import requests

from routes import interruptions as ix


class Resp:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(ix, "CACHE_DIR", str(tmp_path))
    return tmp_path


def up(payload, calls):
    def post(url, **kw):
        calls.append(url)
        return Resp(payload)
    return post


def down(calls):
    def post(url, **kw):
        calls.append(url)
        raise requests.Timeout("504 gateway timeout")
    return post


def nodes(*pts):
    return {"elements": [{"lat": la, "lon": lo, "tags": {"highway": "stop"}} for la, lo in pts]}


def test_fresh_answer_is_reused_without_the_network(monkeypatch):
    calls = []
    monkeypatch.setattr(ix.requests, "post", up({"elements": []}, calls))
    ix.query_overpass("Q")
    ix.query_overpass("Q")
    assert len(calls) == 1


def test_stale_copy_beats_unknown_when_overpass_is_down(monkeypatch, cache_in_tmp):
    calls = []
    monkeypatch.setattr(ix.requests, "post", up({"elements": [1]}, calls))
    ix.query_overpass("Q")
    for f in os.listdir(cache_in_tmp):                  # age the cache past fresh
        p = os.path.join(cache_in_tmp, f)
        e = json.load(open(p))
        e["ts"] = time.time() - 30 * 86400
        json.dump(e, open(p, "w"))
    monkeypatch.setattr(ix.requests, "post", down(calls))
    assert ix.query_overpass("Q") == {"elements": [1]}
    assert len(calls) == 1 + len(ix.OVERPASS_URLS)      # it did try the network first


def test_nothing_cached_and_overpass_down_is_still_none(monkeypatch):
    monkeypatch.setattr(ix.requests, "post", down([]))
    assert ix.query_overpass("never seen") is None
    assert ix.fetch_controls(43.0, -89.4, 1000) is None


def test_corrupt_cache_file_is_a_miss_not_a_crash(monkeypatch, cache_in_tmp):
    open(os.path.join(cache_in_tmp, ix.CONTROLS_PREFIX + "bad.json"), "w").write("{not json")
    monkeypatch.setattr(ix.requests, "post", up(nodes((43.0, -89.4)), []))
    assert ix.fetch_controls(43.0, -89.4, 1000) == [(43.0, -89.4, 1.0, ix.CONTROL_ON_ROUTE_M, False)]


def test_one_big_fetch_answers_smaller_searches_inside_it(monkeypatch):
    calls = []
    inner, outer = (43.0005, -89.4005), (43.08, -89.4)   # ~9 km apart
    monkeypatch.setattr(ix.requests, "post", up(nodes(inner, outer), calls))
    big = ix.fetch_controls(43.0, -89.4, 20000)
    assert len(big) == 2 and len(calls) == 1
    small = ix.fetch_controls(43.0, -89.4, 2000)         # a different area, same region
    assert small == [(inner[0], inner[1], 1.0, ix.CONTROL_ON_ROUTE_M, False)]  # trimmed to the smaller box
    assert len(calls) == 1                               # no network at all


def test_covering_fetch_is_used_even_stale_when_overpass_is_down(monkeypatch, cache_in_tmp):
    monkeypatch.setattr(ix.requests, "post", up(nodes((43.0005, -89.4005)), []))
    ix.fetch_controls(43.0, -89.4, 20000)
    for f in os.listdir(cache_in_tmp):
        p = os.path.join(cache_in_tmp, f)
        e = json.load(open(p))
        e["ts"] = time.time() - 60 * 86400
        json.dump(e, open(p, "w"))
    monkeypatch.setattr(ix.requests, "post", down([]))
    got = ix.fetch_controls(43.0, -89.4, 3000)
    assert got == [(43.0005, -89.4005, 1.0, ix.CONTROL_ON_ROUTE_M, False)]


# ---- busy mirrors get a second chance; dead ones do not ----

@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(ix.time, "sleep", waited.append)
    return waited


def scripted(answers, calls):
    """Each mirror replies with the next status in its own list."""
    def post(url, **kw):
        calls.append(url)
        status = answers[url].pop(0)
        if status == "timeout":
            raise requests.Timeout("read timed out")
        return Resp({"elements": [url]}, status)
    return post


def test_a_busy_mirror_is_asked_again_after_a_pause(monkeypatch, sleeps):
    """Seen live: overpass-api.de answered 504 in 8 s, then 200 a moment
    later -- while the other mirror sat silent for its full 90 s."""
    first, second = ix.OVERPASS_URLS
    calls = []
    monkeypatch.setattr(ix.requests, "post", scripted({first: [504, 200], second: ["timeout"]}, calls))
    assert ix.query_overpass("Q") == {"elements": [first]}
    assert calls == [first, second, first]
    assert sleeps == [ix.OVERPASS_RETRY_WAIT_S]


def test_a_mirror_that_stays_busy_is_asked_only_twice(monkeypatch, sleeps):
    first, second = ix.OVERPASS_URLS
    calls = []
    monkeypatch.setattr(ix.requests, "post", scripted({first: [429, 429], second: [503, 503]}, calls))
    assert ix.query_overpass("Q") is None
    assert calls == [first, second, first, second]


def test_silent_mirrors_are_not_waited_on_twice(monkeypatch, sleeps):
    calls = []
    monkeypatch.setattr(ix.requests, "post", down(calls))
    assert ix.query_overpass("Q") is None
    assert len(calls) == len(ix.OVERPASS_URLS) and sleeps == []


def test_cached_lists_from_before_reach_existed_are_not_read_back(monkeypatch, cache_in_tmp):
    """Old lists clustered side-street stops together with the road's own;
    reading one back would bring the over-count with it."""
    (cache_in_tmp / "controls_old.json").write_text(json.dumps(
        {"ts": time.time(), "bbox": [42.0, -90.0, 44.0, -89.0], "controls": [[43.0, -89.4, 1.0]]}))
    calls = []
    monkeypatch.setattr(ix.requests, "post", up(nodes((43.0005, -89.4005)), calls))
    assert ix.fetch_controls(43.0, -89.4, 1000) == [(43.0005, -89.4005, 1.0, ix.CONTROL_ON_ROUTE_M, False)]
    assert len(calls) == 1


def test_cached_lists_from_before_trail_crossings_are_not_read_back(monkeypatch, cache_in_tmp):
    """A list cached before crossings were fetched has none: reading it back
    would show a trail with road crossings as uninterrupted for a week."""
    (cache_in_tmp / "controls2_old.json").write_text(json.dumps(
        {"ts": time.time(), "bbox": [42.0, -90.0, 44.0, -89.0],
         "controls": [[43.0, -89.4, 1.0, ix.CONTROL_ON_ROUTE_M]]}))
    calls = []
    crossing = {"elements": [{"lat": 43.0005, "lon": -89.4005, "tags": {"highway": "crossing"}}]}
    monkeypatch.setattr(ix.requests, "post", up(crossing, calls))
    assert ix.fetch_controls(43.0, -89.4, 1000) == [ix.trail_crossing(43.0005, -89.4005)]
    assert len(calls) == 1


def test_a_crossing_node_is_fetched_as_a_trail_crossing_and_cached_as_one(monkeypatch):
    calls = []
    payload = {"elements": [
        {"lat": 43.0005, "lon": -89.4005, "tags": {"highway": "crossing", "crossing": "unmarked"}},
        {"lat": 43.001, "lon": -89.401, "tags": {"highway": "stop"}}]}
    monkeypatch.setattr(ix.requests, "post", up(payload, calls))
    fetched = ix.fetch_controls(43.0, -89.4, 2000)
    assert ix.trail_crossing(43.0005, -89.4005) in fetched
    assert (43.001, -89.401, 1.0, ix.CONTROL_ON_ROUTE_M, False) in fetched
    assert sorted(ix.fetch_controls(43.0, -89.4, 1500)) == sorted(fetched)   # from the cache
    assert len(calls) == 1
