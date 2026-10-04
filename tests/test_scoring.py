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
    anchored.points = natural.points = [(42.99, -89.5, 0), (43.01, -89.5, 0)]
    keepers, _ = rank(spec, [anchored, natural])
    assert keepers[0].seed == "sweep"


def test_via_place_is_an_area_not_a_point():
    """'Through Verona' is satisfied by a loop that sweeps through town
    without touching the geocoded center; 'via the cafe' still is not."""
    from routes.policy import via_place_tolerance_m
    through_town = cand(50, 900, "through")
    through_town.points = [(42.99, -89.482, 0), (43.01, -89.482, 0)]  # ~1.5 km east
    misses = cand(50, 900, "misses")
    misses.points = [(42.99, -89.44, 0), (43.01, -89.44, 0)]          # ~5 km east
    town = RouteSpec("x", distance_m=50 * 1609.344, via=[(43.0, -89.5)],
                     via_tolerance_m=[via_place_tolerance_m(5000)])   # 5 km-wide town
    keepers, rejects = rank(town, [through_town, misses])
    assert [k.seed for k in keepers] == ["through"]
    assert rejects[0][0].seed == "misses" and "waypoint" in rejects[0][1]

    cafe = RouteSpec("x", distance_m=50 * 1609.344, via=[(43.0, -89.5)],
                     via_tolerance_m=[via_place_tolerance_m(20)])     # a building
    assert not rank(cafe, [through_town])[0]


def test_via_tolerance_comes_from_the_geocoded_extent(monkeypatch):
    from routes import geocode
    from routes.policy import via_place_tolerance_m
    town = {"lat": "42.99", "lon": "-89.53", "display_name": "Verona",
            "boundingbox": ["42.96", "43.02", "-89.58", "-89.49"]}
    cafe = {"lat": "43.07", "lon": "-89.40", "display_name": "Cafe",
            "boundingbox": ["43.0699", "43.0701", "-89.4001", "-89.3999"]}
    for hit in (town, cafe):
        monkeypatch.setattr(geocode, "_nominatim_get", lambda params, hit=hit: [hit])
        geocode.geocode(hit["display_name"])
    assert 2500 < via_place_tolerance_m(geocode.place_extent_m(42.99, -89.53)) <= 3000
    assert via_place_tolerance_m(geocode.place_extent_m(43.07, -89.40)) == 150
    assert via_place_tolerance_m(geocode.place_extent_m(1.0, 2.0)) == 150  # unknown


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
