"""A request the API can't accept says why in words the page can show.

FastAPI's 422 body is a list of error objects under "detail"; the page
shows `error` or a string `detail`, so a rider whose request ran past 600
characters (nothing in the page stops them) saw only "Request failed (422)."
"""
from collections import defaultdict, deque

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits

HEADERS = {"X-Session-Id": "readable-422-session"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(api, "JOBS", {})
    monkeypatch.setattr(api, "RUNNING", {})
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    return TestClient(api.app)


def refusal(client, body):
    response = client.post("/api/ask", headers=HEADERS, json=body)
    assert response.status_code == 422
    return response.json()["error"]


def test_a_request_too_long_says_how_long_it_may_be(client):
    assert refusal(client, {"text": "x" * 601}) == "Keep the request under 600 characters."


def test_a_blank_request_says_to_type_one(client):
    assert refusal(client, {"text": "   "}) == "Type a request first."


def test_a_start_too_long_says_how_long_it_may_be(client):
    assert refusal(client, {"text": "20 mile loop", "start": "x" * 201}) == \
        "The start address can be up to 200 characters."


def test_anything_else_still_names_the_field(client):
    message = refusal(client, {"text": "20 mile loop", "intent": "teleport"})
    assert message.startswith("That request couldn't be read") and "intent" in message


def test_the_refused_text_is_not_echoed_back(client):
    # FastAPI's default body repeats the whole input; the rider has it already.
    response = client.post("/api/ask", headers=HEADERS, json={"text": "Monona " * 100})
    assert "Monona" not in response.text
