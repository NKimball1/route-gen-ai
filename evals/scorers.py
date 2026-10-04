"""Deterministic scoring for the evaluation campaign.

Every judgement in this file is a computation. No model is asked whether a
route is good, and in particular the model that produced the route is never
asked to grade it.

Two layers:

1. INTERPRETATION -- did the parse mean what the request said? Checked
   field-by-field against expectations frozen in evals/cases_v1.json.
2. ARTIFACT -- is the thing that came back a valid, honest answer? Checked
   against the GPX on disk with evals/geo.py, which re-implements the
   measurements rather than trusting the app's own reported numbers.

Tolerances and their reasons are in TOLERANCES below. A check is "hard"
(a promise the product makes) or "soft" (a preference it tries to honor);
only hard failures can turn a case red.
"""
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Sequence

from evals import geo

Status = Literal["pass", "fail", "na", "info"]
Verdict = Literal["pass", "partial", "fail", "inconclusive"]


TOLERANCES: dict[str, dict[str, Any]] = {
    "distance": {
        "default": 0.15,
        "why": "The product's own acceptance band: routes/scoring.py rejects any "
               "candidate more than 15% from the target, so 15% is the promise "
               "being tested, not a number chosen to flatter the result. Edits "
               "use 18% because an edit re-routes around fixed endpoints and has "
               "less freedom to hit a length exactly.",
    },
    "loop_closure_m": {
        "default": 250.0,
        "why": "routes/editing._is_loop uses 250 m. Wider than it looks: a loop "
               "that starts and ends on opposite sides of a divided road is "
               "closed for a rider.",
    },
    "start_proximity_m": {
        "default": 600.0,
        "why": "Geocoding a landmark returns a building centroid; the router "
               "snaps to the nearest routable way. 600 m absorbs both without "
               "hiding a route that starts in the wrong place.",
    },
    "via_m": {
        "default": 150.0,
        "why": "Review revision 2026-10-03: waypoint acceptance is now 150 m. "
               "Explicit case tolerances remain visible overrides; historical "
               "reports are not rewritten automatically.",
    },
    "avoid_on_road_m": {
        "default": 30.0,
        "why": "Review revision 2026-10-03: at most 30 m on the excluded road, "
               "allowing a crossing but not a block of riding along it.",
    },
    "teleport": {
        "default": "gap > 800 m AND a bearing change of 60 deg or more",
        "why": "A first attempt used a plain 600 m gap threshold and flagged "
               "three perfectly good routes: Wisconsin's section-line grid "
               "gives dead-straight mile-long roads, which BRouter emits as a "
               "single segment. Requiring the long segment to ALSO turn away "
               "from the route's heading keeps the check able to catch a real "
               "splice without punishing straight rural geometry. Recorded "
               "here because the threshold was wrong before it was right.",
    },
    "repeat_frac": {
        "default": 0.25,
        "why": "routes/scoring.py rejects loops above 25% repeated road. "
               "Reported for every route; only enforced on loops, because an "
               "out-and-back is 100% repeated BY DESIGN and flagging that "
               "would be a scorer bug, not a product bug.",
    },
    "climb_cap_slack": {
        "default": 0.0,
        "why": "A stated ceiling is a hard promise: routes/scoring.py rejects "
               "anything above it, so the eval does too, with no slack. The "
               "elevation figure itself carries roughly +/-10% instrument "
               "spread (see PROVENANCE), which is reported alongside.",
    },
}

