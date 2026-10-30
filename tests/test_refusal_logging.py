"""Every 429 leaves a row in the usage log.

The log recorded asks and their outcomes but no refusal: a full daily
cap (the cost backstop) or one abuser hammering the limits turned riders
away with nothing to show for it afterwards.
"""
from collections import defaultdict, deque
import json

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits

HEADERS = {"X-Session-Id": "refusal-test-session"}
GPX = b'<gpx><trk><trkseg><trkpt lat="43" lon="-89"/><trkpt lat="43.01" lon="-89"/></trkseg></trk></gpx>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(api, "JOBS", {})
    monkeypatch.setattr(api, "RUNNING", {})
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    return TestClient(api.app)


def rows():
    with open(limits.USAGE_LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.mark.parametrize("knob,reason", [
    ("ASK_GLOBAL_DAY", "daily capacity reached - try again tomorrow"),
    ("JOBS_CONCURRENT", "The server is busy - try again shortly."),
])
def test_a_refused_ask_is_logged_with_its_reason(client, monkeypatch, knob, reason):
    monkeypatch.setattr(limits.Limits, knob, 0)
    response = client.post("/api/ask", headers=HEADERS, json={"text": "10 mile loop"})
    assert response.status_code == 429 and response.json()["error"] == reason
    (row,) = rows()
    assert row["event"] == "ask_refused" and row["reason"] == reason
    assert row["sid"] == HEADERS["X-Session-Id"]
    assert row["ip"] == limits.pseudonymize_ip("testclient")
    assert "10 mile loop" not in json.dumps(row)     # a refusal carries no typed text
    assert not api.RUNNING and not api.JOBS


def test_a_second_ask_while_one_runs_is_logged(client):
    api.RUNNING[HEADERS["X-Session-Id"]] = "busy-job"
    assert client.post("/api/ask", headers=HEADERS, json={"text": "ride"}).status_code == 429
    assert [r["event"] for r in rows()] == ["ask_refused"]


def test_a_refused_upload_is_logged(client, monkeypatch):
    monkeypatch.setattr(limits.Limits, "UPLOAD_PER_IP_HOUR", 0)
    response = client.post("/api/upload", headers=HEADERS, files={"file": ("r.gpx", GPX)})
    assert response.status_code == 429
    (row,) = rows()
    assert row["event"] == "upload_refused" and row["reason"] == response.json()["error"]


def test_a_refused_lookup_is_logged(client, monkeypatch):
    monkeypatch.setattr(limits.Limits, "GEOCODE_PER_IP_HOUR", 0)
    response = client.get("/api/geocode", headers=HEADERS, params={"q": "Capitol Square, Madison WI"})
    assert response.status_code == 429
    (row,) = rows()
    assert row["event"] == "geocode_refused" and "Capitol" not in json.dumps(row)
