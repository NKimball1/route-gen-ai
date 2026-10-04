"""HTTP-level isolation, mutation, rate-limit, and cancellation regressions."""
from collections import defaultdict, deque
import threading
import time

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits, storage, service

SID = "test-session-one"
OTHER = "test-session-two"
HEADERS = {"X-Session-Id": SID, "X-Invite-Code": "test-invite"}
GPX = b'<gpx><trk><trkseg><trkpt lat="43" lon="-89"><ele>200</ele></trkpt><trkpt lat="43.01" lon="-89"><ele>201</ele></trkpt></trkseg></trk></gpx>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path))
    monkeypatch.setattr(api, "INVITE_CODE", "test-invite")
    monkeypatch.setattr(api, "JOBS", {})
    monkeypatch.setattr(api, "RUNNING", {})
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    monkeypatch.setattr(limits, "log_event", lambda *a, **kw: None)
    with TestClient(api.app) as client:
        yield client


def upload(client, content=GPX):
    response = client.post("/api/upload", headers=HEADERS, files={"file": ("same.gpx", content)})
    assert response.status_code == 200, response.text
    return response.json()["candidates"][0]["gpx"]


def test_same_name_uploads_are_immutable_and_undoable(client):
    first = upload(client)
    before = client.get("/api/gpx", headers=HEADERS, params={"path": first}).content
    second = upload(client, GPX.replace(b'43.01', b'43.02'))
    assert first != second
    assert client.get("/api/gpx", headers=HEADERS, params={"path": first}).content == before
    undone = client.post("/api/undo", headers=HEADERS)
    assert undone.json()["candidates"][0]["gpx"] == first
    restored = client.get("/api/current", headers=HEADERS).json()
    assert restored["current"] == first and restored["candidates"][0]["latlngs"]


def test_downloads_and_jobs_require_both_invite_and_ownership(client):
    path = upload(client)
    assert client.get("/api/gpx", params={"path": path}).status_code == 400
    assert client.get("/api/gpx", headers={"X-Session-Id": SID}, params={"path": path}).status_code == 401
    other = {**HEADERS, "X-Session-Id": OTHER}
    assert client.get("/api/gpx", headers=other, params={"path": path}).status_code == 400
    api.JOBS["job"] = {"sid": SID, "status": "done", "result": {}, "buf": api.JobLog(), "ts": time.time()}
    assert client.get("/api/job/job", headers=other).status_code == 404
    assert client.get("/api/job/job", headers=HEADERS).status_code == 200
    assert client.get("/api/job/missing", headers=HEADERS).status_code == 404


def test_cancel_waits_for_worker_and_never_commits_staged_route(client, monkeypatch):
    first = upload(client)
    started, release = threading.Event(), threading.Event()
    def dispatch(text, address, workdir, intent="auto"):
        path = storage.artifact_path(workdir, "cancelled")
        with open(path, "wb") as output:
            output.write(GPX)
        storage.publish(path, workdir)
        started.set()
        assert release.wait(5)
        return {"kind": "route", "ok": True, "candidates": []}
    monkeypatch.setattr(service, "_dispatch", dispatch)
    response = client.post("/api/ask", headers=HEADERS, json={"text": "ride"})
    job = response.json()["job"]
    assert started.wait(5)
    try:
        assert client.post("/api/cancel", headers=HEADERS).status_code == 200
        assert client.post("/api/ask", headers=HEADERS, json={"text": "next"}).status_code == 429
        assert client.post("/api/undo", headers=HEADERS).status_code == 409
        assert client.post("/api/current", headers=HEADERS, json={"text": first}).status_code == 409
        assert client.post("/api/upload", headers=HEADERS, files={"file": ("x.gpx", GPX)}).status_code == 409
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while api.JOBS[job]["status"] == "running" and time.monotonic() < deadline:
        time.sleep(.01)
    assert api.JOBS[job]["status"] == "cancelled"
    assert SID not in api.RUNNING
    assert client.get("/api/current", headers=HEADERS).json()["current"] == first


def test_busy_requests_do_not_spend_quota(client):
    api.RUNNING[SID] = "running"
    response = client.post("/api/ask", headers=HEADERS, json={"text": "ride"})
    assert response.status_code == 429
    assert not limits._WINDOWS


def test_rejected_requests_do_not_drain_other_users_global_budget(client):
    responses = [limits.check_ask(SID, "one-ip") for _ in range(400)]
    assert sum(r is None for r in responses) == limits.Limits.ASK_PER_SESSION_HOUR
    assert len(limits._WINDOWS[("ask_g",)]) == limits.Limits.ASK_PER_SESSION_HOUR
    assert limits.check_ask(OTHER, "another-ip") is None


def test_forged_forwarding_header_does_not_change_ip_identity(client, monkeypatch):
    identities = []
    monkeypatch.setattr(limits, "check_geocode", lambda ip: identities.append(ip) or "test refusal")
    for spoofed in ("1.2.3.4", "5.6.7.8"):
        client.get("/api/geocode", headers={**HEADERS, "X-Forwarded-For": spoofed}, params={"q": "public"})
    assert identities[0] == identities[1]


def test_oversize_uploads_preserve_current(client):
    first = upload(client)
    assert client.post("/api/upload", headers=HEADERS, files={"file": ("x.gpx", b'x' * (api.UPLOAD_MAX_BYTES + 1))}).status_code == 413
    assert client.get("/api/current", headers=HEADERS).json()["current"] == first


def test_error_after_cancel_cannot_leave_a_zombie_job(client, monkeypatch):
    def fail(text, log_sink, **kw):
        log_sink.cancelled = True
        raise RuntimeError("late provider failure")
    monkeypatch.setattr(service, "handle_request", fail)
    api.JOBS["failure"] = {"sid": SID, "status": "running", "result": None, "buf": api.JobLog(), "ts": time.time()}
    api.RUNNING[SID] = "failure"
    api._run("failure", "ride", None, api.session_dir(SID))
    assert api.JOBS["failure"]["status"] == "cancelled"
    assert SID not in api.RUNNING
