"""Web session folders are cleaned up once nothing has written to them for a while.

Devlog open item (phase 39): session folders were never cleaned up, so a public
server's disk grows with every browser that ever visited. Retention is opt-in
(ROUTEGEN_SESSION_DAYS); unset keeps everything, as before.
"""
import os
import threading

import pytest

from routes import storage

DAY = 86400.0
NOW = 1_800_000_000.0


def session(root, name, age_days, files=("state.json", "route_a.gpx")):
    folder = root / name
    folder.mkdir(parents=True)
    stamp = NOW - age_days * DAY
    for file in files:
        (folder / file).write_text("x")
        os.utime(folder / file, (stamp, stamp))
    os.utime(folder, (stamp, stamp))
    return folder


def test_stale_sessions_go_and_recent_ones_stay(tmp_path):
    stale = session(tmp_path, "stale-session", 40)
    recent = session(tmp_path, "recent-session", 5)
    removed = storage.prune_sessions(str(tmp_path), 30 * DAY, now=NOW)
    assert removed == [str(stale)]
    assert not stale.exists() and recent.exists()


def test_one_recent_file_keeps_the_whole_session(tmp_path):
    """Age is the newest write anywhere in the folder: an old first route
    does not doom a session that was edited yesterday."""
    folder = session(tmp_path, "edited-session", 40)
    (folder / "edit_b.gpx").write_text("x")
    os.utime(folder / "edit_b.gpx", (NOW - DAY, NOW - DAY))
    assert storage.prune_sessions(str(tmp_path), 30 * DAY, now=NOW) == []
    assert folder.exists()


def test_a_session_in_use_is_never_deleted(tmp_path):
    folder = session(tmp_path, "busy-session", 40)
    inside, release = threading.Event(), threading.Event()

    def hold():
        with storage.transaction(str(folder)):
            inside.set()
            release.wait(5)
    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert inside.wait(5)
        assert storage.prune_sessions(str(tmp_path), 30 * DAY, now=NOW) == []
        assert folder.exists()
    finally:
        release.set()
        worker.join()


def test_kept_names_are_skipped_and_a_missing_root_is_fine(tmp_path):
    folder = session(tmp_path, "starting-session", 40)
    assert storage.prune_sessions(str(tmp_path), 30 * DAY, now=NOW,
                                  keep={"starting-session"}) == []
    assert folder.exists()
    assert storage.prune_sessions(str(tmp_path / "nope"), 30 * DAY, now=NOW) == []


@pytest.fixture
def api_module(tmp_path, monkeypatch):
    import api
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path))
    monkeypatch.setattr(api, "_LAST_PRUNE", 0.0)
    return api


def test_retention_is_off_unless_configured(api_module, tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "SESSION_MAX_AGE_S", 0.0)
    folder = session(tmp_path, "old-session", 400)
    api_module._prune_sessions(NOW)
    assert folder.exists()


def test_the_server_prunes_at_most_hourly_and_spares_running_jobs(api_module, tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "SESSION_MAX_AGE_S", 30 * DAY)
    monkeypatch.setattr(api_module, "RUNNING", {"running-session": "job"})
    running = session(tmp_path, "running-session", 40)
    first = session(tmp_path, "old-session-1", 40)
    api_module._prune_sessions(NOW)
    assert not first.exists() and running.exists()
    second = session(tmp_path, "old-session-2", 40)
    api_module._prune_sessions(NOW + 60)
    assert second.exists()
    api_module._prune_sessions(NOW + 3601)
    assert not second.exists()
