"""find_spot output: named roads, and a hard stop limit (offline)."""
from find_spot import where_lines
from routes.intervals import IntervalSpec, Spot, find_spots
from routes.places import describe_stretch
from routes.providers import _destination
from tests.test_spoke import on_road
from tests.test_spot_results import DenseProvider

O = (43.0, -89.5)
LINE = [(43.0 + k * 0.001, -89.5) for k in range(11)]   # ~1.1 km north


def lookup_by_lat(table):
    """Fake reverse geocoder: road chosen by latitude band."""
    def look(lat, lon):
        for lo, hi, road in table:
            if lo <= lat <= hi:
                return road
        return None
    return look


def test_one_road_is_named_once_with_its_town():
    look = lookup_by_lat([(42.9, 43.1, ("Badger State Trail", "Fitchburg"))])
    assert describe_stretch(LINE, look) == "Badger State Trail, Fitchburg"


def test_a_stretch_that_changes_roads_names_both_ends():
    look = lookup_by_lat([(42.9, 43.0029, ("Femrite Drive", "Madison")),
                          (43.003, 43.0079, ("Hope Road", "Madison")),
                          (43.008, 43.1, ("Nora Road", "Madison"))])
    assert describe_stretch(LINE, look) == "Hope Road, Madison (Femrite Drive -> Nora Road)"


def test_failed_lookups_degrade_to_a_question_mark_not_a_crash():
    assert describe_stretch(LINE, lambda lat, lon: None) == "?"
    assert describe_stretch([], lambda lat, lon: ("X", "")) == "?"


def test_where_lines_number_the_spots_and_give_a_start_point():
    spot = Spot(on_road([(p[0], p[1], 300.0) for p in LINE]))
    look = lookup_by_lat([(42.9, 43.1, ("Oncken Road", "Westport"))])
    line = where_lines([spot], look)[0]
    assert line.startswith("#1: Oncken Road, Westport") and "starts at 43.00000,-89.50000" in line


class EastWest(DenseProvider):
    def route(self, waypoints, avoid=None, protect=None):
        a, b = waypoints[0], waypoints[-1]
        return None if abs(b[1] - a[1]) < 0.01 else super().route(waypoints, avoid, protect)


def test_max_stops_zero_drops_a_stretch_with_a_stop_sign(monkeypatch):
    """The scorer only PREFERS fewer stops; with max_stops=0 a stretch that
    has one must not come back at all."""
    import math

    import routes.interruptions as interruptions
    deg_per_km = 1.0 / (111.32 * math.cos(math.radians(O[0])))
    # a stop sign every 1.5 km along the eastern road: no stretch there avoids one
    stops_east = [(O[0], O[1] + (0.5 + 1.5 * k) * deg_per_km, 1.0) for k in range(7)]
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: stops_east)
    loose = find_spots(IntervalSpec("x", 2, 10.0, "flat", 30.0), O[0], O[1], EastWest(),
                       n_spokes=4, top=3)
    strict = find_spots(IntervalSpec("x", 2, 10.0, "flat", 30.0, max_stops=0), O[0], O[1],
                        EastWest(), n_spokes=4, top=3)
    assert any(s.n_controls > 0 for s in loose)          # without the limit it shows up
    assert strict and all(s.n_controls == 0 for s in strict)


class TrailEastRoadWest(EastWest):
    """The eastern spoke rides a trail all the way; the western one a road."""
    def route(self, waypoints, avoid=None, protect=None):
        leg = super().route(waypoints, avoid, protect)
        if leg is not None and waypoints[-1][1] > waypoints[0][1]:
            leg["path"] = [[(p[0], p[1]) for p in leg["points"]]]
        return leg


def test_no_stops_excludes_a_trail_with_unsigned_road_crossings(monkeypatch):
    """The Badger State Trail complaint: a trail crossing a road every
    1.5 km came back as uninterrupted. The same OSM tag marks crosswalks
    on a road, which a rider ON the road does not stop for."""
    import routes.interruptions as interruptions
    from routes.interruptions import trail_crossing
    plan = IntervalSpec("x", 2, 10.0, "flat", 30.0)
    # one crossing every ~1.5 km ON each spoke's line (a crossing node is
    # on the line itself; the east-west legs drift a few meters off 43.0)
    crossings = []
    for bearing in (90.0, 270.0):
        end = _destination(O[0], O[1], bearing, plan.travel_radius_m / 1.2)
        crossings += [trail_crossing(O[0] + (end[0] - O[0]) * f, O[1] + (end[1] - O[1]) * f)
                      for f in (0.05 + 0.15 * k for k in range(7))]
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: crossings)
    loose = find_spots(plan, O[0], O[1], TrailEastRoadWest(), n_spokes=4, top=3)
    on_trail = [s for s in loose if s.points[-1][1] > O[1]]
    on_road = [s for s in loose if s.points[-1][1] < O[1]]
    assert on_trail and all(s.n_controls > 0 for s in on_trail)
    assert on_road and all(s.n_controls == 0 for s in on_road)
    strict = find_spots(IntervalSpec("x", 2, 10.0, "flat", 30.0, max_stops=0), O[0], O[1],
                        TrailEastRoadWest(), n_spokes=4, top=3)
    assert strict and all(s.points[-1][1] < O[1] for s in strict)
