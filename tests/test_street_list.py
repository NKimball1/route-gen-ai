"""Routing along a named sequence of streets (routes/street_list.py).

Synthetic streets on a small grid; FakeProvider draws straight legs
between waypoints, so a route rides a street exactly when consecutive
waypoints lie along it -- which is what plan_waypoints must guarantee.
"""
from routes.editing import _dist_m
from routes.street_list import (StreetLeg, build_street_route, junction,
                                nearest_on_ways, parse_street_list,
                                plan_waypoints)
from tests.test_editing import FakeProvider

S = 0.0025   # ~280 m of latitude
O = (43.0, -89.4)


def line(a, b, n=20):
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]


# A runs east, B runs north from A's east end, C runs east from B's top
A = [line(O, (O[0], O[1] + 4 * S))]
B = [line((O[0], O[1] + 4 * S), (O[0] + 4 * S, O[1] + 4 * S))]
C = [line((O[0] + 4 * S, O[1] + 4 * S), (O[0] + 4 * S, O[1] + 8 * S))]
FAR = [line((O[0] + 0.2, O[1]), (O[0] + 0.2, O[1] + 4 * S))]   # ~22 km north: never meets
STREETS = {"A St": A, "B Ave": B, "C Rd": C, "Far Rd": FAR}


def fetch(name, near, radius_m):
    return STREETS.get(name, [])


def test_parse_street_list_accepts_the_ways_people_write_it():
    assert parse_street_list("Capital City Trail -> Dempsey Rd -> Davies St") == \
        ["Capital City Trail", "Dempsey Rd", "Davies St"]
    assert parse_street_list("A; B;C") == ["A", "B", "C"]
    assert parse_street_list("Jenifer St, then Rutledge St, then Lakeland Ave") == \
        ["Jenifer St", "Rutledge St", "Lakeland Ave"]
    assert parse_street_list("  ") == []


def test_junction_finds_where_streets_meet():
    pt, gap = junction(A, B)
    assert gap < 1.0
    assert _dist_m(pt, (O[0], O[1] + 4 * S)) < 5


def test_nearest_on_ways_projects_onto_a_segment_not_just_nodes():
    sparse = [[O, (O[0], O[1] + 4 * S)]]          # two nodes, ~800 m apart
    q, d = nearest_on_ways((O[0] + 0.0005, O[1] + 2 * S), sparse)
    assert d < 60 and abs(q[1] - (O[1] + 2 * S)) < 1e-6


def test_every_street_is_ridden_in_order():
    r = build_street_route(["A St", "B Ave", "C Rd"], FakeProvider(), start=O, fetch=fetch)
    assert r.ok, r.problems
    assert [s.ridden for s in r.streets] == [True, True, True]
    assert all(s.ridden_m > 500 for s in r.streets)
    # with no end given, the ride finishes at the far end of the last street
    assert _dist_m(r.points[-1], (O[0] + 4 * S, O[1] + 8 * S)) < 30


def test_streets_that_never_meet_are_reported():
    _, problems = plan_waypoints([StreetLeg("A St", A), StreetLeg("Far Rd", FAR)], O, None)
    assert any("don't meet" in p for p in problems)


def test_a_street_that_cant_be_found_is_reported_not_dropped_silently():
    r = build_street_route(["A St", "Nowhere Ln", "B Ave"], FakeProvider(), start=O, fetch=fetch)
    assert any("Nowhere Ln" in p for p in r.problems)
    assert r.streets[0].ridden and r.streets[2].ridden


class ShortcutProvider(FakeProvider):
    """A router that ignores the middle waypoints and cuts straight across."""
    def route(self, waypoints, avoid=None, protect=None):
        return super().route([waypoints[0], waypoints[-1]], avoid, protect)


def test_a_route_that_skips_a_street_is_caught():
    r = build_street_route(["A St", "B Ave", "C Rd"], ShortcutProvider(), start=O, fetch=fetch)
    assert not r.ok
    assert any("B Ave" in p for p in r.problems)


def test_a_capped_wide_search_is_recovered_at_the_junction():
    """The real failure: a long trail is dozens of OSM ways, a wide search
    hits the geocoder's result cap, and the segment that meets the next
    street isn't in it -- so 'they don't meet' was reported for two roads
    that do. A local re-fetch at the junction must recover it."""
    j = B[0][0]                                              # where B begins
    near_part = [line((O[0], O[1] + 2 * S), j)]              # trail segment touching B
    far_part = [line(O, (O[0], O[1] + 2 * S))]               # ends ~400 m short of B

    def capped(name, near, radius_m):
        if name == "B Ave":
            return B
        if name == "Trail":
            close = _dist_m(near, j) < 500 and radius_m <= 3000
            return near_part if close else far_part          # only a local search sees it
        return []

    r = build_street_route(["Trail", "B Ave"], FakeProvider(), start=O, fetch=capped)
    assert r.ok, r.problems
    assert r.streets[0].ridden and r.streets[1].ridden


def test_loop_back_to_start():
    r = build_street_route(["A St", "B Ave"], FakeProvider(), start=O, end=O, fetch=fetch)
    assert _dist_m(r.points[0], O) < 5 and _dist_m(r.points[-1], O) < 5
