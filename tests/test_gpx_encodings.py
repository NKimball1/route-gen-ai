"""Uploads read the encoding a GPX file is actually in.

The upload path decoded every file as UTF-8. A valid GPX from an exporter
writing Latin-1 or UTF-16 was refused, and any refusal of this kind showed
the rider Python's own words: "'utf-8' codec can't decode byte 0xe9 in
position 62: invalid continuation byte".
"""
from fastapi.testclient import TestClient
import pytest

import api
from routes.gpx_in import decode_gpx

HEADERS = {"X-Session-Id": "gpx-encoding-session"}
GPX = ('<?xml version="1.0" encoding="{}"?><gpx><trk><name>Café Loop</name><trkseg>'
       '<trkpt lat="43.0747" lon="-89.3842"/><trkpt lat="43.0800" lon="-89.3842"/>'
       '</trkseg></trk></gpx>')
FIT = b"\x0e\x10\xd9\x07\x85\x00\x00\x00.FIT\x90\xff\x00\x00"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "SESSIONS_ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(api, "INVITE_CODE", None)
    return TestClient(api.app)


def send(client, data):
    return client.post("/api/upload", headers=HEADERS, files={"file": ("ride.gpx", data)})


@pytest.mark.parametrize("data", [
    GPX.format("UTF-8").encode("utf-8"),
    b"\xef\xbb\xbf" + GPX.format("UTF-8").encode("utf-8"),
    GPX.format("ISO-8859-1").encode("latin-1"),
    GPX.format("windows-1252").encode("cp1252"),
    GPX.format("UTF-16").encode("utf-16"),
], ids=["utf-8", "utf-8 with BOM", "latin-1", "windows-1252", "utf-16"])
def test_a_valid_gpx_uploads_whatever_its_encoding(client, data):
    response = send(client, data)
    assert response.status_code == 200, response.text
    assert "Café" in decode_gpx(data)


def test_a_file_that_mislabels_itself_is_read_as_what_it_is():
    # Plain UTF-8 under a stale Latin-1 declaration must not become "CafÃ©".
    assert "Café" in decode_gpx(GPX.format("ISO-8859-1").encode("utf-8"))


@pytest.mark.parametrize("data,words", [
    (FIT, "FIT file"),
    (b"\x89PNG\r\n\x1a\n\xff\xfe\xfd", "isn't a GPX"),
    (GPX.format("no-such-encoding").encode("latin-1"), "isn't a GPX"),
], ids=["fit", "binary", "unknown declared encoding"])
def test_an_unreadable_file_gets_plain_words(client, data, words):
    response = send(client, data)
    assert response.status_code == 400
    assert words in response.json()["detail"]
    assert "codec" not in response.json()["detail"]


def test_entities_are_still_refused_once_decoded(client):
    hidden = '<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "b">]><gpx></gpx>'.encode("utf-16")
    response = send(client, hidden)
    assert response.status_code == 400 and "DTD" in response.json()["detail"]
