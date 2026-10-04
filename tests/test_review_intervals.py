"""Interval-finder regressions independent of live providers."""
import math

import pytest

from routes.despur import _leg_len
from routes.intervals import IntervalSpec, find_spots
from routes.power import mmss
from tests.test_editing import road


class FixedRoad:
    def __init__(self, points):
        self.points = points

    def route(self, *args, **kwargs):
        return {"points": self.points, "distance_m": _leg_len(self.points),
                "ascent_m": 0, "major_m": 0}


def test_interval_export_keeps_every_bend_and_measured_length(monkeypatch):
    points = [(43, -89, 100)]
    for index in range(70):
        lat, lon, elevation = points[-1]
        points.append((lat + 60 / 111195 if index % 2 == 0 else lat,
                       lon + 60 / (111195 * math.cos(math.radians(lat))) if index % 2 else lon,
                       elevation))
    monkeypatch.setattr("routes.interruptions.fetch_controls", lambda *a: [])
    spot = find_spots(IntervalSpec("public", 1, 5, "flat"), 43, -89,
                      FixedRoad(points), n_spokes=1)[0]
    assert _leg_len(spot.points) == pytest.approx(spot.length_m, abs=.01)
    start = points.index(spot.points[0])
    assert spot.points == points[start:start + len(spot.points)]


def test_reversed_incline_travel_is_measured_to_its_new_start(monkeypatch):
    points = [(43 + i * .001, -89, 500 - i * 4.44) for i in range(60)]
    monkeypatch.setattr("routes.interruptions.fetch_controls", lambda *a: [])
    spec = IntervalSpec("public", 1, 5, "incline")
    spot = find_spots(spec, 43, -89, FixedRoad(points), n_spokes=1)[0]
    assert spot.mean_grade_pct > 0
    assert spot.dist_from_start_m == pytest.approx(_leg_len([points[0], spot.points[0]]), abs=1)
    assert spot.dist_from_start_m <= spec.travel_radius_m


def test_a_hard_stop_limit_requires_known_counts(monkeypatch):
    monkeypatch.setattr("routes.interruptions.fetch_controls", lambda *a: None)
    assert find_spots(IntervalSpec("public", 1, 5, "flat", max_stops=0),
                      43, -89, FixedRoad(road(300)), n_spokes=1) == []


def test_time_rounding_carries_to_the_next_minute():
    assert mmss(59.6) == "1:00"
    assert mmss(3599.6) == "60:00"


def test_any_grade_rep_without_watts_is_sized_at_flat_speed():
    """'Any' means the rider will take whatever road comes; sizing it at
    climbing speed made a 10-minute rep barely half the road it needs."""
    flat = IntervalSpec("public", 1, 10, "flat").rep_distance_m
    assert IntervalSpec("public", 1, 10, "any").rep_distance_m == pytest.approx(flat)
    assert IntervalSpec("public", 1, 10, "incline").rep_distance_m < flat
