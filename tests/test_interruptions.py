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


def test_corner_nodes_cluster_to_one_intersection():
    from routes.interruptions import _cluster
    # four signal heads on the corners of one intersection (~20 m apart)
    corners = [(43.0, -89.5, 1.5), (43.0002, -89.5, 1.5),
               (43.0, -89.50025, 1.5), (43.0002, -89.50025, 1.5)]
    merged = _cluster(corners)
    assert len(merged) == 1
    assert merged[0][2] == 1.5


def test_provided_cum_is_used():
    # 4-tuple points carry road-distance cum; hit positions must be in that
    # basis, not a fresh chord sum starting at zero.
    pts = [(43.0 + k * LAT_STEP, -89.5, 300.0, 5000.0 + k * 122.0)
           for k in range(20)]
    hits = controls_along(pts, [(43.0 + 5.5 * LAT_STEP, -89.5, 1.0)])
    assert len(hits) == 1
    assert 5550 < hits[0][0] < 5800


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


# ---- whose stop is it? ----
# On the Madison-area map, stop signs on SIDE streets within 40 m of a road
# outnumbered the road's own 5,037 to 2,082 (median 13 m off its line). A
# rider on the through road does not stop for them.
M_PER_DEG_LON = 111320 * 0.7314  # at 43 N


def test_a_side_street_stop_sign_is_not_the_riders():
    from routes.policy import CONTROL_ON_ROUTE_M, SIGNAL_REACH_M
    pts = road(20)
    at = 43.0 + 5 * LAT_STEP
    side_stop = (at, -89.5 + 13 / M_PER_DEG_LON, 1.0, CONTROL_ON_ROUTE_M)   # on the side street
    own_stop = (43.0 + 12 * LAT_STEP, -89.5, 1.0, CONTROL_ON_ROUTE_M)       # on our road
    corner_signal = (at, -89.5 + 13 / M_PER_DEG_LON, 1.5, SIGNAL_REACH_M)   # signals stop everyone
    hits = controls_along(pts, [side_stop, own_stop, corner_signal])
    assert [w for _, w in hits] == [1.5, 1.0]


def test_an_all_way_stop_counts_once():
    """Both approaches of our road carry a sign (one per direction)."""
    from routes.policy import CONTROL_ON_ROUTE_M
    pts = road(20)
    at = 43.0 + 5 * LAT_STEP
    near_side = (at - 10 / 110540, -89.5, 1.0, CONTROL_ON_ROUTE_M)
    far_side = (at + 10 / 110540, -89.5, 1.0, CONTROL_ON_ROUTE_M)
    assert len(controls_along(pts, [near_side, far_side])) == 1


def test_fetched_stops_are_not_clustered_into_side_street_positions(monkeypatch, tmp_path):
    """An all-way stop's nodes sit ~15 m apart; clustering them kept the
    FIRST one's position, which could be the side street's, 15 m off our
    line -- and the road's own stop would then be missed."""
    from routes import interruptions as ix
    monkeypatch.setattr(ix, "CACHE_DIR", str(tmp_path))
    side = {"lat": 43.0, "lon": -89.5 + 15 / M_PER_DEG_LON, "tags": {"highway": "stop"}}
    own = {"lat": 43.0, "lon": -89.5, "tags": {"highway": "stop"}}
    monkeypatch.setattr(ix, "query_overpass", lambda q: {"elements": [side, own]})
    got = ix.fetch_controls(43.0, -89.5, 1000)
    assert got is not None and len(got) == 2
    pts = [(43.0 - 0.002, -89.5), (43.0 + 0.002, -89.5)]
    assert len(controls_along(pts, got)) == 1
