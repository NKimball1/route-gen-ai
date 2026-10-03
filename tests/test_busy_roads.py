"""Busy roads count against an interval stretch.

Field case 2026-10-02: the top 4x10 result ran its last stretch onto South
Fish Hatchery Road, a busy county road; a slightly shorter window that
stopped before it was the better spot, but the finder couldn't see road
class, so length won.
"""
import math

from routes import providers
from routes.intervals import IntervalSpec, find_spots
from routes.providers import BRouterProvider, is_busy
from tests.test_spot_results import DenseProvider
from tests.test_surface import KX, Resp

O = (43.0, -89.5)


def test_is_busy_reads_road_class():
    for tags in ("highway=secondary", "highway=primary surface=asphalt",
                 "highway=trunk", "highway=secondary_link"):
        assert is_busy(tags), tags
    for tags in ("highway=tertiary", "highway=residential", "highway=cycleway",
                 "highway=unclassified"):
        assert not is_busy(tags), tags


def test_router_reports_busy_stretches(monkeypatch):
    coords = [[O[1] + (k * 50.0) / KX, O[0], 300.0] for k in range(41)]
    mid, end = coords[20], coords[40]
    rows = [["Longitude", "Latitude", "Elevation", "Distance", "c", "e", "t", "n", "i", "WayTags"],
            [int(mid[0] * 1e6), int(mid[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=residential"],
            [int(end[0] * 1e6), int(end[1] * 1e6), 300, 1000, 0, 0, 0, 0, 0, "highway=secondary"]]
    payload = {"features": [{"geometry": {"coordinates": coords},
                             "properties": {"track-length": "2000", "messages": rows}}]}
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: Resp(payload))
    leg = BRouterProvider().route([(O[0], coords[0][0]), (O[0], end[0])])
    assert leg is not None and len(leg["busy"]) == 1
    assert min(p[1] for p in leg["busy"][0]) >= mid[0] - 1e-6


class QuietThenBusy(DenseProvider):
    """One eastern spoke: quiet for its first 4.5 km, a busy road after."""
    BUSY_FROM_KM = 4.5

    def route(self, waypoints, avoid=None, protect=None):
        a, b = waypoints[0], waypoints[-1]
        if b[1] <= a[1] + 0.01:
            return None
        leg = super().route(waypoints, avoid, protect)
        start_lon = a[1] + self.BUSY_FROM_KM * 1000 / (111320.0 * math.cos(math.radians(a[0])))
        leg["busy"] = [[(p[0], p[1]) for p in leg["points"] if p[1] >= start_lon]]
        return leg


def test_a_window_running_onto_a_busy_road_loses_to_one_that_stops_short(monkeypatch):
    import routes.interruptions as interruptions
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: [])
    spec = IntervalSpec("x", 4, 10.0, "flat", 30.0)          # ~5.4 km reps
    best = find_spots(spec, O[0], O[1], QuietThenBusy(), n_spokes=4, top=1)[0]
    busy_lon = O[1] + QuietThenBusy.BUSY_FROM_KM * 1000 / (111320.0 * math.cos(math.radians(O[0])))
    assert max(p[1] for p in best.points) <= busy_lon + 0.0003, \
        "the best spot runs onto the busy road"
