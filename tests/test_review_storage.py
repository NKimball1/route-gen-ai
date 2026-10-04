"""Real filesystem and threaded regressions for route-state integrity."""
import threading
from pathlib import Path

import pytest

from routes import pipeline, storage
from routes.execution import Cancellation, JobCancelled
from routes.gpx_out import write_track
from routes.spec import RouteSpec, RouteCandidate


def artifact(workdir, name="ride", lat=43.0):
    path = storage.artifact_path(str(workdir), name)
    write_track([(lat, -89, 0), (lat + .01, -89, 0)], name, "test", path)
    return path


def test_repeated_generation_preserves_geometry_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "geocode", lambda a: (43, -89, "public"))
    monkeypatch.setattr(pipeline, "build_preview", lambda *a: None)
    spec = RouteSpec.from_imperial("public", 10)

    class Provider:
        name = "test"
        latitude = 43

        def candidates(self, *a, **kw):
            return [RouteCandidate(self.name, "s", spec.distance_m, 0,
                    points=[(self.latitude, -89, 0), (self.latitude + .01, -89, 0)])]

    provider = Provider()
    first = pipeline.compose([spec], [provider], out_dir=str(tmp_path))[0].gpx_path
    contents = Path(first).read_bytes()
    provider.latitude = 44
    second = pipeline.compose([spec], [provider], out_dir=str(tmp_path))[0].gpx_path
    assert first != second
    assert Path(first).read_bytes() == contents
    assert storage.current_route(str(tmp_path)) == second
    assert storage.undo(str(tmp_path)) == first


def test_cancel_discards_selection_and_parent_changes(tmp_path):
    first, second = artifact(tmp_path), artifact(tmp_path)
    storage.publish(first, str(tmp_path))
    token = Cancellation()
    with pytest.raises(JobCancelled):
        with storage.transaction(str(tmp_path), cancellation=token):
            storage.publish(second, str(tmp_path))
            assert storage.current_route(str(tmp_path)) == second  # staged
            token.cancel()
    assert storage.current_route(str(tmp_path)) == first
    assert storage.predecessor(second) is None


def test_failure_rolls_back_and_committed_token_refuses_late_cancel(tmp_path):
    first, second = artifact(tmp_path), artifact(tmp_path)
    storage.publish(first, str(tmp_path))
    with pytest.raises(RuntimeError):
        with storage.transaction(str(tmp_path)):
            storage.publish(second, str(tmp_path))
            raise RuntimeError("provider failed")
    assert storage.current_route(str(tmp_path)) == first
    token = Cancellation()
    with storage.transaction(str(tmp_path), cancellation=token):
        storage.publish(second, str(tmp_path))
    assert token.finished and not token.cancel()
    assert storage.current_route(str(tmp_path)) == second


def test_a_second_writer_cannot_enter_a_running_session(tmp_path):
    ready, release = threading.Event(), threading.Event()

    def worker():
        with storage.transaction(str(tmp_path)):
            ready.set()
            assert release.wait(5)

    thread = threading.Thread(target=worker)
    thread.start()
    assert ready.wait(5)
    try:
        with pytest.raises(storage.SessionBusy):
            with storage.transaction(str(tmp_path), blocking=False):
                pytest.fail("entered another request's session")
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()


def test_parentage_is_immutable_and_selection_does_not_rewrite_it(tmp_path):
    a, b, c = [artifact(tmp_path) for _ in range(3)]
    storage.publish(a, str(tmp_path))
    storage.publish(b, str(tmp_path))
    storage.select_route(a, str(tmp_path))
    storage.publish(c, str(tmp_path))
    assert storage.predecessor(b) == a
    assert storage.predecessor(c) == a
    with pytest.raises(ValueError, match="immutable"):
        storage.record_parent(b, c)


def test_containment_rejects_sibling_prefixes_and_traversal(tmp_path):
    session = tmp_path / "session"
    sibling = tmp_path / "session-other"
    session.mkdir()
    sibling.mkdir()
    assert storage.owned_path(str(session / "r.gpx"), str(session))
    assert not storage.owned_path(str(sibling / "r.gpx"), str(session))
    assert not storage.owned_path(str(session / ".." / "r.gpx"), str(session))


def test_atomic_state_failure_preserves_the_previous_selection(tmp_path, monkeypatch):
    a, b = artifact(tmp_path), artifact(tmp_path)
    storage.publish(a, str(tmp_path))
    def fail_replace(*args):
        raise OSError("disk full")
    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError):
        storage.publish(b, str(tmp_path))
    assert storage.current_route(str(tmp_path)) == a
    assert not list(tmp_path.glob("*.tmp"))


def test_cancel_after_preview_cannot_commit_generated_routes(tmp_path, monkeypatch):
    baseline = artifact(tmp_path)
    storage.publish(baseline, str(tmp_path))
    monkeypatch.setattr(pipeline, "geocode", lambda a: (43, -89, "public"))
    token = Cancellation()
    monkeypatch.setattr(pipeline, "build_preview", lambda *a: token.cancel())
    spec = RouteSpec("public", 1000)
    class Provider:
        name = "test"
        def candidates(self, *a, **kw):
            return [RouteCandidate("test", "s", 1000, 0, points=[(43, -89, 0), (43.01, -89, 0)])]
    with pytest.raises(JobCancelled):
        with storage.transaction(str(tmp_path), cancellation=token):
            pipeline.compose([spec], [Provider()], out_dir=str(tmp_path))
    assert storage.current_route(str(tmp_path)) == baseline
    assert storage.predecessor(baseline) is None


def test_damaged_history_is_set_aside_not_a_locked_session(tmp_path):
    """A truncated state.json (crash mid-copy, disk full) used to make every
    request fail. Now the current route survives, new work commits, and the
    damaged file is kept for diagnosis."""
    first, second = artifact(tmp_path, "a"), artifact(tmp_path, "b")
    storage.publish(first, str(tmp_path))
    storage.publish(second, str(tmp_path))
    (tmp_path / storage.STATE_FILE).write_text('{"current": "', encoding="utf-8")

    assert storage.current_route(str(tmp_path)) == second
    kept = list(tmp_path.glob(storage.STATE_FILE + ".damaged-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == '{"current": "'

    third = artifact(tmp_path, "c")
    storage.publish(third, str(tmp_path))
    assert storage.current_route(str(tmp_path)) == third
    assert storage.undo(str(tmp_path)) == second
