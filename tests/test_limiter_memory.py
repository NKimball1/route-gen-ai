"""The rate limiter's table forgets visitors once their windows have passed.

Nothing was ever removed from it: one key per session id and per IP, for
the life of the process. Session ids are made up by the client, and an ask
refused for its IP still left an empty window behind for its new session
id -- so one IP could grow the table by a key per request, forever.
"""
from collections import defaultdict, deque

import pytest

from routes import limits

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def fresh_table(monkeypatch):
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    monkeypatch.setattr(limits, "_last_sweep", 0.0)


def test_refused_asks_leave_nothing_behind(monkeypatch):
    monkeypatch.setattr(limits.Limits, "ASK_PER_IP_HOUR", 1)
    assert limits.check_ask("first-session", "198.51.100.7") is None
    before = len(limits._WINDOWS)
    for n in range(50):
        assert limits.check_ask(f"made-up-{n}", "198.51.100.7") is not None
    assert len(limits._WINDOWS) == before


def test_windows_long_past_are_forgotten():
    for n in range(50):
        limits.allow(("geo", f"203.0.113.{n}"), 30, limits.HOUR_S, now=NOW)
    limits.allow(("geo", "192.0.2.1"), 30, limits.HOUR_S, now=NOW + limits.DAY_S + 1)
    assert set(limits._WINDOWS) == {("geo", "192.0.2.1")}


def test_a_window_still_counting_is_kept():
    limits.allow(("ask_g",), 400, limits.DAY_S, now=NOW)
    limits.allow(("geo", "192.0.2.1"), 30, limits.HOUR_S, now=NOW + limits.DAY_S - 60)
    assert len(limits._WINDOWS[("ask_g",)]) == 1


def test_forgetting_does_not_reset_a_limit_in_force():
    for _ in range(3):
        assert limits.allow(("upload", "192.0.2.9"), 3, limits.HOUR_S, now=NOW)
    later = NOW + limits._SWEEP_EVERY_S + 1           # a sweep runs; the hour has not passed
    limits.allow(("geo", "192.0.2.1"), 30, limits.HOUR_S, now=later)
    assert not limits.allow(("upload", "192.0.2.9"), 3, limits.HOUR_S, now=later)


def test_a_closed_limit_still_refuses_on_an_empty_table(monkeypatch):
    # RATE_ASK_GLOBAL_DAY=0 shuts asks off; skipping keys not yet in the
    # table must not skip their limits.
    monkeypatch.setattr(limits.Limits, "ASK_GLOBAL_DAY", 0)
    assert limits.check_ask("any-session", "192.0.2.1") == "daily capacity reached - try again tomorrow"
    assert not limits._WINDOWS
