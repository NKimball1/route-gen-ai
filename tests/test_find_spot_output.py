"""find_spot output: named roads, and a hard stop limit (offline)."""
from find_spot import where_lines
from routes.intervals import IntervalSpec, IntervalSpot, find_spots
from routes.places import describe_stretch
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
    spot = IntervalSpot(points=[(p[0], p[1], 300.0) for p in LINE], length_m=1100.0)
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
