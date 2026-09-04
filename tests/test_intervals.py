from routes.intervals import (IntervalSpec, _resample, _score, _window_stats)

LAT_STEP = 0.0011  # ~122 m of latitude per step


def road(n, lat0=43.0, lon=-89.5, ele_fn=lambda k: 300.0):
    return [(lat0 + k * LAT_STEP, lon, ele_fn(k)) for k in range(n)]


def test_rep_distance_flat_vs_incline():
    flat = IntervalSpec("x", 2, 20, "flat")
    hill = IntervalSpec("x", 4, 5, "incline")
    assert 6 < flat.rep_distance_m / 1609.344 < 7      # 20 min at 20 mph
    assert 0.8 < hill.rep_distance_m / 1609.344 < 1.0  # 5 min at 11 mph


def test_flat_road_stats():
    rs = _resample(road(40))
    length, mean, std, tpk = _window_stats(rs, 0, len(rs) - 1)
    assert length > 4000
    assert abs(mean) < 0.1
    assert std < 0.1
    assert tpk == 0.0


def test_steady_climb_stats():
    rs = _resample(road(40, ele_fn=lambda k: 300.0 + k * 4.9))  # ~4% grade
    _, mean, std, _ = _window_stats(rs, 0, len(rs) - 1)
    assert 3.5 < mean < 4.5
    assert std < 0.5


def test_scoring_prefers_matching_grade():
    flat_spec = IntervalSpec("x", 2, 20, "flat")
    hill_spec = IntervalSpec("x", 4, 5, "incline")
    L = flat_spec.rep_distance_m
    flat_on_flat = _score(flat_spec, L, 0.0, 0.2, 0.5)
    flat_on_hill = _score(flat_spec, L, 4.0, 0.2, 0.5)
    assert flat_on_flat > flat_on_hill
    L = hill_spec.rep_distance_m
    hill_on_hill = _score(hill_spec, L, 4.0, 0.3, 0.5)
    hill_on_flat = _score(hill_spec, L, 0.0, 0.3, 0.5)
    assert hill_on_hill > hill_on_flat


def test_turny_road_penalized():
    spec = IntervalSpec("x", 2, 20, "flat")
    L = spec.rep_distance_m
    assert _score(spec, L, 0.0, 0.2, 0.0) > _score(spec, L, 0.0, 0.2, 3.0)
