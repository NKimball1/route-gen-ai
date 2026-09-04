from routes.interruptions import controls_along
from routes.intervals import IntervalSpec, _score

LAT_STEP = 0.0011  # ~122 m of latitude per step


def road(n, lat0=43.0, lon=-89.5):
    return [(lat0 + k * LAT_STEP, lon, 300.0) for k in range(n)]


def test_control_on_the_road_counts():
    pts = road(20)
    on_road = (43.0 + 5.5 * LAT_STEP, -89.5, 1.0)       # between two samples
    far_away = (43.0 + 5.5 * LAT_STEP, -89.49, 1.0)      # ~800 m east
    hits = controls_along(pts, [on_road, far_away])
    assert len(hits) == 1
    # ~5.5 steps of ~122 m along the line
    assert 550 < hits[0][0] < 800


def test_hits_sorted_by_position():
    pts = road(20)
    controls = [(43.0 + k * LAT_STEP, -89.5, 1.0) for k in (12, 3, 7)]
    hits = controls_along(pts, controls)
    assert [round(h[0]) for h in hits] == sorted(round(h[0]) for h in hits)
    assert len(hits) == 3


def test_scoring_penalizes_controls():
    spec = IntervalSpec("x", 2, 20, "flat")
    L = spec.rep_distance_m
    clean = _score(spec, L, 0.0, 0.2, 0.5, control_wt=0.0)
    lit_up = _score(spec, L, 0.0, 0.2, 0.5, control_wt=8.0)
    assert clean > lit_up
    # a great-grade stretch full of lights should lose to a slightly worse
    # grade with none
    quiet_tilted = _score(spec, L, 0.8, 0.4, 0.5, control_wt=0.0)
    assert quiet_tilted > lit_up


def test_clean_short_beats_long_with_stops():
    # The user's actual complaint: a full-length stretch with 8 stops must
    # lose to a clean stretch a third the length (turnarounds interrupt less
    # than traffic lights).
    spec = IntervalSpec("x", 2, 20, "flat")
    L = spec.rep_distance_m
    long_with_stops = _score(spec, L, 0.1, 2.6, 0.2, control_wt=9.0)
    clean_short = _score(spec, 0.36 * L, 0.3, 1.6, 0.3, control_wt=0.0)
    assert clean_short > long_with_stops
