"""A reloaded page can find the request still running for its session.

The page told riders to "refresh to see its result", but a refreshed page
had no way to learn a job was running: no progress, no Cancel, and every
new request refused with "A request is already running for this session."
"""
from fastapi.testclient import TestClient
import pytest

import api

MINE = {"X-Session-Id": "running-job-mine"}
THEIRS = {"X-Session-Id": "running-job-theirs"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(api, "RUNNING", {})
    return TestClient(api.app)


def test_nothing_running_says_so(client):
    assert client.get("/api/running", headers=MINE).json() == {"job": None}


def test_the_running_job_is_named_to_its_own_session_only(client):
    api.RUNNING[MINE["X-Session-Id"]] = "job-123"
    assert client.get("/api/running", headers=MINE).json() == {"job": "job-123"}
    assert client.get("/api/running", headers=THEIRS).json() == {"job": None}


def test_it_needs_a_session(client):
    assert client.get("/api/running").status_code == 400
