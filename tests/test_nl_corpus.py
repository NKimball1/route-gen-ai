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
]


@pytest.mark.parametrize("text,expected_type,checks", CASES,
                         ids=[c[0][:44] for c in CASES])
def test_corpus(text, expected_type, checks):
    from dotenv import load_dotenv
    load_dotenv(".env")
    from routes.nl import parse_request
    r = parse_request(text)
    assert r["request_type"] == expected_type, r
    e = r.get("edit") or {}
    for key, want in checks.items():
        if key == "places_min":
            assert e.get("places") and len(e["places"]) >= want, e
        elif key == "mode" and want == "shorten_or_target":
            assert e.get("mode") in ("shorten", "extend"), e
        elif key == "miles_delta":
            assert e.get("miles_delta") == pytest.approx(want, abs=3), e
        elif key == "target_miles":
            assert e.get("target_miles") == pytest.approx(want, abs=3) \
                or e.get("miles_delta") is not None, e
        else:
            assert e.get(key) == want, (key, e)
