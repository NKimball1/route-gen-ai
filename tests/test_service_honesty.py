"""Regressions for four failures the evaluation campaign found.

The theme running through all four: the app already knew the right answer
internally and did not say it out loud. A dead geocoder escaped as a
ConnectionError traceback; a dead router produced "try a looser target"; an
interval stretch too short for one rep was presented without the lap count
the CLI had already computed. Each test below pins the sentence the user
actually sees, because that was the thing that was wrong.

Case ids refer to evals/cases_v1.json.
"""
import requests

import pytest

from routes import service
from routes.geocode import GeocodeNotFound


def _parse(**over):
    base = {"request_type": "route", "address": "Monona Terrace, Madison WI",
            "route": {"distance_miles": 20, "max_climb_ft": None,
                      "maximize_climb": False, "minimize_climb": False,
                      "shape": "loop", "avoid_places": [], "via_places": []},
            "interval": None, "edit": None, "notes": "",
            "_usage": {"model": "test", "input_tokens": 1, "output_tokens": 1}}
    base.update(over)
    return base


def _install_parse(monkeypatch, parsed):
    import routes.nl
    monkeypatch.setattr(routes.nl, "parse_request",
                        lambda text, client=None, context=None: dict(parsed))


def _patch_geocode(monkeypatch, fn):
    """Patch every name `geocode` is reachable under.

    routes/pipeline.py does `from routes.geocode import geocode` at import
    time, and _dispatch imports pipeline lazily -- so patching only
    routes.geocode.geocode leaks the stub permanently into whichever test
    happens to trigger that first import.
    """
    import routes.geocode
    import routes.pipeline
    monkeypatch.setattr(routes.geocode, "geocode", fn)
    monkeypatch.setattr(routes.pipeline, "geocode", fn)


# ---- D37: a geocoder that cannot be reached -----------------------------

def test_unreachable_geocoder_is_a_message_not_a_traceback(monkeypatch, tmp_path):
    """Case D37. Before the fix this raised requests.ConnectionError straight
    out of handle_request: a traceback on the CLI, and in the web app a
    banner reading 'ERROR: ConnectionError: HTTPConnectionPool(host=...)'."""
    _install_parse(monkeypatch, _parse(
        route={"distance_miles": 20, "max_climb_ft": None,
               "maximize_climb": False, "minimize_climb": False,
               "shape": "loop", "avoid_places": [],
               "via_places": ["Olbrich Gardens, Madison WI"]}))

    _patch_geocode(monkeypatch, lambda a: (_ for _ in ()).throw(
        requests.ConnectionError("boom")))

    result = service.handle_request("a 20 mile loop", workdir=str(tmp_path))
    assert result["ok"] is False
    summary = result["summary"].lower()
    assert "lookup" in summary or "nominatim" in summary
    assert "try again" in summary
    assert "connectionpool" not in summary and "traceback" not in summary


def test_unfindable_place_says_which_place(monkeypatch, tmp_path):
    """Case H14: 'the place with the good pie'. A ValueError from Nominatim
    used to escape the same way."""
    _install_parse(monkeypatch, _parse(
        route={"distance_miles": 30, "max_climb_ft": None,
               "maximize_climb": False, "minimize_climb": False,
               "shape": "loop", "avoid_places": [],
               "via_places": ["place with the good pie, Verona, WI"]}))

    _patch_geocode(monkeypatch, lambda a: (43.07, -89.38, "Monona Terrace"))
    monkeypatch.setattr("routes.geocode.geocode_flexible", lambda place, **kw: (_ for _ in ()).throw(GeocodeNotFound(f"Could not geocode address: {place!r}")))

    result = service.handle_request("30 mile loop", workdir=str(tmp_path))
    assert result["ok"] is False
    assert "good pie" in result["summary"]
    assert "couldn't find" in result["summary"].lower()


