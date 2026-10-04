"""Constraint and geometry regressions independent of live providers."""
import pytest

from routes.despur import _leg_len
from routes.spec import RouteSpec


class FixedRoad:
    def __init__(self, points):
        self.points = points

    def route(self, *args, **kwargs):
        return {"points": self.points, "distance_m": _leg_len(self.points),
                "ascent_m": 0, "major_m": 0}


@pytest.mark.parametrize("distance", [0, -1, float("nan"), float("inf")])
def test_direct_route_specs_reject_invalid_distance(distance):
    with pytest.raises(ValueError, match="distance"):
        RouteSpec("public", distance)


def test_shared_climb_search_does_not_consume_personal_strava_tokens(monkeypatch):
    from routes.climbs import find_climbs
    monkeypatch.setattr("routes.strava.available", lambda: pytest.fail("accessed personal Strava account"))
    monkeypatch.setattr("routes.peaks.fetch_peaks", lambda *a: [])
    assert find_climbs(43, -89, 1000, FixedRoad([]), n_spokes=0) == []
