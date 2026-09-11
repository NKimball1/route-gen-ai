"""Server-side bounds on LLM-parsed numbers (routes/service.py).

The parser's output is schema-valid but its VALUES come from user text —
on a public deploy that text is attacker-controlled, so every number gets
clamped before it can size an Overpass bbox or a compute budget.
"""
from routes.service import MAX_PLACES, PARSE_BOUNDS, _clamp_parsed


def test_hostile_route_numbers_are_pulled_to_the_edges():
    req = {"request_type": "route", "route": {
        "distance_miles": 90000, "max_climb_ft": -5,
        "avoid_places": [], "via_places": []}}
    _clamp_parsed(req)
    assert req["route"]["distance_miles"] == PARSE_BOUNDS["route"]["distance_miles"][1]
    assert req["route"]["max_climb_ft"] == 0.0


def test_giant_edit_radius_is_capped():
    req = {"request_type": "edit_route", "edit": {
        "mode": "avoid", "place": "x", "radius_m": 5_000_000,
        "miles_delta": -3, "target_miles": None}}
    _clamp_parsed(req)
    assert req["edit"]["radius_m"] == PARSE_BOUNDS["edit"]["radius_m"][1]
    assert req["edit"]["miles_delta"] == 0.0
    assert req["edit"]["target_miles"] is None  # nulls pass through


def test_interval_reps_stay_integers():
    req = {"request_type": "interval_spot", "interval": {
        "reps": 500, "rep_minutes": 0.01, "max_travel_minutes": 30}}
    _clamp_parsed(req)
    assert req["interval"]["reps"] == PARSE_BOUNDS["interval"]["reps"][1]
    assert isinstance(req["interval"]["reps"], int)
    assert req["interval"]["max_travel_minutes"] == 30  # in-range untouched


def test_place_lists_are_truncated():
    req = {"request_type": "route", "route": {
        "distance_miles": 30, "via_places": [f"p{i}" for i in range(40)],
        "avoid_places": ["ok"]}}
    _clamp_parsed(req)
    assert len(req["route"]["via_places"]) == MAX_PLACES
    assert req["route"]["avoid_places"] == ["ok"]


def test_reasonable_request_is_untouched():
    route = {"distance_miles": 50.0, "max_climb_ft": 1000.0,
             "avoid_places": ["Verona Rd"], "via_places": []}
    req = {"request_type": "route", "route": dict(route),
           "interval": None, "edit": None}
    _clamp_parsed(req)
    assert req["route"] == route
