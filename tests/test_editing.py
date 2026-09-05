from routes.editing import detour_around

LAT_STEP = 0.00025  # ~28 m per point


class FakeProvider:
    """Returns a straight two-point leg between the requested endpoints and
    records the no-go zones it was asked to honor."""
    def __init__(self):
        self.calls = []

    def route(self, waypoints, avoid=None, protect=None):
        self.calls.append(avoid)
        a, b = waypoints[0], waypoints[-1]
        pts = [(a[0], a[1], 300.0), ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 300.0),
               (b[0], b[1], 300.0)]
        return {"points": pts, "distance_m": 1000.0, "ascent_m": 0.0,
                "major_m": 0.0}


def road(n):
    return [(43.0 + k * LAT_STEP, -89.5, 300.0) for k in range(n)]


def test_no_contact_returns_none():
    pts = road(100)
    assert detour_around(pts, (44.0, -89.5, 500.0), FakeProvider()) is None


def test_detour_replaces_zone_section():
    pts = road(200)  # ~5.5 km straight north
    mid = pts[100]
    provider = FakeProvider()
    result = detour_around(pts, (mid[0], mid[1], 300.0), provider)
    assert result is not None
    assert result.detours == 1
    assert result.removed_m > 1000  # zone diameter + buffers
    # the no-go passed to the router covers the zone with margin
    assert provider.calls[0][0][2] > 300.0
    # start and end of the route are untouched
    assert result.points[0][:2] == pts[0][:2]
    assert result.points[-1][:2] == pts[-1][:2]


def test_two_crossings_two_detours():
    pts = road(300)
    z1, z2 = pts[60], pts[220]
    provider = FakeProvider()
    result = detour_around(pts, (z1[0], z1[1], 200.0), provider)
    assert result.detours == 1
    # separate zones handled in separate calls when edited sequentially
    result2 = detour_around(result.points, (z2[0], z2[1], 200.0), provider)
    assert result2.detours == 1
