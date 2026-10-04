"""Constraint and geometry regressions independent of live providers."""
import pytest

from routes.despur import _leg_len
from routes.editing import route_via_chain
from routes.geometry import waypoints_in_order
from routes.gpx_in import parse_gpx_text
from routes.scoring import rank
from routes.spec import RouteSpec, RouteCandidate, AvoidRoad
from tests.test_editing import FakeProvider, road


class FixedRoad:
    def __init__(self, points):
        self.points = points

    def route(self, *args, **kwargs):
        return {"points": self.points, "distance_m": _leg_len(self.points),
                "ascent_m": 0, "major_m": 0}


def test_via_edit_preserves_requested_order():
    points = road(400)
    targets = [points[170][:2], points[130][:2]]
    result = route_via_chain(points, targets, FakeProvider())
    assert result is not None
    assert waypoints_in_order(result.points, targets, 10)


def test_shape_and_precise_waypoints_are_hard_constraints():
    spec = RouteSpec("public", 1000, shape="outback")
    candidate = RouteCandidate("test", "s", 1000, 0, shape="loop")
    assert not rank(spec, [candidate])[0]
    spec = RouteSpec("public", 1000, via=[(43, -89)])
    candidate.points = [(43.01, -89, 0), (43.02, -89, 0)]
    assert not rank(spec, [candidate])[0]  # 1 km away is not a cafe stop


def test_generation_rejects_an_excluded_road_outside_the_geocoded_circle():
    points = road(100)
    spec = RouteSpec("public", 1000,
                    avoid_roads=[AvoidRoad("Main Road", [[p[:2] for p in points]])])
    candidate = RouteCandidate("test", "s", 1000, 0, points=points)
    assert not rank(spec, [candidate])[0]


def test_unknown_provider_road_exposure_is_disclosed():
    spec = RouteSpec("public", 1000)
    candidate = RouteCandidate("ors", "s", 1000, 0, major_m=None)
    kept, _ = rank(spec, [candidate])
    assert kept and "unknown" in kept[0].warnings[0]


def test_gpx_namespaces_and_touching_segments():
    xml = ('<g:gpx xmlns:g="http://www.topografix.com/GPX/1/1"><g:trk>'
           '<g:trkseg><g:trkpt lat="43" lon="-89"/><g:trkpt lat="43.01" lon="-89"/></g:trkseg>'
           '<g:trkseg><g:trkpt lat="43.01" lon="-89"/><g:trkpt lat="43.02" lon="-89"/></g:trkseg>'
           '</g:trk></g:gpx>')
    assert len(parse_gpx_text(xml)) == 3


@pytest.mark.parametrize("gap_deg", [0.0005, 0.0145])  # ~55 m pause, ~1.6 km dropout
def test_gpx_joins_one_recording_split_at_pauses_and_dropouts(gap_deg):
    """Garmin/Wahoo files start a new segment at every auto-pause or signal
    loss; those uploads are one ride and must not be refused."""
    a, b = 43.01, 43.01 + gap_deg
    xml = ('<gpx><trk>'
           f'<trkseg><trkpt lat="43" lon="-89"/><trkpt lat="{a}" lon="-89"/></trkseg>'
           f'<trkseg><trkpt lat="{b}" lon="-89"/><trkpt lat="{b + .01}" lon="-89"/></trkseg>'
           '</trk></gpx>')
    assert [p[0] for p in parse_gpx_text(xml, strict=True)] == [43, a, b, b + .01]


def test_gpx_with_track_and_duplicate_route_reads_the_track_once():
    xml = ('<gpx><rte><rtept lat="43" lon="-89"/><rtept lat="43.02" lon="-89"/></rte>'
           '<trk><trkseg><trkpt lat="43" lon="-89"/><trkpt lat="43.01" lon="-89"/>'
           '<trkpt lat="43.02" lon="-89"/></trkseg></trk></gpx>')
    assert len(parse_gpx_text(xml, strict=True)) == 3


@pytest.mark.parametrize("xml", [
    '<gpx><trk><trkseg><trkpt lat="43" lon="-89"/></trkseg><trkseg><trkpt lat="44" lon="-89"/></trkseg></trk></gpx>',
    '<gpx><trk><trkseg><trkpt lat="43" lon="-89"/></trkseg></trk><trk><trkseg><trkpt lat="43.05" lon="-89"/></trkseg></trk></gpx>',
    '<!DOCTYPE gpx [<!ENTITY x "unsafe">]><gpx/>',
])
def test_gpx_refuses_disconnected_segments_and_entities(xml):
    with pytest.raises(ValueError):
        parse_gpx_text(xml)


@pytest.mark.parametrize("distance", [0, -1, float("nan"), float("inf")])
def test_direct_route_specs_reject_invalid_distance(distance):
    with pytest.raises(ValueError, match="distance"):
        RouteSpec("public", distance)


def test_road_routing_buffers_do_not_replace_actual_area_constraints(monkeypatch):
    from routes.constraints import resolve_avoid
    monkeypatch.setattr("routes.constraints.geocode_flexible", lambda *a, **kw: (43, -89, "public"))
    monkeypatch.setattr("routes.constraints.looks_like_road", lambda name: name == "Main Road")
    monkeypatch.setattr("routes.constraints.fetch_road", lambda *a: [[(43, -89), (43.01, -89)]])
    monkeypatch.setattr("routes.constraints.road_nogos", lambda *a, **kw: [(43, -89, 100)])
    resolved = resolve_avoid(["Main Road", "Park:500"])
    assert resolved.areas == [(43, -89, 500)]
    assert resolved.routing_zones == [(43, -89, 100), (43, -89, 500)]
    assert len(resolved.roads) == 1
    spec = RouteSpec("public", 1000, avoid=resolved.routing_zones,
                     avoid_roads=resolved.roads, avoid_areas=resolved.areas)
    monkeypatch.setattr("routes.scoring.on_road_meters", lambda *a: 0)
    c = RouteCandidate("test", "s", 1000, 0, points=[(43, -89.01, 0), (43, -88.99, 0)])
    assert rank(spec, [c])[1][0][1] == "still enters an excluded area"


def test_shared_climb_search_does_not_consume_personal_strava_tokens(monkeypatch):
    from routes.climbs import find_climbs
    monkeypatch.setattr("routes.strava.available", lambda: pytest.fail("accessed personal Strava account"))
    monkeypatch.setattr("routes.peaks.fetch_peaks", lambda *a: [])
    assert find_climbs(43, -89, 1000, FixedRoad([]), n_spokes=0) == []


def test_missing_road_geometry_refuses_instead_of_using_a_circle(monkeypatch):
    from routes.constraints import resolve_avoid, ConstraintUnavailable
    monkeypatch.setattr("routes.constraints.geocode_flexible", lambda *a, **kw: (43, -89, "Monroe Street"))
    monkeypatch.setattr("routes.constraints.fetch_road", lambda *a: [])
    with pytest.raises(ConstraintUnavailable, match="road geometry is unavailable"):
        resolve_avoid(["Monroe Street, Madison WI"])
