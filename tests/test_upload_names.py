"""An uploaded route keeps the name its rider gave it.

Upload renamed every route after its filename, squeezed to [A-Za-z0-9_-]:
a ride named "Tuesday Night Worlds" in a file Garmin Connect exported as
COURSE_8812.gpx came back -- in the list, and as the course name on the
next Garmin import -- as "COURSE_8812", and "Lake Loop.gpx" as "Lake_Loop".
"""
from collections import defaultdict, deque
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import pytest

import api
from routes import limits

HEADERS = {"X-Session-Id": "upload-names-session"}
POINTS = "".join(f'<trkpt lat="{la}" lon="{lo}"><ele>260</ele></trkpt>'
                 for la, lo in [(43.0747, -89.3842), (43.0735, -89.3826), (43.0723, -89.3811)])
NS = "{http://www.topografix.com/GPX/1/1}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    monkeypatch.setattr(api, "RUNNING", {})
    monkeypatch.setattr(limits, "_WINDOWS", defaultdict(deque))
    return TestClient(api.app)


def upload(client, filename, name=None):
    named = f"<name>{name}</name>" if name else ""
    body = (f'<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk>{named}'
            f"<trkseg>{POINTS}</trkseg></trk></gpx>")
    response = client.post("/api/upload", headers=HEADERS,
                           files={"file": (filename, body.encode(), "application/gpx+xml")})
    assert response.status_code == 200, response.text
    candidate = response.json()["candidates"][0]
    saved = ET.parse(candidate["gpx"]).getroot().find(f"{NS}trk/{NS}name").text
    return saved, candidate["label"]


def test_the_routes_own_name_is_kept(client):
    saved, label = upload(client, "COURSE_8812.gpx", "Tuesday Night Worlds")
    assert saved == "Tuesday Night Worlds"
    assert label.startswith("uploaded: Tuesday Night Worlds - ")


def test_without_a_name_the_filename_reads_as_typed(client):
    saved, label = upload(client, "Lake Loop.gpx")
    assert saved == "Lake Loop" and "uploaded: Lake Loop - " in label


def test_a_name_on_several_lines_becomes_one(client):
    saved, label = upload(client, "x.gpx", "Lake\n\t  Loop ")
    assert saved == "Lake Loop" and "\n" not in label


def test_a_long_name_is_cut_short(client):
    saved, _ = upload(client, "x.gpx", "Loop " * 40)
    assert len(saved) <= api.ROUTE_NAME_MAX_CHARS


def test_nothing_unprintable_reaches_the_file():
    # The test client percent-encodes such filenames; browsers may not.
    assert api._route_name("Lake\x01Loop​") == "LakeLoop"
    assert api._route_name("\x01 \t") == "route"
