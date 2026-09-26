"""Interval-spot result hygiene: no duplicate stretches, and 'unknown' is
never printed as 0 (routes/intervals.py, find_spot.py)."""
from routes.intervals import IntervalSpec, IntervalSpot, _dedupe, find_spots
from tests.test_editing import LAT_STEP, FakeProvider


class DenseProvider(FakeProvider):
    """Straight flat legs with enough points for the resampler to see a
    road (FakeProvider's three-point legs are too sparse to search)."""
    def route(self, waypoints, avoid=None, protect=None):
        a, b = waypoints[0], waypoints[-1]
        n = 200
        pts = [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n, 300.0)
               for k in range(n + 1)]
        from routes.editing import _cum
        return {"points": pts, "distance_m": _cum(pts)[-1], "ascent_m": 0.0,
                "major_m": 0.0}


def stretch(lat0: float, lon0: float, n: int = 40) -> IntervalSpot:
    pts = [(lat0 + k * LAT_STEP, lon0, 300.0) for k in range(n)]
    return IntervalSpot(points=pts, length_m=n * 28.0)


def test_same_road_from_adjacent_spokes_collapses_to_one_result():
    a = stretch(43.00, -89.50)
    a.score = 0.9
    twin = stretch(43.0005, -89.5001)   # ~55 m over: the same stretch
    twin.score = 0.85
    other = stretch(43.05, -89.50)      # 5.5 km north: a different road
    other.score = 0.8
    kept = _dedupe([a, twin, other])
    assert kept == [a, other]


def test_dedupe_keeps_the_better_scored_copy():
    worse = stretch(43.00, -89.50)
    worse.score = 0.5
    better = stretch(43.0002, -89.50)
    better.score = 0.7
    assert _dedupe([better, worse]) == [better]


def test_overpass_down_marks_counts_unknown_not_zero(monkeypatch):
    import routes.interruptions as interruptions
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: None)
    spec = IntervalSpec("x", 2, 20.0, "flat", 20.0)
    spots = find_spots(spec, 43.0, -89.5, DenseProvider(), n_spokes=4, top=3)
    assert spots, "the fake provider's straight flat spokes should yield spots"
    assert all(s.controls_known is False for s in spots)
    assert all(s.n_controls == 0 for s in spots)   # 0 hits, flagged unknown


def test_overpass_up_but_empty_is_a_real_zero(monkeypatch):
    import routes.interruptions as interruptions
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: [])
    spec = IntervalSpec("x", 2, 20.0, "flat", 20.0)
    spots = find_spots(spec, 43.0, -89.5, DenseProvider(), n_spokes=4, top=3)
    assert spots and all(s.controls_known for s in spots)


class WindingProvider(DenseProvider):
    """A road three times longer than the crow flies: out along the spoke,
    back along a parallel lane 100 m over, and out again on a third. Ride-out
    distance runs far past the travel budget while every point stays
    inside the spoke's crow-flies reach."""
    def route(self, waypoints, avoid=None, protect=None):
        a, b = waypoints[0], waypoints[-1]
        from routes.editing import _cum
        n, lane = 200, 0.0012   # ~100 m of longitude at 43 N

        def line(p, q, off):
            return [(p[0] + (q[0] - p[0]) * k / n,
                     p[1] + (q[1] - p[1]) * k / n + off, 300.0)
                    for k in range(n + 1)]
        pts = line(a, b, 0.0) + line(b, a, lane)[1:] + line(a, b, 2 * lane)[1:]
        return {"points": pts, "distance_m": _cum(pts)[-1], "ascent_m": 0.0,
                "major_m": 0.0}


def test_within_the_travel_budget_means_riding_distance(monkeypatch):
    """Stop signs along the first 8 km of every spoke make the far end the
    best-scoring window -- and on a winding road the far end is beyond the
    riding budget even though the spoke's endpoint is not."""
    import routes.interruptions as interruptions
    from routes.editing import _cum
    from routes.providers import _destination

    spec = IntervalSpec("x", 2, 8.0, "flat", 20.0)   # 20 min ~ 8 km budget
    provider = WindingProvider()
    controls = []
    for bearing in (0.0, 90.0, 180.0, 270.0):
        dest = _destination(43.0, -89.5, bearing, spec.travel_radius_m / 1.2)
        pts = provider.route([(43.0, -89.5), dest])["points"]
        cum = _cum(pts)
        controls += [(p[0], p[1], 1.5) for p, c in zip(pts, cum)
                     if c <= spec.travel_radius_m + 1000]
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: controls)
    spots = find_spots(spec, 43.0, -89.5, provider, n_spokes=4, top=3)
    assert spots
    assert all(s.dist_from_start_m <= spec.travel_radius_m for s in spots),         [round(s.dist_from_start_m) for s in spots]
