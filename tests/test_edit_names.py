"""An edited route keeps its ride's name in the GPX, which Garmin shows.

An edit was named after its parent's file -- storage id and all -- so
importing an edited route gave a Garmin course called
route_40mi_ride_1_loop_brouter_<32 hex> edit instead of "40mi ride #1 edit".
"""
import os
import xml.etree.ElementTree as ET

import pytest

from routes import edit_service
from routes.editing import EditResult
from routes.gpx_out import write_track
from routes.storage import artifact_path

CAPITOL_TO_MONONA = [(43.0747, -89.3842, 263.0), (43.0735, -89.3826, 261.0),
                     (43.0723, -89.3811, 259.0), (43.0713, -89.3801, 262.0)]


@pytest.fixture(autouse=True)
def no_router(monkeypatch):
    monkeypatch.setattr(edit_service, "extend_route", lambda points, meters, provider: EditResult(
        points=points + [(43.0705, -89.3790, 260.0)], distance_m=1200.0, ascent_m=5.0,
        overlap_frac=0.0, detours=1, removed_m=0.0, added_m=150.0))


def gpx_name(path):
    root = ET.parse(path).getroot()
    return root.find("{http://www.topografix.com/GPX/1/1}trk/{http://www.topografix.com/GPX/1/1}name").text


def edit(path, out_dir):
    out, _, ok = edit_service.run_edit(path, mode="extend", miles_delta=1.0, out_dir=out_dir)
    assert ok is True
    return out


def saved(tmp_path, stem, name):
    path = artifact_path(str(tmp_path), stem)
    write_track(CAPITOL_TO_MONONA, name, "0.4 mi", path)
    return path


def test_an_edit_is_named_after_the_ride(tmp_path):
    parent = saved(tmp_path, "route_40mi_ride_1_loop_brouter", "40mi ride #1")
    assert gpx_name(edit(parent, str(tmp_path))) == "40mi ride #1 edit"


def test_editing_an_edit_does_not_stack(tmp_path):
    parent = saved(tmp_path, "route_40mi_ride_1_loop_brouter", "40mi ride #1")
    again = edit(edit(parent, str(tmp_path)), str(tmp_path))
    assert gpx_name(again) == "40mi ride #1 edit"


def test_a_file_without_a_name_falls_back_to_its_own_without_the_id(tmp_path):
    path = os.path.join(tmp_path, "upload_capitol_monona_" + "3f" * 16 + ".gpx")
    with open(path, "w", encoding="utf-8") as f:
        f.write('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
                + "".join(f'<trkpt lat="{la}" lon="{lo}"/>' for la, lo, _ in CAPITOL_TO_MONONA)
                + "</trkseg></trk></gpx>")
    assert gpx_name(edit(path, str(tmp_path))) == "upload_capitol_monona edit"