PROVENANCE: dict[str, str] = {
    "geometry": "BRouter 1.7.10, self-hosted, profile fastbike-quiet, OSM "
                "extract tiles W90_N40 / W95_N40 (southern Wisconsin).",
    "elevation": "SRTM-derived elevations carried in BRouter's GeoJSON output. "
                 "The app's climb figure is routes/elevation.track_ascent, a "
                 "model calibrated against RideWithGPS and one barometric "
                 ".fit file; the devlog records that instruments themselves "
                 "disagree by 8-16%, so +/-10% is the attainable accuracy. "
                 "The eval reports the app's number AND an unsmoothed "
                 "independent sum over the same elevations. Neither is "
                 "ground truth -- only a barometric ride is.",
    "geocoding": "OSM Nominatim, public endpoint, no key.",
    "road_geometry": "OSM Overpass API, way centerlines, used for the "
                     "avoid-road measurement.",
    "not_measured": "Whether a route is SAFE or PLEASANT to ride. No check in "
                    "this harness establishes that. Traffic volume, shoulder "
                    "width, pavement quality, sightlines and seasonal "
                    "conditions are outside what OSM tags and this eval can "
                    "see. Those claims would need field riding.",
}


@dataclass
class Check:
    name: str
    status: Status
    hard: bool
    detail: str
    value: Any = None
    limit: Any = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Measured:
    """Everything the harness measured about the returned artifact."""
    gpx_path: str | None = None
    gpx_valid: bool | None = None
    gpx_error: str | None = None
    n_points: int | None = None
    distance_mi: float | None = None
    app_reported_mi: float | None = None
    ascent_ft_app: float | None = None
    ascent_ft_independent: float | None = None
    loop_closure_m: float | None = None
    start_proximity_m: float | None = None
    end_proximity_m: float | None = None
    via_distances_m: dict[str, float] = field(default_factory=dict)
    avoid_on_road_m: dict[str, float] = field(default_factory=dict)
    max_point_gap_m: float | None = None
    duplicate_points: int | None = None
    repeat_frac: float | None = None
    mirror_frac: float | None = None
    teleports: list[dict[str, Any]] = field(default_factory=list)
    gaps_confirmed_road: list[dict[str, Any]] = field(default_factory=list)
    gaps_unknown: list[dict[str, Any]] = field(default_factory=list)
    has_elevation: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# interpretation layer
# --------------------------------------------------------------------------

