"""Every upload leaves a row in the usage log, whatever became of it.

Asks and refusals were logged, uploads were not: an operator could not
tell whether anyone used the upload path, nor how often a rider's file
was turned away (and why -- the parser is strict about split rides and
bad coordinates), nor whether files arrived too large.
"""
from collections import defaultdict, deque
import json

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits

HEADERS = {"X-Session-Id": "upload-log-session"}
GOOD = (b'<gpx><trk><trkseg><trkpt lat="43.0747" lon="-89.3842"/>'
        b'<trkpt lat="43.0800" lon="-89.3842"/></trkseg></trk></gpx>')
BAD_POINT = b'<gpx><trk><trkseg><trkpt lat="." lon="-89"/><trkpt lat="43" lon="-89"/></trkseg></trk></gpx>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    return TestClient(api.app)


def rows():
    with open(limits.USAGE_LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def send(client, data, name="Lunch Ride.gpx"):
    return client.post("/api/upload", headers=HEADERS, files={"file": (name, data)})


def test_a_good_upload_is_logged(client):
    assert send(client, GOOD).status_code == 200
    (row,) = rows()
    assert row["event"] == "upload" and row["outcome"] == "ok"
    assert row["sid"] == HEADERS["X-Session-Id"]
    assert row["ip"] == limits.pseudonymize_ip("testclient")
    assert row["bytes"] == len(GOOD)
    assert "Lunch" not in json.dumps(row)       # the filename is the rider's, not the log's


def test_a_rejected_file_is_logged_with_why(client):
    response = send(client, BAD_POINT)
    assert response.status_code == 400
    (row,) = rows()
    assert row["outcome"] == "rejected" and row["reason"] == response.json()["detail"]


def test_an_oversize_file_is_logged(client, monkeypatch):
    monkeypatch.setattr(api, "UPLOAD_MAX_BYTES", 10)
    assert send(client, GOOD).status_code == 413
    (row,) = rows()
    assert row["outcome"] == "too_large"


def test_an_unexpected_failure_is_logged(client, monkeypatch):
    def broken(*args):
        raise OSError("disk full")
    monkeypatch.setattr(api, "_save_upload", broken)
    with pytest.raises(OSError):
        send(client, GOOD)
    (row,) = rows()
    assert row["outcome"] == "error" and row["error"] == "OSError"
