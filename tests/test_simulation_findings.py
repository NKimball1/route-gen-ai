"""Regressions for bugs found by the phase-34 end-to-end simulation."""
from edit_route import last_edit_changed, normalize_places, note_outcome
from routes.editing import _cum, anchor_at
from routes.preview import parse_gpx_text
from tests.test_editing import LAT_STEP, FakeProvider

# ---- untrusted uploads: the GPX point parser ----


def test_malformed_numbers_are_skipped_not_a_500():
    gpx = ('<trkpt lat="." lon="-"/><trkpt lat="nan" lon="inf"/>'
           '<trkpt lat="" lon="-89.4"/><trkpt lat="43.0" lon="-89.4"/>')
    assert parse_gpx_text(gpx) == [(43.0, -89.4, None)]


def test_impossible_coordinates_are_skipped():
    gpx = ('<trkpt lat="943.07" lon="-889.38"/><trkpt lat="-500" lon="7000"/>'
           '<trkpt lat="90.1" lon="0"/><trkpt lat="0" lon="180.5"/>')
    assert parse_gpx_text(gpx) == []


def test_ele_is_found_anywhere_inside_the_point():
    gpx = ('<trkpt lat="43.0" lon="-89.4"><time>2026-01-01T00:00:00Z</time>'
           '<ele>281.5</ele></trkpt>')
    assert parse_gpx_text(gpx) == [(43.0, -89.4, 281.5)]


def test_ele_never_leaks_in_from_the_next_point():
    gpx = ('<trkpt lat="43.0" lon="-89.4"></trkpt>'
           '<trkpt lat="43.1" lon="-89.4"><ele>300</ele></trkpt>')
    assert parse_gpx_text(gpx) == [(43.0, -89.4, None), (43.1, -89.4, 300.0)]


def test_single_quoted_attributes():
    assert parse_gpx_text("<rtept lon='-89.4' lat='43.0'/>") == \
        [(43.0, -89.4, None)]


# ---- a lone place may arrive in either parser field ----

def test_one_element_places_list_is_a_single_place():
    assert normalize_places(None, ["Olbrich Park, Madison"]) == \
        ("Olbrich Park, Madison", None)


def test_real_chains_and_plain_places_pass_through():
    assert normalize_places(None, ["a", "b"]) == (None, ["a", "b"])
    assert normalize_places("x", None) == ("x", None)
    assert normalize_places(None, ["", "  "]) == (None, None)


# ---- corrections must not revert a change the user never complained about ----

def test_failed_edit_means_nothing_to_revert(tmp_path):
    d = str(tmp_path)
    assert last_edit_changed(d) is True      # no record: old behavior
    note_outcome(d, False)                   # the last request failed
    assert last_edit_changed(d) is False
    note_outcome(d, True)
    assert last_edit_changed(d) is True


# ---- anchor: rotating a loop to a new start IS a change ----

def square_loop(n=60):
    """A closed square loop, n points per side, starting at the SW corner."""
    lat0, lon0, s = 43.0, -89.5, LAT_STEP
    pts = [(lat0 + k * s, lon0, 300.0) for k in range(n)]
    pts += [(lat0 + n * s, lon0 + k * s, 300.0) for k in range(n)]
    pts += [(lat0 + (n - k) * s, lon0 + n * s, 300.0) for k in range(n)]
    pts += [(lat0, lon0 + (n - k) * s, 300.0) for k in range(n + 1)]
    return pts


def test_anchor_rotates_a_loop_that_already_passes_the_target():
    pts = square_loop()
    far_corner = pts[120]                    # diagonally opposite the start
    result = anchor_at(pts, far_corner[:2], FakeProvider())
    assert result is not None, "rotation was discarded as 'nothing to change'"
    assert result.points[0][:2] == far_corner[:2]
    assert result.points[-1][:2] == far_corner[:2]
    assert abs(_cum(result.points)[-1] - _cum(pts)[-1]) < 1.0  # same ride


def test_anchor_at_the_existing_start_is_still_a_no_op():
    pts = square_loop()
    assert anchor_at(pts, pts[0][:2], FakeProvider()) is None
