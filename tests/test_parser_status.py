"""Is the plain-English parser usable? Health reports it before a rider finds out.

Live 10-14: the server's Claude API key had been rejected, /api/health said
everything was fine (it only checked BRouter), and the end-to-end simulator
reported ten separate failures for that one cause.
"""
import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from routes import nl

REQUEST = httpx.Request("GET", "https://api.anthropic.com/v1/models")


def _status_error(cls, status):
    return cls("boom", response=httpx.Response(status, request=REQUEST), body=None)


class FakeClient:
    def __init__(self, error=None):
        self.error, self.calls = error, 0
        outer = self

        class Models:
            def list(self, **kw):
                outer.calls += 1
                if outer.error is not None:
                    raise outer.error
                return []
        self.models = Models()


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(nl, "_PARSER_STATUS", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")


@pytest.mark.parametrize("error,status", [
    (None, "ok"),
    (_status_error(anthropic.AuthenticationError, 401), "key_rejected"),
    (_status_error(anthropic.PermissionDeniedError, 403), "key_rejected"),
    (_status_error(anthropic.RateLimitError, 429), "rate_limited"),
    (_status_error(anthropic.OverloadedError, 529), "unreachable"),
    (_status_error(anthropic.InternalServerError, 500), "unreachable"),
    (anthropic.APIConnectionError(request=REQUEST), "unreachable"),
])
def test_parser_status_names_the_problem(error, status):
    assert nl.parser_status(FakeClient(error)) == status


def test_no_key_is_known_without_calling_anyone(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    client = FakeClient()
    assert nl.parser_status(client) == "no_key"
    assert client.calls == 0


def test_the_answer_is_reused_for_a_few_minutes(monkeypatch):
    """Health checks can poll every few seconds; the check is free but not
    instant, so one answer serves PARSER_STATUS_TTL_S."""
    clock = [1000.0]
    monkeypatch.setattr(nl.time, "monotonic", lambda: clock[0])
    client = FakeClient()
    nl.parser_status(client)
    nl.parser_status(client)
    assert client.calls == 1
    clock[0] += nl.PARSER_STATUS_TTL_S + 1
    nl.parser_status(client)
    assert client.calls == 2


@pytest.mark.parametrize("parser,code,ok", [("ok", 200, True), ("key_rejected", 503, False)])
def test_health_reports_the_parser(monkeypatch, parser, code, ok):
    import api
    monkeypatch.setattr("routes.providers.brouter_reachable", lambda url, timeout_s=1.0: True)
    monkeypatch.setattr(nl, "parser_status", lambda client=None: parser)
    response = TestClient(api.app).get("/api/health")
    assert response.status_code == code
    body = response.json()
    assert body["parser"] == parser and body["ok"] is ok and body["brouter"] == "up"
