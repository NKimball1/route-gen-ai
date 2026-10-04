from evals.scorers import Check, Measured, score_outcome, score_route, verdict_for


def test_missing_constraint_measurements_cannot_pass():
    case = {"expect": {"route": {"via": ["Park"], "avoid": ["Main Road"]}}}
    measured = Measured(gpx_path="route.gpx", gpx_valid=True, n_points=10, max_point_gap_m=10)
    checks = score_route(case, measured, {})
    assert verdict_for(checks)[0] == "inconclusive"


def test_honesty_requirement_rejects_unqualified_success():
    case = {"expect": {"outcome": "decline_or_honest"}}
    result = {"ok": True, "summary": "Done!", "candidates": [{"gpx": "x"}]}
    assert verdict_for(score_outcome(case, result, {}, None))[0] == "fail"
    result.update(ok="partial", summary="Only part of the request is supported.",
                  warnings=["Unsupported preference"])
    assert verdict_for(score_outcome(case, result, {}, None))[0] == "pass"


def test_unsupported_notes_must_reach_the_user_visible_summary():
    case = {"expect": {"outcome": "success_with_caveat"}}
    result = {"ok": True, "summary": "Done!", "candidates": [{"gpx": "x"}]}
    parsed = {"notes": "cannot verify shoulder width"}
    assert verdict_for(score_outcome(case, result, parsed, None))[0] == "fail"
    result["summary"] += " Cannot verify shoulder width."
    assert verdict_for(score_outcome(case, result, parsed, None))[0] == "pass"


def test_unknown_geometry_is_inconclusive_not_a_pass():
    measured = Measured(gpx_path="x.gpx", gpx_valid=True, n_points=10,
                        distance_mi=1.0, max_point_gap_m=1000, teleports=[],
                        gaps_unknown=[{"road": None}])
    checks = score_route({"expect": {"request_type": "route", "route": {}}}, measured, {})
    continuity = next(c for c in checks if c.name == "geometry.continuous")
    assert continuity.status == "na"
    assert verdict_for([continuity])[0] == "inconclusive"
    assert verdict_for([continuity, Check("broken", "fail", True, "failure")])[0] == "fail"
