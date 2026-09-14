"""Undo follows real parentage, not edit numbers (edit_route.py).

Edit numbers only go up (next free number). After an undo or a
revert-first correction, the newest edit was built on an OLDER version
than editN-1 — undo must land there, not on the change the user just
rejected.
"""
import os

from edit_route import predecessor, record_parent


def touch(d, name):
    p = os.path.join(d, name)
    open(p, "w").close()
    return p


def test_correction_undo_lands_on_the_version_it_was_built_on(tmp_path):
    d = str(tmp_path)
    base = touch(d, "route.gpx")
    e1 = touch(d, "route_edit1.gpx")
    e2 = touch(d, "route_edit2.gpx")   # the bad change
    e3 = touch(d, "route_edit3.gpx")   # correction, built on edit1
    record_parent(e1, base)
    record_parent(e2, e1)
    record_parent(e3, e1)
    assert predecessor(e3) == e1       # not e2
    assert predecessor(e2) == e1
    assert predecessor(e1) == base
    assert predecessor(base) is None


def test_fallback_without_lineage_walks_down_over_gaps(tmp_path):
    d = str(tmp_path)
    base = touch(d, "route.gpx")
    e1 = touch(d, "route_edit1.gpx")
    e4 = touch(d, "route_edit4.gpx")   # edit2/edit3 gone
    assert predecessor(e4) == e1
    assert predecessor(e1) == base


def test_parent_in_another_directory_is_kept_as_a_path(tmp_path):
    shared = tmp_path / "shared"
    session = tmp_path / "session"
    shared.mkdir(), session.mkdir()
    base = touch(str(shared), "route.gpx")
    e1 = touch(str(session), "route_edit1.gpx")
    record_parent(e1, base)
    assert predecessor(e1) == base


def test_corrupt_lineage_file_falls_back_quietly(tmp_path):
    d = str(tmp_path)
    base = touch(d, "route.gpx")
    e1 = touch(d, "route_edit1.gpx")
    with open(os.path.join(d, "lineage.json"), "w") as f:
        f.write("{not json")
    assert predecessor(e1) == base
    record_parent(e1, base)            # overwrites the corrupt file
    assert predecessor(e1) == base