def _dig(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match(value: Any, rule: dict[str, Any]) -> tuple[bool, str]:
    if "equals" in rule:
        return value == rule["equals"], f"{value!r} vs expected {rule['equals']!r}"
    if "in" in rule:
        return value in rule["in"], f"{value!r} vs allowed {rule['in']!r}"
    if "range" in rule:
        lo, hi = rule["range"]
        ok = isinstance(value, (int, float)) and lo <= value <= hi
        return ok, f"{value!r} vs [{lo}, {hi}]"
    if "len_min" in rule:
        n = len(value) if isinstance(value, (list, tuple, str)) else 0
        return n >= rule["len_min"], f"{n} item(s), need >= {rule['len_min']}"
    if "nonempty" in rule:
        ok = bool(value) and (not isinstance(value, str) or bool(value.strip()))
        return ok == rule["nonempty"], f"{'present' if ok else 'empty'}"
    return False, f"unknown rule {rule!r}"


def score_parse(case: dict[str, Any], parsed: dict[str, Any] | None
                ) -> list[Check]:
    """Did the system interpret the request the way the frozen expectation says?"""
    checks: list[Check] = []
    want_type = case["expect"].get("request_type")
    if parsed is None:
        checks.append(Check("parse.reached", "fail", True,
                            "no parse was produced (the request never got that far)"))
        return checks
    got_type = parsed.get("request_type")
    checks.append(Check(
        "parse.request_type", "pass" if got_type == want_type else "fail", True,
        f"got {got_type!r}, expected {want_type!r}", got_type, want_type))

    for dotted, rule in (case["expect"].get("parse") or {}).items():
        # a few derived predicates that no single field expresses
        if dotted == "route.not_both_climb_flags":
            r = parsed.get("route") or {}
            val = not (r.get("maximize_climb") and r.get("minimize_climb"))
            ok, detail = val == rule.get("equals", True), (
                f"maximize={r.get('maximize_climb')} minimize={r.get('minimize_climb')}")
        elif dotted == "edit.target_or_delta":
            e = parsed.get("edit") or {}
            val = e.get("target_miles") is not None or e.get("miles_delta") is not None
            ok, detail = val == rule.get("equals", True), (
                f"target_miles={e.get('target_miles')} miles_delta={e.get('miles_delta')}")
        elif dotted == "edit.places_len_min":
            e = parsed.get("edit") or {}
            places = e.get("places") or ([e["place"]] if e.get("place") else [])
            ok = len(places) >= rule.get("equals", 2)
            detail = f"{len(places)} place string(s): {places!r}"
        else:
            value = _dig(parsed, dotted)
            ok, detail = _match(value, rule)
        checks.append(Check(f"parse.{dotted.split('.')[-1]}",
                            "pass" if ok else "fail", True, detail))
    return checks


# --------------------------------------------------------------------------
# artifact layer
# --------------------------------------------------------------------------

def measure_route(gpx_path: str | None, case: dict[str, Any],
                  resolved: dict[str, Any],
                  app_label: str | None) -> Measured:
    """Re-measure the returned GPX from scratch. `resolved` carries geocoded
    coordinates the runner looked up (start, via targets, avoid road ways)."""
    m = Measured(gpx_path=gpx_path)
    if not gpx_path:
        return m
    try:
        pts = geo.parse_gpx_strict(gpx_path)
    except geo.GpxProblem as e:
        m.gpx_valid = False
        m.gpx_error = str(e)
        return m
    except OSError as e:
        m.gpx_valid = False
        m.gpx_error = f"unreadable: {e}"
        return m
    m.gpx_valid = True
    m.n_points = len(pts)
    m.has_elevation = any(p[2] is not None for p in pts)
    m.distance_mi = geo.fmt_mi(geo.track_length_m(pts))
    m.loop_closure_m = round(geo.haversine_m(pts[0], pts[-1]), 1)
    m.max_point_gap_m = round(geo.max_point_gap_m(pts), 1)
    # Flagged gaps are only suspects. Each one is then checked against the
    # road network: a segment a road actually runs along is not a break.
    from evals.roadcheck import classify_gaps
    breaks, roads, unknown = classify_gaps(pts, geo.suspicious_gaps(pts))
    m.teleports = breaks
    m.gaps_confirmed_road = roads
    m.gaps_unknown = unknown
    m.mirror_frac = round(geo.mirror_fraction(pts), 3)
    m.duplicate_points = geo.duplicate_point_runs(pts)
    m.ascent_ft_independent = geo.fmt_ft(geo.ascent_m_simple(pts))

    from routes.elevation import track_ascent
    from routes.overlap import repeated_fraction
    m.ascent_ft_app = geo.fmt_ft(track_ascent(list(pts)))
    m.repeat_frac = round(repeated_fraction(pts), 3)

    if app_label:
        import re
        hit = re.search(r"([\d.]+)\s*mi\b", app_label)
        if hit:
            m.app_reported_mi = float(hit.group(1))

    start = resolved.get("start_latlon")
    if start:
        m.start_proximity_m = round(geo.haversine_m(start, pts[0]), 1)
        m.end_proximity_m = round(geo.haversine_m(start, pts[-1]), 1)
    for name, latlon in (resolved.get("via_latlon") or {}).items():
        m.via_distances_m[name] = round(geo.min_dist_to_track_m(latlon, pts), 1)
    if resolved.get("end_latlon"):
        m.end_proximity_m = round(geo.haversine_m(resolved["end_latlon"], pts[-1]), 1)
    for name, ways in (resolved.get("avoid_ways") or {}).items():
        from routes.road_avoid import on_road_meters
        m.avoid_on_road_m[name] = round(on_road_meters(list(pts), ways), 1)
    return m


def score_spot(case: dict[str, Any], m: Measured,
               parsed: dict[str, Any] | None,
               resolved: dict[str, Any],
               result: dict[str, Any] | None = None) -> list[Check]:
    """An interval spot is a STRETCH OF ROAD, deliberately away from the
    start -- so the route checks (starts here, closes into a loop) do not
    apply.

    What it promises instead: reachable inside the stated travel budget, and
    HONEST about fit. Lapping a stretch shorter than one rep is a supported
    answer (routes/intervals.py ranks length only up to one rep, on the
    stated reasoning that you can lap a short stretch) -- so 'too short' is
    a soft miss, while failing to TELL the rider they will be lapping is a
    hard failure. Reading the implementation is what moved that line; the
    first version of this check graded length alone and would have pushed
    the product toward refusing perfectly usable stretches.
    """
    checks: list[Check] = []
    iv = (parsed or {}).get("interval") or {}
    if not iv or m.distance_mi is None:
        return checks
    from routes.intervals import IntervalSpec
    spec = IntervalSpec("", int(iv.get("reps") or 1),
                        float(iv.get("rep_minutes") or 1),
                        str(iv.get("kind") or "flat"),
                        float(iv.get("max_travel_minutes") or 30))
    if m.start_proximity_m is not None:
        limit = spec.travel_radius_m
        checks.append(Check(
            "spot.within_travel_budget",
            "pass" if m.start_proximity_m <= limit else "fail", True,
            f"stretch begins {m.start_proximity_m / 1000:.1f} km from the "
            f"start; the stated budget of {iv.get('max_travel_minutes')} min "
            f"allows {limit / 1000:.1f} km at the app's assumed 15 mph "
            f"travel pace",
            round(m.start_proximity_m, 0), round(limit, 0)))

    need_mi = spec.rep_distance_m / geo.METERS_PER_MILE
    stretch_m = m.distance_mi * geo.METERS_PER_MILE
    # 5% grace, for the same reason the product allows one: rep length comes
    # from an assumed fixed pace, so a stretch 1% short is one lap, not two.
    # Computed here independently rather than imported, so the eval does not
    # simply agree with the product by construction.
    need_m = spec.rep_distance_m * 0.95
    laps = 1 if stretch_m <= 0 else max(1, math.ceil(need_m / stretch_m - 1e-9))
    checks.append(Check(
        "spot.long_enough_for_a_rep",
        "pass" if laps <= 1 else "fail", False,
        f"{m.distance_mi:.2f} mi of road for a {iv.get('rep_minutes')}-minute "
        f"{iv.get('kind')} rep, which needs about {need_mi:.2f} mi at the "
        f"app's assumed pace ({laps} lap(s) per rep)",
        m.distance_mi, round(need_mi, 2)))

    if laps > 1:
        shown = " ".join([(result or {}).get("summary") or ""]
                         + list((result or {}).get("candidate_labels") or []))
        told = "lap" in shown.lower()
        checks.append(Check(
            "spot.lap_count_disclosed", "pass" if told else "fail", True,
            f"the stretch needs {laps} laps per rep; the text the rider sees "
            f"{'says so' if told else 'does NOT mention lapping'}: "
            f"{shown.strip()[:200]!r}"))
    return checks


def score_route(case: dict[str, Any], m: Measured,
                parsed: dict[str, Any] | None) -> list[Check]:
    exp = case["expect"].get("route") or {}
    checks: list[Check] = []

    if m.gpx_path is None:
        return checks
    is_spot = (parsed or {}).get("request_type") == "interval_spot"
    if not m.gpx_valid:
        checks.append(Check("gpx.valid", "fail", True,
                            m.gpx_error or "unknown GPX problem"))
        return checks
    checks.append(Check("gpx.valid", "pass", True,
                        f"well-formed, {m.n_points} points, "
                        f"elevation {'present' if m.has_elevation else 'ABSENT'}"))

    # ---- distance ----
    target = exp.get("target_miles")
    if target == "from_parse":
        target = _dig(parsed or {}, "route.distance_miles")
    if target is not None and m.distance_mi is not None:
        tol = exp.get("distance_tolerance", TOLERANCES["distance"]["default"])
        err = abs(m.distance_mi - target) / target
        checks.append(Check(
            "route.distance", "pass" if err <= tol else "fail", True,
            f"{m.distance_mi:.2f} mi vs target {target} mi "
            f"({err:+.1%}, tolerance +/-{tol:.0%})",
            m.distance_mi, target))

    delta = exp.get("distance_delta_mi")
    if delta is not None and m.distance_mi is not None and exp.get("_before_mi"):
        got = m.distance_mi - exp["_before_mi"]
        dtol = exp.get("distance_delta_tolerance_mi", 3.0)
        checks.append(Check(
            "route.distance_delta", "pass" if abs(got - delta) <= dtol else "fail",
            True, f"grew {got:+.2f} mi, asked for {delta:+.1f} mi "
                  f"(tolerance +/-{dtol} mi)", round(got, 2), delta))

    # ---- shape ----
    if is_spot:
        pass
    elif exp.get("loop") is True and m.loop_closure_m is not None:
        lim = exp.get("loop_closure_m", TOLERANCES["loop_closure_m"]["default"])
        checks.append(Check(
            "route.loop_closure", "pass" if m.loop_closure_m <= lim else "fail",
            True, f"start-to-finish gap {m.loop_closure_m:.0f} m "
                  f"(limit {lim:.0f} m)", m.loop_closure_m, lim))
    elif exp.get("loop") is False and exp.get("shape") == "outback" \
            and m.loop_closure_m is not None:
        lim = exp.get("loop_closure_m", TOLERANCES["loop_closure_m"]["default"])
        checks.append(Check(
            "route.outback_returns", "pass" if m.loop_closure_m <= lim else "fail",
            True, f"out-and-back finishes {m.loop_closure_m:.0f} m from the "
                  f"start (limit {lim:.0f} m)", m.loop_closure_m, lim))

    # ---- start / end ----
    # Only a FRESH route promises to begin at the stated address. An
    # interval spot is a stretch deliberately out of town, and an edit
    # inherits whatever start the route it edits already had -- checking
    # either against the request's start address measures nothing.
    fresh_route = (case["expect"].get("request_type") == "route"
                   and not is_spot)
    if m.start_proximity_m is not None and fresh_route \
            and not exp.get("anchor") and not exp.get("end_at"):
        lim = TOLERANCES["start_proximity_m"]["default"]
        checks.append(Check(
            "route.starts_where_asked",
            "pass" if m.start_proximity_m <= lim else "fail", True,
            f"begins {m.start_proximity_m:.0f} m from the geocoded start "
            f"(limit {lim:.0f} m)", m.start_proximity_m, lim))
    if exp.get("anchor") and m.start_proximity_m is not None:
        lim = exp.get("anchor_tolerance_m", 1500.0)
        worst = max(m.start_proximity_m, m.end_proximity_m or 0.0)
        checks.append(Check(
            "route.anchored", "pass" if worst <= lim else "fail", True,
            f"start {m.start_proximity_m:.0f} m / finish "
            f"{m.end_proximity_m:.0f} m from the anchor (limit {lim:.0f} m)",
            worst, lim))
    if exp.get("end_at") and m.end_proximity_m is not None:
        lim = exp.get("end_tolerance_m", 1200.0)
        checks.append(Check(
            "route.ends_where_asked",
            "pass" if m.end_proximity_m <= lim else "fail", True,
            f"finishes {m.end_proximity_m:.0f} m from the requested end "
            f"(limit {lim:.0f} m)", m.end_proximity_m, lim))

    # ---- climbing ----
    cap = exp.get("max_climb_ft")
    if cap == "from_parse":
        cap = _dig(parsed or {}, "route.max_climb_ft")
    if cap is not None and m.ascent_ft_app is not None:
        checks.append(Check(
            "route.climb_cap", "pass" if m.ascent_ft_app <= cap else "fail", True,
            f"{m.ascent_ft_app:.0f} ft vs cap {cap:.0f} ft "
            f"(app model; independent unsmoothed sum "
            f"{m.ascent_ft_independent:.0f} ft)", m.ascent_ft_app, cap))
    floor = exp.get("min_climb_ft")
    if floor is not None and m.ascent_ft_app is not None:
        # SOFT: 'as much climbing as you can' is a ranking goal, not a promise.
        checks.append(Check(
            "route.climb_sought", "pass" if m.ascent_ft_app >= floor else "fail",
            False, f"{m.ascent_ft_app:.0f} ft on a climb-maximizing request "
                   f"(expected at least {floor:.0f} ft for this terrain)",
            m.ascent_ft_app, floor))

    # ---- waypoints ----
    for name in exp.get("via") or []:
        if name not in m.via_distances_m:
            checks.append(Check(f"route.via[{name}]", "na", True,
                                "Required waypoint could not be measured."))
    for name, dist in m.via_distances_m.items():
        lim = exp.get("via_tolerance_m", TOLERANCES["via_m"]["default"])
        checks.append(Check(
            f"route.via[{name}]", "pass" if dist <= lim else "fail", True,
            f"closest approach {dist:.0f} m (limit {lim:.0f} m)", dist, lim))

    # ---- avoid ----
    for name in exp.get("avoid") or []:
        if name not in m.avoid_on_road_m:
            checks.append(Check(f"route.avoid[{name}]", "na", True,
                                "Excluded road geometry could not be verified."))
    for name, on_road in m.avoid_on_road_m.items():
        lim = exp.get("avoid_on_road_m", TOLERANCES["avoid_on_road_m"]["default"])
        # An explicit exclusion is a constraint, not merely a ranking preference.
        checks.append(Check(
            f"route.avoid[{name}]", "pass" if on_road <= lim else "fail", True,
            f"{on_road:.0f} m still ridden on it (limit {lim:.0f} m)",
            on_road, lim))

    # ---- geometry sanity ----
    if m.teleports is not None:
        n = len(m.teleports)
        confirmed = len(m.gaps_confirmed_road or [])
        unknown = len(m.gaps_unknown or [])
        if n:
            worst = max(m.teleports, key=lambda t: t["gap_m"])
            detail = (f"{n} break(s) in the track: "
                      f"{worst['gap_m']:.0f} m with no road along it "
                      f"({worst.get('why', '')})")
        else:
            detail = ("continuous: no breaks. Longest single segment "
                      f"{m.max_point_gap_m:.0f} m"
                      + (f"; {confirmed} long straight segment(s) were "
                         f"checked against the road network and a road runs "
                         f"along each" if confirmed else "")
                      + (f"; {unknown} could not be checked (router "
                         f"unavailable)" if unknown else ""))
        checks.append(Check("geometry.continuous",
                            "fail" if n else "na" if unknown else "pass", True, detail, n, 0))
    if m.repeat_frac is not None:
        # An out-and-back repeats every road BY DESIGN. Decide which it is
        # from the geometry (mirror test), not from what the request asked
        # for -- shape 'both' lets the app return either.
        is_outback = (m.mirror_frac or 0.0) >= 0.6 or exp.get("shape") == "outback"
        if is_outback:
            checks.append(Check(
                "geometry.repeat", "info", False,
                f"{m.repeat_frac:.0%} repeated road, but the track is "
                f"{m.mirror_frac:.0%} its own reverse -- this is an "
                f"out-and-back, where repetition is the shape, not a defect",
                m.repeat_frac, None))
        else:
            lim = TOLERANCES["repeat_frac"]["default"]
            shape_note = (" -- not an out-and-back"
                          if (m.mirror_frac or 0) < 0.3 else "")
            checks.append(Check(
                "geometry.repeat",
                "pass" if m.repeat_frac <= lim else "fail", False,
                f"{m.repeat_frac:.0%} of the route rides the same road twice "
                f"(the app rejects loops above {lim:.0%}); mirror score "
                f"{m.mirror_frac:.0%}{shape_note}",
                m.repeat_frac, lim))
    return checks


# --------------------------------------------------------------------------
# outcome layer
# --------------------------------------------------------------------------

OUTCOME_HELP = {
    "success": "must return at least one usable result",
    "decline": "must refuse, with an explanation the user can act on",
    "either": "either a result or an honest refusal is acceptable; the "
              "measured checks decide",
    "decline_or_honest": "may refuse, or may return a best effort as long as "
                         "it does not claim to have met the impossible part",
    "success_with_caveat": "must return a result AND flag the part of the "
                           "request it cannot honor",
}


def score_outcome(case: dict[str, Any], result: dict[str, Any] | None,
                  parsed: dict[str, Any] | None,
                  error: str | None) -> list[Check]:
    want = case["expect"].get("outcome", "success")
    checks: list[Check] = []
    if error is not None:
        checks.append(Check(
            "outcome.no_crash", "fail", True,
            f"the request raised out of the service layer: {error}"))
        return checks
    checks.append(Check("outcome.no_crash", "pass", True,
                        "handled without an unhandled exception"))
    if result is None:
        checks.append(Check("outcome.returned", "fail", True, "no result object"))
        return checks

    ok = bool(result.get("ok"))
    n = len(result.get("candidates") or [])
    summary = (result.get("summary") or "").strip()
    produced = ok and n > 0

    if want == "success":
        checks.append(Check(
            "outcome.succeeded", "pass" if produced else "fail", True,
            f"ok={result.get('ok')!r}, {n} candidate(s): {summary[:160]}"))
    elif want == "decline":
        checks.append(Check(
            "outcome.declined", "pass" if not produced else "fail", True,
            f"ok={result.get('ok')!r}, {n} candidate(s): {summary[:160]}"))
        checks.append(Check(
            "outcome.explained", "pass" if summary else "fail", True,
            f"user-facing explanation: {summary[:200] or '(none)'}"))
    elif want == "success_with_caveat":
        checks.append(Check(
            "outcome.succeeded", "pass" if produced else "fail", True,
            f"ok={result.get('ok')!r}, {n} candidate(s): {summary[:160]}"))
        notes = ((parsed or {}).get("notes") or "").strip()
        checks.append(Check(
            "outcome.flagged_unsupported", "pass" if notes and notes.lower() in summary.lower() else "fail", True,
            f"unsupported part reported to the user: {notes[:200] or '(nothing said)'}"))
    elif want == "decline_or_honest" and produced:
        disclosed = result.get("ok") == "partial" and bool(result.get("warnings")) and bool(summary)
        checks.append(Check("outcome.honest_best_effort", "pass" if disclosed else "fail", True,
                            f"Best effort must be explicitly partial with user-visible caveats: {summary[:200]}"))
    else:  # either / an explained refusal
        checks.append(Check(
            "outcome.handled", "pass", False,
            f"ok={result.get('ok')!r}, {n} candidate(s): {summary[:160]}"))
        if not produced:
            checks.append(Check(
                "outcome.explained", "pass" if summary else "fail", True,
                f"user-facing explanation: {summary[:200] or '(none)'}"))

    mentions = case["expect"].get("message_mentions")
    if mentions:
        # Graded on what the USER SEES (the summary / banner), not the log.
        # A log line naming the real cause does not help someone staring at
        # a one-line failure message that blames the wrong thing.
        seen = summary.lower()
        hit = [w for w in mentions if w.lower() in seen]
        in_log = [w for w in mentions
                  if w.lower() in (result.get("log") or "").lower()]
        checks.append(Check(
            "outcome.message_names_cause", "pass" if hit else "fail", True,
            f"user-visible summary {'names' if hit else 'does NOT name'} the "
            f"cause (matched {hit or 'nothing'} of {mentions}); the progress "
            f"log matched {in_log or 'nothing'}. Summary was: {summary[:160]!r}"))
    return checks


def verdict_for(checks: Sequence[Check]) -> tuple[Verdict, list[str]]:
    hard_fail = [c.name for c in checks if c.hard and c.status == "fail"]
    soft_fail = [c.name for c in checks if not c.hard and c.status == "fail"]
    if hard_fail:
        return "fail", hard_fail
    unknown = [c.name for c in checks if c.hard and c.status == "na"]
    if unknown:
        return "inconclusive", unknown
    if soft_fail:
        return "partial", soft_fail
    return "pass", []
