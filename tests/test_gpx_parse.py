"""GPX point parsing must tolerate foreign exporters (routes/preview.py).

XML promises nothing about attribute order; our writer emits lat-first
but an uploaded file may not.
"""
from routes.preview import parse_gpx_text

OURS = '<trkpt lat="43.05" lon="-89.45"><ele>280.1</ele></trkpt>'
LON_FIRST = '<trkpt lon="-89.45" lat="43.05"><ele>280.1</ele></trkpt>'
SELF_CLOSING = '<rtept lat="43.05" lon="-89.45"/>'
EXTRA_ATTRS = '<trkpt lon="-89.45" foo="1" lat="43.05"><ele>280.1</ele></trkpt>'


def test_lat_first_and_lon_first_parse_identically():
    assert parse_gpx_text(OURS) == parse_gpx_text(LON_FIRST) \
        == [(43.05, -89.45, 280.1)]


def test_self_closing_rtept_without_ele():
    assert parse_gpx_text(SELF_CLOSING) == [(43.05, -89.45, None)]


def test_unknown_attributes_are_ignored():
    assert parse_gpx_text(EXTRA_ATTRS) == [(43.05, -89.45, 280.1)]


def test_point_missing_a_coordinate_is_skipped_not_crashed():
    assert parse_gpx_text('<trkpt lat="43.05"/>') == []
