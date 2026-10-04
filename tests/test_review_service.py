"""User-visible outcomes and parser/application boundaries."""
import copy

import pytest

from routes import service, storage, edit_service
from routes.intervals import IntervalSpot
from routes.spec import METERS_PER_MILE
from routes.editing import _cum
from routes.requests_model import ParsedRequest
from tests.test_review_storage import artifact


def request(kind="route", **changes):
    result = {"request_type": kind, "address": "public", "notes": "",
              "route": None, "edit": None, "interval": None,
              "_usage": {"model": "test"}}
    if kind == "route":
        result["route"] = dict(distance_miles=20, max_climb_ft=None,
                               maximize_climb=False, minimize_climb=False,
                               shape="loop", via_places=[], avoid_places=[])
    elif kind == "edit_route":
        result["edit"] = dict(mode="anchor", place="my house", places=None,
                              radius_m=1000, miles_delta=None, target_miles=None,
                              connect_return=False, revert_first=False)
    elif kind == "interval_spot":
        result["interval"] = dict(reps=2, rep_minutes=20, kind="flat", max_travel_minutes=30)
    result.update(changes)
    return result


def install_parse(monkeypatch, parsed):
    contexts = []
    def fake(text, client=None, context=None):
        contexts.append(context)
        return copy.deepcopy(parsed)
    monkeypatch.setattr("routes.nl.parse_request", fake)
    return contexts


def test_home_edits_receive_real_address_and_parser_gets_route_context(tmp_path, monkeypatch):
    path = artifact(tmp_path)
    storage.publish(path, str(tmp_path))
    contexts = install_parse(monkeypatch, request("edit_route"))
    forwarded = []
    def edit(*args, **kw):
        forwarded.append(args[1])
        return None, "unchanged", False
    monkeypatch.setattr(edit_service, "run_edit", edit)
    service.handle_request("start and end at home", default_address="123 Public Street",
                           workdir=str(tmp_path))
    assert forwarded == ["123 Public Street"]
    assert contexts[0]["has_current_route"] is True
    assert contexts[0]["start_address"] == "123 Public Street"
    assert contexts[0]["current_distance_miles"] > 0


def test_failed_correction_displays_and_keeps_the_existing_selection(tmp_path, monkeypatch):
    original, edited = artifact(tmp_path), artifact(tmp_path)
    storage.publish(original, str(tmp_path))
    storage.publish(edited, str(tmp_path))
    parsed = request("edit_route")
    parsed["edit"].update(revert_first=True, place="park")
    install_parse(monkeypatch, parsed)
    sources = []
    def fail(path, *a, **kw):
        sources.append(path)
        return None, "cannot route", False
    monkeypatch.setattr(edit_service, "run_edit", fail)
    result = service.handle_request("no, use the park", workdir=str(tmp_path))
    assert sources == [original]
    assert result["candidates"][0]["gpx"] == edited
    assert storage.current_route(str(tmp_path)) == edited


def test_failed_return_connection_is_partial_and_names_the_missing_leg(tmp_path, monkeypatch):
    base = artifact(tmp_path)
    calls = []
    class Router:
        def route(self, points, **kw):
            calls.append(points)
            if len(calls) > 1:
                return None
            geometry = [(a, b, 0) for a, b in points]
            return dict(points=geometry, distance_m=_cum(geometry)[-1], ascent_m=0, major_m=0)
    monkeypatch.setattr(edit_service, "BRouterProvider", lambda **kw: Router())
    monkeypatch.setattr(edit_service, "geocode_flexible", lambda *a, **kw: (43, -89.01, "public"))
    monkeypatch.setattr(edit_service, "build_preview", lambda *a: None)
    out, message, ok = edit_service.run_edit(base, "public", mode="connect", connect_return=True,
                                           out_dir=str(tmp_path))
    assert out and ok == "partial"
    assert "return connection" in message.lower()


