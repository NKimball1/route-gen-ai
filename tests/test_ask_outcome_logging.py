"""A finished ask's usage row says how it went, not just that it finished.

Live 11-12: with the Claude API key rejected, a request failed at the
parser and was logged {"event": "ask_done"} -- the same row as a route
the rider got. So were "clarify" bounces, searches that found nothing and
partial results: the log could count requests but not whether they worked.
"""
import time

import pytest

import api
from routes import limits, service

SID = "outcome-log-session"


@pytest.fixture
def events(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "JOBS", {})
    monkeypatch.setattr(api, "RUNNING", {})
    seen = []
    monkeypatch.setattr(limits, "log_event", lambda event, **kw: seen.append((event, kw)))
    return seen


def finish(monkeypatch, result):
    monkeypatch.setattr(service, "handle_request", lambda text, log_sink, **kw: result)
    api.JOBS["j"] = {"sid": SID, "status": "running", "result": None, "buf": api.JobLog(), "ts": time.time()}
    api.RUNNING[SID] = "j"
    api._run("j", "ride", None, api.session_dir(SID))


@pytest.mark.parametrize("result,kind,ok,routes", [
    ({"kind": "route", "ok": True, "summary": "3 loops", "candidates": [{}, {}, {}]}, "route", True, 3),
    ({"kind": "interval_spot", "ok": "partial", "summary": "1 spot", "candidates": [{}]}, "interval_spot", "partial", 1),
    ({"kind": "error", "ok": False, "summary": "the server's API key needs replacing", "candidates": []}, "error", False, 0),
    ({"kind": "clarify", "ok": False, "summary": "How far?", "candidates": []}, "clarify", False, 0),
], ids=["routes", "partial spot", "parser down", "clarify"])
def test_a_finished_ask_is_logged_with_its_outcome(events, monkeypatch, result, kind, ok, routes):
    finish(monkeypatch, result)
    ((event, row),) = events
    assert event == "ask_done"
    assert (row["kind"], row["ok"], row["routes"]) == (kind, ok, routes)


def test_the_summary_stays_out_of_the_log(events, monkeypatch):
    # Summaries quote the rider's places ("Couldn't find '...' on the map").
    finish(monkeypatch, {"kind": "error", "ok": False, "candidates": [],
                         "summary": "Couldn't find 'Capitol Square' on the map."})
    ((_, row),) = events
    assert "Capitol" not in str(row)
