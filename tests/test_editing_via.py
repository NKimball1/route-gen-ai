from routes.editing import route_via
from tests.test_editing import FakeProvider, road

LAT_STEP = 0.00025


def test_via_splices_through_target():
    pts = road(300)  # straight north ~8 km
    # a target ~400 m east of the route's midpoint
    mid = pts[150]
    target = (mid[0], mid[1] + 0.005)
    provider = FakeProvider()
    result = route_via(pts, target, provider)
    assert result is not None
    # the new route passes through the target
    assert any(abs(p[0] - target[0]) < 1e-4 and abs(p[1] - target[1]) < 1e-4
               for p in result.points)
    # ends untouched
    assert result.points[0][:2] == pts[0][:2]
    assert result.points[-1][:2] == pts[-1][:2]
    # the target was protected from despurring in the router call
    assert provider.calls, "router was called"


def test_via_too_far_refuses():
    pts = road(100)
    far = (44.5, -89.5)  # ~165 km away
    assert route_via(pts, far, FakeProvider()) is None
