"""Every response tells browsers not to frame the app or sniff its types.

No response set either: any site could load the app in a hidden frame and
steer a rider's clicks (Undo, Cancel, an upload) -- clickjacking -- and a
browser was free to guess at a file's type instead of taking the one sent.
"""
from fastapi.testclient import TestClient
import pytest

import api

FRAMING = {"x-frame-options": "DENY", "content-security-policy": "frame-ancestors 'none'"}


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.mark.parametrize("path", ["/", "/about", "/assets/app.js", "/no-such-page",
                                  "/api/running"])            # a 401 without a session
def test_no_response_can_be_framed_or_sniffed(client, path):
    headers = client.get(path).headers
    for name, value in FRAMING.items():
        assert headers.get(name) == value, (path, name)
    assert headers.get("x-content-type-options") == "nosniff", path


def test_a_page_header_is_not_doubled(client):
    assert client.get("/").headers.get_list("x-frame-options") == ["DENY"]