def test_lookup_failure_is_not_swallowed_into_a_route(monkeypatch, tmp_path):
    """A failed lookup must not quietly produce a route without the
    waypoint the rider asked for."""
    _install_parse(monkeypatch, _parse(
        route={"distance_miles": 20, "max_climb_ft": None,
               "maximize_climb": False, "minimize_climb": False,
               "shape": "loop", "avoid_places": [], "via_places": ["nowhere"]}))
    _patch_geocode(monkeypatch, lambda a: (_ for _ in ()).throw(
        GeocodeNotFound("nope")))
    result = service.handle_request("x", workdir=str(tmp_path))
    assert result["candidates"] == []
    assert result["kind"] == "error"


# ---- D36: a router that cannot be reached -------------------------------

def test_dead_router_is_not_reported_as_a_bad_request(monkeypatch, tmp_path):
    """Case D36. The log said ROUTING SERVER UNREACHABLE; the banner said
    'No route met the constraints - try a looser target or different
    distance', which sends the rider to fix a request that was fine."""
    _install_parse(monkeypatch, _parse())
    _patch_geocode(monkeypatch, lambda a: (43.07, -89.38, "Monona Terrace"))
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: False)

    result = service.handle_request("a 20 mile loop", workdir=str(tmp_path))
    assert result["ok"] is False
    summary = result["summary"].lower()
    assert "routing server" in summary
    assert "nothing is wrong with your request" in summary
    assert "looser target" not in summary


def test_dead_router_fails_fast_without_routing_calls(monkeypatch, tmp_path):
    """The preflight exists so a black-holed router costs one TCP connect
    rather than a dozen two-minute timeouts."""
    _install_parse(monkeypatch, _parse())
    _patch_geocode(monkeypatch, lambda a: (43.07, -89.38, "Monona Terrace"))
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: False)
    calls = []
    monkeypatch.setattr("routes.providers.BRouterProvider.route",
                        lambda self, *a, **k: calls.append(1))
    service.handle_request("a 20 mile loop", workdir=str(tmp_path))
    assert calls == []


def test_router_dying_mid_run_still_names_the_router(monkeypatch, tmp_path):
    """Preflight can pass and the server die a second later, so the
    end-of-run summary has to tell the two failures apart too."""
    _install_parse(monkeypatch, _parse())
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: True)

    def dead_route(self, *a, **k):
        self.unreachable = True
        return None

    monkeypatch.setattr("routes.providers.BRouterProvider.route", dead_route)
    _patch_geocode(monkeypatch, lambda a: (43.07, -89.38, "Monona Terrace"))
    result = service.handle_request("a 20 mile loop", workdir=str(tmp_path))
    assert "routing server" in (result["summary"] or "").lower()
    assert "looser target" not in (result["summary"] or "").lower()


def test_genuine_no_candidates_still_says_loosen_the_target(monkeypatch, tmp_path):
    """Teeth: the honest 'your constraints were too tight' message must
    survive. Otherwise the fix above would just relabel every failure."""
    _install_parse(monkeypatch, _parse())
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: True)
    monkeypatch.setattr("routes.providers.BRouterProvider.route",
                        lambda self, *a, **k: None)   # answers, finds nothing
    _patch_geocode(monkeypatch, lambda a: (43.07, -89.38, "Monona Terrace"))
    result = service.handle_request("a 20 mile loop", workdir=str(tmp_path))
    assert "looser target" in (result["summary"] or "").lower()
    assert "routing server" not in (result["summary"] or "").lower()


# ---- D18: an interval stretch shorter than one rep ----------------------

@pytest.mark.parametrize("stretch_m,rep_m,laps", [
    (10000.0, 6700.0, 1),      # holds a whole rep
    (6700.0, 6700.0, 1),       # exactly one rep
    (6600.0, 6700.0, 1),       # 1.5% short: rep pace is an assumption, not
                               # a measurement, so this is still one lap
    (6400.0, 6700.0, 1),       # 4.5% short: inside the 5% grace
    (6200.0, 6700.0, 2),       # 7.5% short: outside it, so say so
    (5500.0, 6700.0, 2),       # D18: 5.5 mi stretch, 6.7 mi rep
    (2000.0, 6700.0, 4),
    (0.0, 6700.0, 1),          # degenerate input must not divide by zero
])
def test_lap_arithmetic(stretch_m, rep_m, laps):
    assert service._laps_for_rep(stretch_m, rep_m) == laps


