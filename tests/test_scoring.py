from routes.scoring import rank
from routes.spec import RouteCandidate, RouteSpec


def cand(dist_mi, climb_ft, seed="s"):
    return RouteCandidate(provider="test", seed=seed,
                          distance_m=dist_mi * 1609.344,
                          ascent_m=climb_ft * 0.3048)


def test_distance_tolerance():
    spec = RouteSpec("x", distance_m=30 * 1609.344)
    keepers, rejects = rank(spec, [cand(30, 500), cand(40, 500)])
    assert len(keepers) == 1 and len(rejects) == 1


def test_climb_cap():
    spec = RouteSpec("x", distance_m=30 * 1609.344, max_ascent_m=1000 * 0.3048)
    keepers, rejects = rank(spec, [cand(30, 900), cand(30, 1100)])
    assert len(keepers) == 1
    assert "exceeds cap" in rejects[0][1]


def test_via_prefers_natural_loops():
    spec = RouteSpec("x", distance_m=50 * 1609.344, via=[(43.0, -89.5)])
    anchored = cand(49.9, 1000, "anchored")          # closer to target
    natural = cand(57.0, 950, "sweep")               # organic but longer
    natural.natural = True
    keepers, _ = rank(spec, [anchored, natural])
    assert keepers[0].seed == "sweep"


def test_overlap_rejected():
    spec = RouteSpec("x", distance_m=50 * 1609.344)
    lollipop = cand(50, 500, "lolly")
    lollipop.overlap_frac = 0.4
    keepers, rejects = rank(spec, [cand(50, 500, "clean"), lollipop])
    assert len(keepers) == 1 and keepers[0].seed == "clean"
    assert "same road twice" in rejects[0][1]


def test_maximize_orders_by_climb():
    spec = RouteSpec("x", distance_m=50 * 1609.344, maximize_ascent=True)
    keepers, _ = rank(spec, [cand(50, 700, "a"), cand(50, 1400, "b")])
    assert keepers[0].seed == "b"
