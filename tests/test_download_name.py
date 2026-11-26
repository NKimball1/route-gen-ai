"""A downloaded GPX is named for the ride, not for the app's bookkeeping.

Every file on disk carries a 32-character id so names never collide, and
an edit keeps its parent's id as well as its own -- so a rider's Downloads
folder (and the course list their Garmin import shows) filled with names
like route_40mi_ride_1_loop_brouter_<32 hex>_edit_<32 hex>.gpx.
"""
import os
import re

from fastapi.testclient import TestClient
import pytest

import api

SID = "download-name-session"
ID_A, ID_B = "3f" * 16, "9b" * 16


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    return TestClient(api.app)


def saved_as(client, stem):
    workdir = api.session_dir(SID)
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, f"{stem}.gpx")
    with open(path, "w", encoding="utf-8") as f:
        f.write("<gpx/>")
    response = client.get("/api/gpx", params={"path": path}, headers={"X-Session-Id": SID})
    assert response.status_code == 200
    return re.search(r'filename="([^"]+)"', response.headers["content-disposition"]).group(1)


def test_a_new_route_downloads_without_its_id(client):
    assert saved_as(client, f"route_40mi_ride_1_loop_brouter_{ID_A}") == "route_40mi_ride_1_loop_brouter.gpx"


def test_an_edit_downloads_without_either_id(client):
    assert saved_as(client, f"route_40mi_ride_1_loop_brouter_{ID_A}_edit_{ID_B}") == \
        "route_40mi_ride_1_loop_brouter_edit.gpx"


def test_a_name_without_an_id_is_left_alone(client):
    assert saved_as(client, "upload_Monona_loop") == "upload_Monona_loop.gpx"
