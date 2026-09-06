from routes.editing import (_cum, connect_from, extend_route, move_endpoint,
                            shorten_route)
from tests.test_editing import FakeProvider, road


def total(points):
    return _cum(points)[-1]


def test_extend_adds_roughly_requested():
    pts = road(300)  # ~8.3 km straight line
    base = total(pts)
    result = extend_route(pts, 3000.0, FakeProvider())
    assert result is not None
    gained = result.distance_m - base
    assert 1500 < gained < 6000  # ballpark of the +3 km ask
    assert result.points[0][:2] == pts[0][:2]
    assert result.points[-1][:2] == pts[-1][:2]


def test_shorten_cuts_roughly_requested():
    # a route with a big square wiggle in the middle that a bridge can cut
    import tests.test_editing as te
    step = te.LAT_STEP
    a = road(100)
    wiggle = [(a[-1][0], a[-1][1] + k * step, 300.0) for k in range(1, 80)]
    wiggle += [(a[-1][0] + k * step, wiggle[-1][1], 300.0) for k in range(1, 40)]
    wiggle += [(wiggle[-1][0], wiggle[-1][1] - k * step, 300.0) for k in range(1, 80)]
    back = [(a[-1][0] + 40 * step + k * step, a[-1][1], 300.0) for k in range(100)]
    pts = a + wiggle + back
    base = total(pts)
    # ask for more than the route can give: best partial cut, not refusal
    result = shorten_route(pts, 3000.0, FakeProvider())
    assert result is not None
    assert result.distance_m < base - 1200
    assert result.points[0][:2] == pts[0][:2]
    assert result.points[-1][:2] == pts[-1][:2]


def test_shorten_refuses_to_gut_the_ride():
    pts = road(100)  # ~2.7 km
    assert shorten_route(pts, 2500.0, FakeProvider()) is None


def test_move_endpoint_end():
    pts = road(200)
    new_end = (pts[-1][0] + 0.01, pts[-1][1] + 0.01)
    result = move_endpoint(pts, new_end, FakeProvider(), at="end")
    assert result is not None
    assert abs(result.points[-1][0] - new_end[0]) < 1e-6
    assert result.points[0][:2] == pts[0][:2]


def test_move_endpoint_start():
    pts = road(200)
    new_start = (pts[0][0] - 0.01, pts[0][1] - 0.01)
    result = move_endpoint(pts, new_start, FakeProvider(), at="start")
    assert result is not None
    assert abs(result.points[0][0] - new_start[0]) < 1e-6
    assert result.points[-1][:2] == pts[-1][:2]


def square_loop(n_side=60):
    import tests.test_editing as te
    step = te.LAT_STEP
    pts = []
    for k in range(n_side):
        pts.append((43.0 + k * step, -89.5, 300.0))
    for k in range(n_side):
        pts.append((43.0 + n_side * step, -89.5 + k * step, 300.0))
    for k in range(n_side):
        pts.append((43.0 + (n_side - k) * step, -89.5 + n_side * step, 300.0))
    for k in range(n_side + 1):
        pts.append((43.0, -89.5 + (n_side - k) * step, 300.0))
    return pts


def test_move_start_on_loop_rotates_to_nearest_point():
    # the field bug: new start near the loop's FAR corner used to route to
    # a fixed anchor near the old start, riding the loop's own corridor
    # backward. Now the loop rotates so the ride joins at closest approach.
    pts = square_loop()
    far_corner = pts[120]  # opposite the old start
    new_start = (far_corner[0] + 0.004, far_corner[1] + 0.004)
    result = move_endpoint(pts, new_start, FakeProvider(), at="start")
    assert result is not None
    assert abs(result.points[0][0] - new_start[0]) < 1e-6
    # the lead-in joins right at the nearest corner — no long doubled ride
    from routes.editing import _dist_m
    join = result.points[3]  # first route point after the fake 3-pt leg
    assert _dist_m(join, far_corner) < 300
    assert result.overlap_frac < 0.05
    # the whole loop is still ridden
    assert result.distance_m > total(pts)


def test_move_start_open_route_joins_at_closest_approach():
    pts = road(300)
    near_mid = (pts[100][0], pts[100][1] + 0.003)
    result = move_endpoint(pts, near_mid, FakeProvider(), at="start")
    assert result is not None
    assert abs(result.points[0][0] - near_mid[0]) < 1e-6
    # everything before the join is dropped, not re-ridden
    assert result.removed_m > 2000
    assert result.points[-1][:2] == pts[-1][:2]


def test_anchor_loop_round_trip():
    from routes.editing import anchor_at, _dist_m
    pts = square_loop()
    far = (pts[120][0] + 0.006, pts[120][1] + 0.006)
    result = anchor_at(pts, far, FakeProvider())
    assert result is not None
    assert _dist_m(result.points[0], far) < 50
    assert _dist_m(result.points[-1], far) < 50
    assert result.distance_m > total(pts)  # whole loop + both legs


def test_anchor_open_route_connects_both_ends():
    from routes.editing import anchor_at, _dist_m
    pts = road(300)
    home = (pts[150][0], pts[150][1] + 0.02)
    result = anchor_at(pts, home, FakeProvider())
    assert result is not None
    assert _dist_m(result.points[0], home) < 50
    assert _dist_m(result.points[-1], home) < 50


def test_anchor_already_anchored_is_noop():
    from routes.editing import anchor_at
    pts = square_loop()
    at_start = (pts[0][0], pts[0][1])
    assert anchor_at(pts, at_start, FakeProvider()) is None


def test_connect_one_way_and_round_trip():
    pts = road(100)
    home = (pts[0][0] - 0.02, pts[0][1])
    one_way = connect_from(pts, home, FakeProvider(), with_return=False)
    assert abs(one_way.points[0][0] - home[0]) < 1e-6
    assert one_way.points[-1][:2] == pts[-1][:2]
    round_trip = connect_from(pts, home, FakeProvider(), with_return=True)
    assert abs(round_trip.points[0][0] - home[0]) < 1e-6
    assert abs(round_trip.points[-1][0] - home[0]) < 1e-6
    assert round_trip.added_m > one_way.added_m
