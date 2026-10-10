"""Surface awareness: unpaved stretches are kept out of interval results.

Field cases: the Military Ridge State Trail (crushed limestone) and a
'flat' pick that was a third compacted surface both reached the top of
interval lists; at 250 W on a road bike either is a dealbreaker.
"""
import math

from routes import providers
from routes.intervals import IntervalSpec, find_spots
from routes.providers import BRouterProvider, is_unpaved
from tests.test_spot_results import DenseProvider

O = (43.0, -89.5)
KX = 111320.0 * math.cos(math.radians(O[0]))   # meters per degree of longitude


def test_is_unpaved_reads_brouter_way_tags():
    assert is_unpaved("highway=cycleway surface=compacted")
    assert is_unpaved("highway=path surface=gravel bicycle=yes")
    assert is_unpaved("highway=track")                      # farm track, untagged
    assert not is_unpaved("highway=track surface=asphalt")
    assert not is_unpaved("highway=residential surface=asphalt")
    assert not is_unpaved("highway=cycleway")                # untagged path: assume paved


class Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_router_reports_unpaved_stretches_where_they_are(monkeypatch):
    """Rows cover the stretch ENDING at their coordinate: 1 km asphalt, then
    1 km compacted -- the unpaved polyline must be the eastern kilometer."""
    coords = [[O[1] + (k * 50.0) / KX, O[0], 300.0] for k in range(41)]   # 2 km east
    mid, end = coords[20], coords[40]
    rows = [["Longitude", "Latitude", "Elevation", "Distance", "c", "e", "t", "n", "i", "WayTags"],
            [int(mid[0] * 1e6), int(mid[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=residential surface=asphalt"],
            [int(end[0] * 1e6), int(end[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=cycleway surface=compacted"]]
    payload = {"features": [{"geometry": {"coordinates": coords},
                             "properties": {"track-length": "2000", "messages": rows}}]}
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: Resp(payload))
    leg = BRouterProvider().route([(O[0], coords[0][0]), (O[0], end[0])])
    assert leg is not None
    assert len(leg["unpaved"]) == 1
    piece = leg["unpaved"][0]
    assert min(p[1] for p in piece) >= mid[0] - 1e-6        # starts at the 1 km mark
    assert max(p[1] for p in piece) >= end[0] - 1e-6        # runs to the end


def test_router_reports_where_the_route_rides_a_path(monkeypatch):
    """A road crossing interrupts a rider on a trail, not one on the road;
    the Spoke needs to know which parts of its road are paths."""
    coords = [[O[1] + (k * 50.0) / KX, O[0], 300.0] for k in range(61)]   # 3 km east
    a, b, c = coords[20], coords[40], coords[60]
    rows = [["Longitude", "Latitude", "Elevation", "Distance", "c", "e", "t", "n", "i", "WayTags"],
            [int(a[0] * 1e6), int(a[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=residential surface=asphalt"],
            [int(b[0] * 1e6), int(b[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=cycleway surface=asphalt"],
            [int(c[0] * 1e6), int(c[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=footway footway=crossing"]]
    payload = {"features": [{"geometry": {"coordinates": coords},
                             "properties": {"track-length": "3000", "messages": rows}}]}
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: Resp(payload))
    leg = BRouterProvider().route([(O[0], coords[0][0]), (O[0], c[0])])
    assert leg is not None and leg["path"]
    on_path = [p for piece in leg["path"] for p in piece]
    assert min(p[1] for p in on_path) >= a[0] - 1e-6        # the road's first km is not a path
    assert max(p[1] for p in on_path) >= c[0] - 1e-6        # the cycleway and footway are


class PavedWestGravelEast(DenseProvider):
    """Two identical flat spokes; the eastern one is all crushed limestone."""
    def route(self, waypoints, avoid=None, protect=None):
        a, b = waypoints[0], waypoints[-1]
        if abs(b[1] - a[1]) < 0.01:
            return None                                      # only east and west spokes
        leg = super().route(waypoints, avoid, protect)
        if b[1] > a[1]:
            leg["unpaved"] = [[(p[0], p[1]) for p in leg["points"]]]
        return leg


def test_finder_skips_an_unpaved_stretch(monkeypatch):
    import routes.interruptions as interruptions
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: [])
    spec = IntervalSpec("x", 2, 10.0, "flat", 30.0)
    spots = find_spots(spec, O[0], O[1], PavedWestGravelEast(), n_spokes=4, top=3)
    assert spots, "the paved western spoke should still yield a spot"
    assert all(s.stretch.points[-1][1] < O[1] for s in spots), "an all-gravel stretch was returned"
    assert all(s.stretch.gravel_share == 0.0 for s in spots)
