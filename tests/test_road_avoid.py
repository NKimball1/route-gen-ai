from routes.editing import _cum
from routes.road_avoid import (detour_around_road, dist_to_road,
                               on_road_meters, road_nogos)
from tests.test_editing import FakeProvider

STEP = 0.00025  # ~28 m


def road_way(n=120, lat0=43.0, lon=-89.5):
    return [(lat0 + k * STEP, lon) for k in range(n)]


def route_riding_road():
    """A route that approaches, rides the road for ~1.4 km, then leaves."""
    way = road_way()
    approach = [(42.995 + k * STEP, -89.51 + k * STEP * 2, 300.0)
                for k in range(20)]
    on_road = [(way[k][0], way[k][1], 300.0) for k in range(10, 60)]
    leave = [(way[60][0] + k * STEP, way[60][1] + k * STEP * 2, 300.0)
             for k in range(20)]
    return approach + on_road + leave, [way]


def test_on_road_metric():
    pts, ways = route_riding_road()
    m = on_road_meters(pts, ways)
    assert 1100 < m < 1950  # ~1.4 km ridden + transition segments


def test_dist_to_road():
    ways = [road_way()]
    assert dist_to_road((43.005, -89.5), ways) < 5
    assert dist_to_road((43.005, -89.51), ways) > 700


def test_road_nogos_keep_endpoints_clear():
    ways = [road_way()]
    center = (43.008, -89.5)
    keep = [(43.003, -89.5)]
    nogos = road_nogos(ways, center, 1500.0, keep_clear=keep)
    assert nogos, "some no-gos placed"
    from routes.editing import _dist_m
    assert all(_dist_m((n[0], n[1]), keep[0]) >= n[2] + 140 for n in nogos)


def test_detour_around_road_reroutes_the_ridden_stretch():
    pts, ways = route_riding_road()
    result = detour_around_road(pts, ways, FakeProvider())
    assert result is not None
    assert result.failed_detours == 0
    # the fake provider bridges straight between anchors — off the road
    assert on_road_meters(result.points, ways) < on_road_meters(pts, ways) / 3
    assert result.points[0][:2] == pts[0][:2]
    assert result.points[-1][:2] == pts[-1][:2]


def test_route_never_on_road_returns_none():
    ways = [road_way()]
    off = [(43.0 + k * STEP, -89.6, 300.0) for k in range(50)]
    assert detour_around_road(off, ways, FakeProvider()) is None
