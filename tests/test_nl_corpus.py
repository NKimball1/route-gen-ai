"""NL parse-regression corpus — the parser caused the subtlest field bugs
(avoid-vs-via 'instead', comma-joined places, move_start when both ends
were meant, corrections stacking). This pins its behavior on realistic
phrasings.

Live LLM calls (~$0.03 for the corpus), so opt-in:
    RUN_LLM_TESTS=1 python -m pytest tests/test_nl_corpus.py -v
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LLM_TESTS") != "1",
    reason="live LLM corpus; set RUN_LLM_TESTS=1 to run")


CASES = [
    # (text, expected request_type, checks on the parsed edit/objects)
    ("can you avoid mineral point road",
     "edit_route", {"mode": "avoid"}),
    ("actually can we take mineral point road instead of this one",
     "edit_route", {"mode": "via"}),
    ("swing by the memorial union terrace on the way",
     "edit_route", {"mode": "via"}),
    ("go through cherokee marsh, then take the path along the river",
     "edit_route", {"mode": "via", "places_min": 2}),
    ("make this ride start and end at olin park",
     "edit_route", {"mode": "anchor"}),
    ("start from olin park instead",
     "edit_route", {"mode": "move_start"}),
    ("end the ride at the memorial union",
     "edit_route", {"mode": "move_end"}),
    ("undo that",
     "undo", {}),
    ("go back to the previous version",
     "undo", {}),
    ("that last edit wasn't right — I wanted seminole highway, not verona road",
     "edit_route", {"revert_first": True}),
    ("make it about 12 miles longer",
     "edit_route", {"mode": "extend", "miles_delta": 12}),
    ("trim this down to about 35 miles total",
     "edit_route", {"mode": "shorten_or_target", "target_miles": 35}),
    ("route me from the capitol to the start of this ride",
     "edit_route", {"mode": "connect", "connect_return": False}),
    ("add a leg from the capitol to the start, and bring me back to the "
     "capitol at the end",
     "edit_route", {"mode": "connect", "connect_return": True}),
    ("now give me a totally new 25 mile loop from home instead",
     "route", {}),
    ("find me a steady hill near home for some 30/30 intervals",
     "interval_spot", {}),
    # --- route vs edit: both directions, from eval cases D15 and H23 ---
    # A fresh route that LEADS with a complaint used to classify as an
    # avoid edit, and the app answered "No current route to edit".
    ("I hate riding on mineral point road -- 22 mile loop from olin park "
     "without it",
     "route", {"route_avoid_min": 1, "route_miles": 22}),
    ("give me a 30 mile ride from the capitol that stays off beltline "
     "highway",
     "route", {"route_avoid_min": 1, "route_miles": 30}),
    ("40 mile loop from verona that goes through paoli",
     "route", {"route_via_min": 1}),
    # ...and an edit with no stated length used to become a brand-new route
    # with an invented distance, discarding the rider's current one.
    ("route me through vilas park and then the arboretum",
     "edit_route", {"mode": "via", "places_min": 2}),
    ("take me past the memorial union",
     "edit_route", {"mode": "via"}),
    ("stay off monroe street",
     "edit_route", {"mode": "avoid"}),
]


def r_route(parsed):
    return parsed.get("route") or {}


@pytest.mark.parametrize("text,expected_type,checks", CASES,
                         ids=[c[0][:44] for c in CASES])
def test_corpus(text, expected_type, checks):
    from dotenv import load_dotenv
    load_dotenv(".env")
    from routes.nl import parse_request
    r_all = parse_request(text, context={"has_current_route": True, "current_distance_miles": 30,
                                        "start_address": "Monona Terrace, Madison WI", "operation": "auto"})
    r = r_all
    assert r["request_type"] == expected_type, r
    e = r.get("edit") or {}
    for key, want in checks.items():
        if key == "places_min":
            assert e.get("places") and len(e["places"]) >= want, e
        elif key == "mode" and want == "shorten_or_target":
            assert e.get("mode") in ("shorten", "extend"), e
        elif key == "miles_delta":
            assert e.get("miles_delta") == pytest.approx(want, abs=3), e
        elif key == "route_avoid_min":
            r = r_route(r_all)
            assert len(r.get("avoid_places") or []) >= want, r
        elif key == "route_via_min":
            r = r_route(r_all)
            assert len(r.get("via_places") or []) >= want, r
        elif key == "route_miles":
            r = r_route(r_all)
            assert r.get("distance_miles") == pytest.approx(want, abs=3), r
        elif key == "target_miles":
            assert e.get("target_miles") == pytest.approx(want, abs=3) \
                or e.get("miles_delta") is not None, e
        else:
            assert e.get(key) == want, (key, e)


def test_contextless_change_never_invents_a_new_ride():
    from routes.nl import parse_request
    parsed = parse_request("make it 40 miles total", context={
        "has_current_route": False, "start_address": "Monona Terrace, Madison WI", "operation": "auto"})
    assert parsed["request_type"] in ("edit_route", "clarify")
    assert parsed["route"] is None


def test_power_either_direction_and_hard_stop_cap():
    from routes.nl import parse_request
    parsed = parse_request("Find a stretch near home for 4x4 at 285 watts, rider plus bike 90 kg, "
                           "ridden in either direction, no stop signs or signals", context={
        "has_current_route": False, "start_address": "Monona Terrace, Madison WI", "operation": "interval_spot"})
    assert parsed["request_type"] == "interval_spot"
    interval = parsed["interval"]
    assert (interval["kind"], interval["watts"], interval["total_kg"], interval["max_stops"]) == ("any", 285, 90, 0)
