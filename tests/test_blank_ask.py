"""A blank request is turned away before it costs anything.

The ask body only had to be one character long, so a request of spaces
passed: it took one of the session's hourly asks, started a job and sent
nothing to Claude to read. The page trims before sending; the API did not.
"""
from collections import defaultdict, deque
import time

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits, service

HEADERS = {"X-Session-Id": "blank-ask-session"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(api, "JOBS", {})
    monkeypatch.setattr(api, "RUNNING", {})
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    return TestClient(api.app)


@pytest.fixture
def asked(monkeypatch):
    seen = []
    def handle(text, log_sink, default_address=None, **kw):
        seen.append((text, default_address))
        return {"kind": "clarify", "ok": False, "summary": "?", "candidates": []}
    monkeypatch.setattr(service, "handle_request", handle)
    return seen


@pytest.mark.parametrize("text", ["   ", "\n\t "])
def test_a_blank_request_is_refused_and_spends_nothing(client, asked, text):
    response = client.post("/api/ask", headers=HEADERS, json={"text": text})
    assert response.status_code == 422
    assert not api.JOBS and not asked
    assert not limits._WINDOWS                          # no hourly ask used up


def wait_for(asked):
    for job in list(api.JOBS):
        while api.JOBS[job]["status"] == "running":
            time.sleep(0.01)
    return asked


def test_padding_is_trimmed_off_a_request(client, asked):
    assert client.post("/api/ask", headers=HEADERS,
                       json={"text": "  20 mile loop \n", "start": "  Monona Terrace "}).status_code == 200
    assert wait_for(asked) == [("20 mile loop", "Monona Terrace")]


def test_a_blank_start_means_no_start(client, asked):
    assert client.post("/api/ask", headers=HEADERS, json={"text": "20 mile loop", "start": "  "}).status_code == 200
    assert wait_for(asked) == [("20 mile loop", None)]