def test_unknown_stops_and_power_settings_survive_to_the_web_result(tmp_path, monkeypatch):
    parsed = request("interval_spot")
    parsed["interval"].update(kind="any", watts=285, total_kg=90, max_stops=None)
    install_parse(monkeypatch, parsed)
    monkeypatch.setattr(service, "_router_preflight", lambda: None)
    captured = []
    spot = IntervalSpot(points=[(43, -89, 0), (43.01, -89, 0)],
                        length_m=7 * METERS_PER_MILE, controls_known=False)
    def search(spec, **kw):
        captured.append(spec)
        return [spot]
    monkeypatch.setattr("routes.spot_service.run_spot_search", search)
    result = service.handle_request("interval", workdir=str(tmp_path))
    assert result["ok"] == "partial"
    assert "UNKNOWN stops" in result["candidates"][0]["label"]
    assert result["candidates"][0]["metrics"]["stops"] is None
    assert (captured[0].kind, captured[0].watts, captured[0].total_kg) == ("any", 285, 90)


def test_invalid_branch_and_nonfinite_numbers_do_not_mutate_history(tmp_path, monkeypatch):
    base = artifact(tmp_path)
    storage.publish(base, str(tmp_path))
    parsed = request(route=None)
    install_parse(monkeypatch, parsed)
    result = service.handle_request("ride", workdir=str(tmp_path))
    assert result["ok"] is False
    parsed = request()
    parsed["route"]["distance_miles"] = float("nan")
    install_parse(monkeypatch, parsed)
    assert service.handle_request("ride", workdir=str(tmp_path))["ok"] is False
    assert storage.current_route(str(tmp_path)) == base


def test_explicit_edit_selection_cannot_create_a_new_route(tmp_path, monkeypatch):
    install_parse(monkeypatch, request())
    result = service.handle_request("make it 40 miles", workdir=str(tmp_path), intent="edit_route")
    assert result["ok"] is False
    assert not result["candidates"]
    assert storage.current_route(str(tmp_path)) is None


def test_unfulfilled_notes_are_visible_and_prevent_full_success(tmp_path, monkeypatch):
    install_parse(monkeypatch, request(notes="pavement quality cannot be verified"))
    monkeypatch.setattr(service, "_ride_request", lambda *a: {"ok": True, "summary": "Found a ride", "candidates": []})
    result = service.handle_request("ride", workdir=str(tmp_path))
    assert result["ok"] == "partial"
    assert "pavement quality" in result["summary"]
    assert result["warnings"]


def test_request_model_rejects_conflicting_climb_objectives():
    parsed = request()
    parsed.pop("_usage")
    parsed["route"].update(maximize_climb=True, minimize_climb=True)
    with pytest.raises(ValueError):
        ParsedRequest.model_validate(parsed)


def test_truncated_model_output_is_rejected_before_decoding():
    from types import SimpleNamespace
    from routes.nl import parse_request
    response = SimpleNamespace(stop_reason="max_tokens", content=[])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    with pytest.raises(ValueError, match="too complex"):
        parse_request("ride", client=client)


def test_resource_limits_are_disclosed_in_the_result(tmp_path, monkeypatch):
    parsed = request()
    parsed["route"]["distance_miles"] = 999
    parsed["route"]["via_places"] = ["park"] * 9
    install_parse(monkeypatch, parsed)
    monkeypatch.setattr(service, "_ride_request", lambda *a: {"ok": True, "summary": "Found a ride", "candidates": []})
    result = service.handle_request("ride", workdir=str(tmp_path))
    assert result["ok"] == "partial"
    assert "999 to 150" in result["summary"]
    assert "first 8 of 9" in result["summary"]


def test_avoiding_a_road_the_route_only_crosses_says_so(tmp_path, monkeypatch):
    """The ride crosses Cross Street at one intersection: that is not riding
    it, so the answer is 'nothing to avoid', not 'no detour found'."""
    from routes.gpx_out import write_track
    base = storage.artifact_path(str(tmp_path), "ride")
    # due north along lon -89, a point every ~50 m like routed geometry
    write_track([(43.0 + i * 0.00045, -89.0, 0) for i in range(23)], "ride", "test", base)
    cross_street = [[(43.005, -89.01), (43.005, -88.99)]]  # east-west through it
    monkeypatch.setattr(edit_service, "BRouterProvider", lambda **kw: object())
    monkeypatch.setattr(edit_service, "geocode_flexible",
                        lambda *a, **kw: (43.005, -89.0, "Cross Street"))
    monkeypatch.setattr("routes.road_avoid.fetch_road", lambda *a, **kw: cross_street)
    out, message, ok = edit_service.run_edit(base, "Cross Street", mode="avoid",
                                             out_dir=str(tmp_path))
    assert out is None and ok is False
    assert "only crosses" in message
    assert "Could not find" not in message
