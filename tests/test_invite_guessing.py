"""Wrong invite codes are counted per IP and stop being checked past a cap.

The invite code is the only thing between the open internet and the
Claude bill, and it could be guessed at network speed: every wrong code
got a fresh 401, none was logged, none counted against anything.
"""
from collections import defaultdict, deque
import json

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits

CODE = "spoke-wrench"
SESSION = {"X-Session-Id": "invite-guess-session"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", CODE)
    monkeypatch.setattr(limits.Limits, "INVITE_MISSES_PER_IP_HOUR", 3)
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    return TestClient(api.app)


def get(client, code=None):
    headers = {**SESSION, **({"X-Invite-Code": code} if code is not None else {})}
    return client.get("/api/running", headers=headers)


def rows():
    try:
        with open(limits.USAGE_LOG, encoding="utf-8") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def test_the_right_code_still_works(client):
    assert get(client, CODE).status_code == 200


def test_guessing_stops_at_the_cap(client):
    assert [get(client, f"guess-{n}").status_code for n in range(4)] == [401, 401, 401, 429]


def test_past_the_cap_even_the_right_code_waits(client):
    # Otherwise the cap only slows a guesser down until they hit it.
    for n in range(3):
        get(client, f"guess-{n}")
    assert get(client, CODE).status_code == 429


def test_a_missing_code_is_not_a_guess(client):
    # A first visit sends several requests before the rider is asked.
    for _ in range(5):
        assert get(client).status_code == 401
    assert get(client, CODE).status_code == 200


def test_a_wrong_code_is_logged_without_the_code(client):
    get(client, "guess-zero")
    (row,) = rows()
    assert row["event"] == "invite_refused"
    assert row["ip"] == limits.pseudonymize_ip("testclient")
    assert "guess-zero" not in json.dumps(row)
