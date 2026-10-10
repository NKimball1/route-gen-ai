from routes.intervals import IntervalSpec, _score


def test_rep_distance_flat_vs_incline():
    flat = IntervalSpec("x", 2, 20, "flat")
    hill = IntervalSpec("x", 4, 5, "incline")
    assert 6 < flat.rep_distance_m / 1609.344 < 7      # 20 min at 20 mph
    assert 0.8 < hill.rep_distance_m / 1609.344 < 1.0  # 5 min at 11 mph


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