def test_short_interval_spot_discloses_the_lap_count(monkeypatch, tmp_path):
    """Case D18. Lapping a short stretch is a supported answer; presenting
    it as if it held a full rep is not. run_spot_search already printed the
    lap count to the log -- the label the rider reads dropped it."""
    from routes.intervals import IntervalSpot

    _install_parse(monkeypatch, _parse(
        request_type="interval_spot", route=None,
        interval={"reps": 2, "rep_minutes": 20, "kind": "flat",
                  "max_travel_minutes": 20}))
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: True)
    short = IntervalSpot(points=[(43.07, -89.38, 260.0), (43.10, -89.38, 261.0)],
                         length_m=5500.0, mean_grade_pct=0.1,
                         grade_std_pct=0.4, turns_per_km=0.5, n_controls=11,
                         dist_from_start_m=100.0, bearing=0.0, score=0.5)
    monkeypatch.setattr("routes.spot_service.run_spot_search",
                        lambda spec, out_dir=None: [short])

    result = service.handle_request("2x20 threshold", workdir=str(tmp_path))
    label = result["candidates"][0]["label"]
    assert "2 laps per rep" in label
    assert "6.7 mi of road" in result["summary"]


def test_long_enough_interval_spot_says_nothing_about_laps(monkeypatch, tmp_path):
    """Teeth: the disclosure must be conditional, not boilerplate on every
    result."""
    from routes.intervals import IntervalSpot

    _install_parse(monkeypatch, _parse(
        request_type="interval_spot", route=None,
        interval={"reps": 2, "rep_minutes": 20, "kind": "flat",
                  "max_travel_minutes": 20}))
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: True)
    roomy = IntervalSpot(points=[(43.07, -89.38, 260.0), (43.20, -89.38, 261.0)],
                         length_m=12000.0, mean_grade_pct=0.1,
                         grade_std_pct=0.4, turns_per_km=0.5, n_controls=2,
                         dist_from_start_m=100.0, bearing=0.0, score=0.9)
    monkeypatch.setattr("routes.spot_service.run_spot_search",
                        lambda spec, out_dir=None: [roomy])
    result = service.handle_request("2x20 threshold", workdir=str(tmp_path))
    assert "laps per rep" not in result["candidates"][0]["label"]


def test_unreachable_geocoder_on_the_START_address(monkeypatch, tmp_path):
    """The first version of the D37 fix wrapped the WAYPOINT lookups and
    missed this one: the start address is geocoded inside
    routes/pipeline.compose, below the service layer, so it still escaped
    as a raw ConnectionError. Every lookup failure now reaches the same
    boundary no matter which layer raised it."""
    from routes.geocode import GeocodeUnavailable

    _install_parse(monkeypatch, _parse())
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: True)
    _patch_geocode(monkeypatch, lambda a: (_ for _ in ()).throw(
        GeocodeUnavailable("nominatim down")))

    result = service.handle_request("a 20 mile loop", workdir=str(tmp_path))
    assert result["ok"] is False
    assert "nominatim" in result["summary"].lower()
    assert result["candidates"] == []


def test_a_real_bug_below_the_boundary_is_not_disguised_as_a_lookup(
        monkeypatch, tmp_path):
    """Teeth: the boundary translates LOOKUP failures only. A genuine
    programming error must still surface as itself rather than be dressed
    up as 'couldn't find that place'."""
    _install_parse(monkeypatch, _parse())
    monkeypatch.setattr("routes.providers.brouter_reachable",
                        lambda url, timeout_s=1.0: True)
    _patch_geocode(monkeypatch, lambda a: (_ for _ in ()).throw(
        TypeError("someone passed the wrong thing")))
    with pytest.raises(TypeError):
        service.handle_request("a 20 mile loop", workdir=str(tmp_path))
