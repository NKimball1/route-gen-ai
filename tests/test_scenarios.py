"""Scenario tests: variants of every failure class found in field testing.

Each block name references the devlog phase where the original bug bit.
"""
from routes.editing import (_cum, _dist_m, anchor_at, connect_from,
                            detour_around, extend_route, move_endpoint,
                            route_via_chain, shorten_route)
from tests.test_editing import FakeProvider, road
from tests.test_editing_ops import square_loop, total

LAT_STEP = 0.00025


class RefusingProvider(FakeProvider):
    """Refuses any leg that carries no-go zones — simulates 'no way
    around it' so failure accounting can be exercised."""
    def route(self, waypoints, avoid=None, protect=None):
        if avoid:
            return None
        return super().route(waypoints, avoid, protect)


# ---- phase 23: failure accounting on avoid edits ----

def test_avoid_all_sections_failed_is_not_no_contact():
    pts = road(300)
    mid = pts[150]
    result = detour_around(pts, (mid[0], mid[1], 300.0), RefusingProvider())
    # distinguishable: contact happened, nothing rerouted
    assert result is not None
    assert result.detours == 0
    assert result.failed_detours >= 1
    assert result.removed_m == 0.0


def test_avoid_zone_at_route_start_shrinks_or_fails_honestly():
    pts = road(300)
    # zone centered ON the start: the start can never leave it
    result = detour_around(pts, (pts[0][0], pts[0][1], 800.0), FakeProvider())
    if result is not None:
        assert result.failed_detours >= 1  # counted, not silent


def test_avoid_two_separate_contact_runs():
    pts = road(400)
    z1 = pts[100]
    result = detour_around(pts, (z1[0], z1[1], 250.0), FakeProvider())
    assert result is not None and result.detours == 1
    # editing the result near a different spot also works (chained edits)
    z2 = result.points[len(result.points) * 3 // 4]
    result2 = detour_around(result.points, (z2[0], z2[1], 250.0),
                            FakeProvider())
    assert result2 is not None and result2.detours == 1


# ---- phases 19/20: endpoint moves and anchoring, more shapes ----

def test_move_end_on_loop_rotates():
    pts = square_loop()
    far = (pts[120][0] + 0.004, pts[120][1] + 0.004)
    result = move_endpoint(pts, far, FakeProvider(), at="end")
    assert result is not None
    assert _dist_m(result.points[-1], far) < 50
    assert result.distance_m > total(pts)  # loop fully ridden + leg


def test_anchor_open_route_with_matching_start_only_adds_return():
    pts = road(300)
    at_start = (pts[0][0], pts[0][1])
    result = anchor_at(pts, at_start, FakeProvider())
    assert result is not None
    # start already there (no lead-in), return leg added
    assert _dist_m(result.points[0], at_start) < 160
    assert _dist_m(result.points[-1], at_start) < 50


def test_connect_return_ends_and_starts_at_addr_even_for_loop():
    pts = square_loop()
    home = (pts[0][0] - 0.02, pts[0][1] - 0.02)
    result = connect_from(pts, home, FakeProvider(), with_return=True)
    assert _dist_m(result.points[0], home) < 50
    assert _dist_m(result.points[-1], home) < 50


# ---- phase 21: via chains, more shapes ----

def test_via_chain_skips_far_waypoint_keeps_near_ones():
    pts = road(400)
    near1 = (pts[100][0], pts[100][1] + 0.002)
    near2 = (pts[160][0], pts[160][1] + 0.002)
    far = (pts[130][0], pts[130][1] + 0.5)  # ~40 km east
    result = route_via_chain(pts, [near1, far, near2], FakeProvider())
    assert result is not None
    assert result.detours == 2  # far one skipped


def test_via_chain_three_waypoints_ordered_by_route_position():
    pts = road(500)
    ws = [(pts[k][0], pts[k][1] + 0.002) for k in (200, 120, 260)]
    result = route_via_chain(pts, ws, FakeProvider())
    assert result is not None
    assert result.detours == 3
    assert result.points[0][:2] == pts[0][:2]
    assert result.points[-1][:2] == pts[-1][:2]


# ---- phase 18: length edits, more shapes ----

def test_extend_works_on_a_loop():
    pts = square_loop()
    base = total(pts)
    result = extend_route(pts, 2500.0, FakeProvider())
    assert result is not None
    assert result.distance_m > base + 800


def test_shorten_tiny_ask_still_returns_something_sane():
    pts = road(600)
    result = shorten_route(pts, 600.0, FakeProvider())
    # either a modest cut or an honest refusal — never a gutting
    if result is not None:
        assert result.distance_m > 0.6 * total(pts)


# ---- phase 22: undo lineage ----

def test_predecessor_lineage(tmp_path):
    from edit_route import predecessor
    base = tmp_path / "ride.gpx"
    e1 = tmp_path / "ride_edit1.gpx"
    e3 = tmp_path / "ride_edit3.gpx"
    for f in (base, e1, e3):
        f.write_text("<gpx/>")
    assert predecessor(str(e1)) == str(base)
    assert predecessor(str(base)) is None
    # gap in numbering: edit3's predecessor (edit2) doesn't exist
    assert predecessor(str(e3)) is None


# ---- phase 21: geocode query variants (pure logic) ----

def test_geocode_query_variants():
    from routes.geocode import _query_variants
    qs = _query_variants("greentree neighborhood, madison, wi")
    assert "greentree neighborhood, madison, wi" in qs
    assert "greentree" in qs               # suffix stripped at the end
    assert qs.index("greentree neighborhood, madison, wi") < qs.index("greentree")


# ---- phase 24: riding vs crossing a road ----

def test_crossing_a_road_does_not_trigger_road_avoidance():
    from routes.road_avoid import detour_around_road
    # road runs east-west; route crosses it perpendicular — one touch
    way = [(43.01, -89.52 + k * LAT_STEP) for k in range(160)]
    route = [(43.0 + k * LAT_STEP, -89.5, 300.0) for k in range(160)]
    assert detour_around_road(route, [way], FakeProvider()) is None


def test_riding_two_separate_stretches_gets_two_gaps():
    from routes.road_avoid import detour_around_road, on_road_meters
    way = [(43.0 + k * LAT_STEP, -89.5) for k in range(400)]
    seg = lambda a, b: [(way[k][0], way[k][1], 300.0) for k in range(a, b)]
    off = lambda k, n: [(way[k][0] + j * LAT_STEP, way[k][1] + 0.004, 300.0)
                        for j in range(n)]
    route = seg(0, 60) + off(60, 60) + seg(160, 220) + off(220, 60)
    result = detour_around_road(route, [way], FakeProvider())
    assert result is not None
    assert result.detours >= 2
    assert on_road_meters(result.points, [way]) < on_road_meters(route, [way])
