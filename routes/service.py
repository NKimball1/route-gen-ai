"""Shared request handler: plain-English text in, structured results out.

Both the CLI (ask.py) and the web API (api.py) call this — one brain, two
mouths. Captures the pipeline's progress prints as a log and returns the
result candidates with map-drawable geometry.
"""
import io
import math
import os
import sys
import threading
from typing import IO, Any, Sequence, TypedDict, NotRequired
from pydantic import ValidationError
from routes.execution import Cancellation, execution_scope
from routes import storage
from routes.policy import PARSE_BOUNDS, MAX_PLACES
from routes.requests_model import ParsedRequest

from routes.spec import METERS_PER_MILE, Coord, LatLon, Outcome


class CandidateOut(TypedDict):
    """One drawable result row."""
    label: str
    gpx: str                     # path, downloadable via /api/gpx
    latlngs: list[list[float]]   # downsampled [lat, lon] pairs for the map
    metrics: NotRequired[dict[str, Any]]
    warnings: NotRequired[list[str]]


class ServiceResult(TypedDict, total=False):
    """What every request returns — the contract the frontend reads."""
    kind: str                    # route | interval_spot | edit | upload | error
    ok: Outcome
    summary: str
    candidates: list[CandidateOut]
    log: str
    warnings: list[str]


HOME_WORDS: tuple[str, ...] = ("home", "my house", "my home", "house")


class LookupFailed(Exception):
    """A place could not be found, or the lookup service could not be
    reached. Carries the sentence the user should see."""


def _locate(place: str, what: str) -> tuple[float, float, str]:
    """Geocode, converting both failure modes into something sayable."""
    from routes.geocode import geocode
    try:
        return geocode(place)
    except Exception as e:
        raise _lookup_failed(e, what, place) from e


def _lookup_failed(e: BaseException, what: str = "the place",
                   place: str | None = None) -> "LookupFailed":
    """One place decides the words for every lookup failure, wherever it
    happened. The start address is geocoded deep inside the pipeline, not
    here -- which is exactly how the first version of this fix missed it."""
    import requests

    from routes.geocode import GeocodeNotFound, GeocodeUnavailable
    named = f" {place!r}" if place else ""
    if isinstance(e, (GeocodeUnavailable, requests.ConnectionError,
                      requests.Timeout, requests.RequestException)):
        return LookupFailed(
            "Couldn't reach the address lookup service (OpenStreetMap "
            "Nominatim), so the request never got as far as routing. "
            "Nothing is wrong with what you asked for — try again in a "
            f"moment. ({type(e).__name__})")
    if isinstance(e, GeocodeNotFound):
        return LookupFailed(
            f"Couldn't find {what}{named} on the map. Try a road name plus "
            f"the city, or a landmark that appears in OpenStreetMap.")
    raise e


# ---- server-side bounds on parsed numbers ----
# The parser is an LLM fed user text: schema-VALID output can still carry
# absurd values ("radius of a million meters"), and on a public deploy the
# text box is attacker-controlled. A rogue number must not become a giant
# Overpass bbox or an hours-long compute. Out-of-range values are pulled
# to the nearest edge, with a log line so the user sees it happened.
def _clamp_parsed(req: dict[str, Any]) -> list[str]:
    """Bound parsed resource use and return disclosures for the result."""
    warnings: list[str] = []
    for section, bounds in PARSE_BOUNDS.items():
        obj = req.get(section)
        if obj is None:
            continue
        if not isinstance(obj, dict):
            raise ValueError("The interpretation has an invalid request section. Please rephrase.")
        for fld, (lo, hi) in bounds.items():
            v = obj.get(fld)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"Invalid value for {fld}; please rephrase the request.")
            c = min(max(v, lo), hi)
            if c != v:
                print(f"Clamped {fld}: {v:g} -> {c:g}")
                warnings.append(f"{fld.replace('_', ' ')} was limited from {v:g} to {c:g}.")
                obj[fld] = c
        for fld in ("places", "avoid_places", "via_places"):
            v = obj.get(fld)
            if isinstance(v, list) and len(v) > MAX_PLACES:
                print(f"Keeping the first {MAX_PLACES} of "
                      f"{len(v)} {fld}")
                obj[fld] = v[:MAX_PLACES]
                warnings.append(f"Only the first {MAX_PLACES} of {len(v)} {fld.replace('_', ' ')} were applied.")
    return warnings


class _StdoutRouter(io.TextIOBase):
    """Routes print() by THREAD to each job's own log buffer.

    contextlib.redirect_stdout swaps sys.stdout globally — with concurrent
    web jobs, one job's context exit steals or drops another job's output
    (found via a two-user test: user B's log came back empty). This router
    is installed once; each job thread points its writes at its own buffer,
    everything else falls through to the real stdout."""

    def __init__(self, fallback: IO[str]) -> None:
        self.fallback: IO[str] = fallback
        self._local = threading.local()

    def set_target(self, target: IO[str]) -> None:
        self._local.target = target

    def clear_target(self) -> None:
        self._local.target = None

    def _t(self) -> IO[str]:
        return getattr(self._local, "target", None) or self.fallback

    def write(self, s: str) -> int:
        return self._t().write(s)

    def flush(self) -> None:
        f = getattr(self._t(), "flush", None)
        if f:
            f()


if not isinstance(sys.stdout, _StdoutRouter):
    sys.stdout = _StdoutRouter(sys.stdout)


# Rep length is derived from an ASSUMED fixed pace (20 mph flat, 11 mph into
# a grade), so it is already a rough number. A stretch within this much of a
# full rep is one lap: telling a rider to turn around because they are 0.1%
# short would be precision the input never had.


def _laps_for_rep(stretch_m: float, rep_m: float) -> int:
    from routes.spot_service import laps_for_rep
    return laps_for_rep(stretch_m, rep_m)


def _downsample(points: Sequence[Coord],
                max_pts: int = 800) -> list[list[float]]:
    step = max(1, math.ceil(len(points) / max_pts))
    out = [[round(p[0], 5), round(p[1], 5)] for p in points[::step]]
    if points and out[-1] != [round(points[-1][0], 5), round(points[-1][1], 5)]:
        out.append([round(points[-1][0], 5), round(points[-1][1], 5)])
    return out


def handle_request(text: str, log_sink: IO[str] | None = None,
                   default_address: str | None = None,
                   workdir: str | None = None,
                   cancellation: Cancellation | None = None,
                   intent: str = "auto") -> ServiceResult:
    """Run a plain-English request end to end. Returns
    {kind, log, candidates: [{label, gpx, latlngs, stats...}]}.
    `log_sink`: optional file-like that receives progress lines live.
    `default_address`: used when the request names no start (a web user's
    configured starting point); falls back to ROUTEGEN_HOME_ADDRESS.
    `workdir`: this session's workspace — GPX output and the current-route
    pointer live here, so concurrent web sessions never share state.
    Defaults to the CLI's shared output/routes."""
    buf: IO[str] = log_sink if log_sink is not None else io.StringIO()
    if workdir is None:
        workdir = os.path.join("output", "routes")
    os.makedirs(workdir, exist_ok=True)

    # the CLI passes sys.stdout itself as the sink — nothing to re-route
    out = sys.stdout
    router: _StdoutRouter | None = (
        out if isinstance(out, _StdoutRouter) and buf is not out else None)
    if router is not None:
        router.set_target(buf)
    try:
        with execution_scope(cancellation), storage.transaction(workdir, cancellation=cancellation):
            result = _dispatch(text, default_address, workdir, intent)
    except Exception as e:  # noqa: BLE001 - re-raised unless it is a lookup
        from routes.geocode import GeocodeError
        if isinstance(e, (ValidationError, ValueError)):
            e = LookupFailed("Could not safely interpret or apply this request. " + str(e).split("\n")[0])
        elif not isinstance(e, (LookupFailed, GeocodeError)):
            raise
        else:
            e = e if isinstance(e, LookupFailed) else _lookup_failed(e)
        # An address that cannot be found, or a lookup service that cannot be
        # reached, is an ordinary outcome of a request -- not a crash. Before
        # this boundary existed it escaped as a raw requests.ConnectionError
        # or ValueError: a traceback on the CLI, and in the web app a banner
        # reading "ERROR: ConnectionError: HTTPConnectionPool(host=...)".
        print(str(e))
        result = {"kind": "error", "candidates": [], "ok": False,
                  "summary": str(e)}
    finally:
        if router is not None:
            router.clear_target()
    result["log"] = buf.getvalue() if isinstance(buf, io.StringIO) else ""
    return result


# A dead router used to be discovered one timing-out leg at a time: with a
# black-holed host that is 120 s per leg across a dozen legs before the user
# is told anything. One TCP connect answers the same question in a second.
ROUTER_DOWN_MSG: str = (
    "The routing server isn't responding, so no route could be generated. "
    "Nothing is wrong with your request — this is the router (BRouter) "
    "being down, and it will work again once the server is back.")


def _router_preflight() -> str | None:
    """Return the message to show if the routing backend is unreachable."""
    from routes.providers import BRouterProvider, brouter_reachable
    url = BRouterProvider().base_url
    if brouter_reachable(url):
        return None
    print(f"Preflight: routing server unreachable at {url}")
    return ROUTER_DOWN_MSG


def _route_row(path: str, label: str | None = None) -> CandidateOut:
    from routes.preview import _parse_desc, _parse_gpx
    return {"label": label or f"{os.path.basename(path)} - {_parse_desc(path) or ''}",
            "gpx": path, "latlngs": _downsample(_parse_gpx(path))}


def undo_request(workdir: str) -> ServiceResult:
    previous = storage.undo(workdir)
    if previous is None:
        return {"kind": "edit", "ok": False, "summary": "Nothing to undo - this is the earliest version.", "candidates": []}
    return {"kind": "edit", "ok": True, "summary": f"Undone - back to {os.path.basename(previous)}.",
            "candidates": [_route_row(previous)]}


def _home(place: str | None, address: str | None) -> str | None:
    if place and place.strip().lower() in HOME_WORDS:
        if not address:
            raise ValueError("Set a starting address to use 'home' in this request.")
        return address
    return place


def _dispatch(text: str, default_address: str | None,
              workdir: str, intent: str = "auto") -> ServiceResult:
    from routes.nl import parse_request
    from routes.preview import _parse_gpx
    from routes.editing import _cum
    current = storage.current_route(workdir)
    home = default_address or os.environ.get("ROUTEGEN_HOME_ADDRESS")
    context = {"has_current_route": current is not None, "start_address": home,
               "operation": intent,
               "current_distance_miles": _cum(_parse_gpx(current))[-1] / METERS_PER_MILE if current else None}
    req = parse_request(text, context=context)
    usage = req.pop("_usage", {})
    print(f"Parsed ({usage.get('model', 'unknown')}): {req}")
    adjustments = _clamp_parsed(req)
    req = ParsedRequest.model_validate(req).model_dump()
    kind = req["request_type"]
    if kind == "clarify":
        return {"kind": "clarify", "ok": False, "summary": req["notes"] or "Please specify the ride length and whether to create or edit a route.", "candidates": []}
    if intent != "auto" and kind not in (intent, "undo"):
        return {"kind": "error", "ok": False, "summary": "The request did not match the selected operation. Please rephrase or change the operation.", "candidates": []}
    if kind == "undo":
        return undo_request(workdir)
    if kind == "edit_route":
        result = _edit_request(req["edit"], current, home, workdir)
    else:
        address = _home(req.get("address"), home) or home
        if not address:
            return {"kind": "error", "ok": False, "summary": "No start address - set your starting point or include an address in the request.", "candidates": []}
        if kind == "interval_spot":
            result = _spot_request(req["interval"], address, workdir)
        else:
            result = _ride_request(req["route"], address, home, workdir)
    if req["notes"]:
        adjustments.append("Not fulfilled: " + req["notes"])
    for warning in adjustments:
        result.setdefault("warnings", []).append(warning)
        result["summary"] = result.get("summary", "") + " " + warning
        if result.get("ok") is True:
            result["ok"] = "partial"
    return result


def _edit_request(edit: dict[str, Any], current: str | None,
                  home: str | None, workdir: str) -> ServiceResult:
    from routes.edit_service import run_edit
    from routes.editing import _cum
    from routes.preview import _parse_gpx
    if current is None:
        return {"kind": "error", "ok": False, "summary": "No current route to edit - compose or upload one first.", "candidates": []}
    source = current
    if edit["revert_first"] and storage.last_edit_changed(workdir):
        parent = storage.predecessor(current)
        if parent:
            print("Reverting the last change first")
            source = parent
    place = _home(edit["place"], home)
    places = [_home(p, home) or p for p in edit["places"]] if edit["places"] else None
    mode, delta = edit["mode"], edit["miles_delta"]
    if mode in ("extend", "shorten") and not delta and edit["target_miles"]:
        distance = _cum(_parse_gpx(source))[-1] / METERS_PER_MILE
        difference = edit["target_miles"] - distance
        mode, delta = ("extend" if difference > 0 else "shorten"), abs(difference)
    out, message, ok = run_edit(source, place, edit["radius_m"], mode=mode,
                                out_dir=workdir, miles_delta=delta,
                                connect_return=edit["connect_return"], places=places)
    storage.note_outcome(workdir, out is not None)
    if out is None:
        # A failed correction is transactional: neither the selection nor
        # the displayed geometry moves to the proposed predecessor.
        return {"kind": "edit", "ok": False, "summary": message,
                "candidates": [_route_row(current, f"unchanged: {os.path.basename(current)}")]}
    return {"kind": "edit", "ok": ok, "summary": message,
            "warnings": [message] if ok == "partial" else [],
            "candidates": [_route_row(out), _route_row(source, "original route")]}


def _spot_request(iv: dict[str, Any], address: str, workdir: str) -> ServiceResult:
    from routes.intervals import IntervalSpec
    from routes.spot_service import run_spot_search, spot_label, spot_warnings, spot_metrics
    from routes.power import DEFAULT_TOTAL_KG
    down = _router_preflight()
    if down:
        return {"kind": "error", "ok": False, "summary": down, "candidates": []}
    spec = IntervalSpec(address, iv["reps"], iv["rep_minutes"], iv["kind"],
                        iv["max_travel_minutes"], watts=iv["watts"],
                        total_kg=iv["total_kg"] or DEFAULT_TOTAL_KG,
                        max_stops=iv["max_stops"])
    spots = run_spot_search(spec, out_dir=workdir)
    candidates: list[CandidateOut] = []
    for i, spot in enumerate(spots, 1):
        candidates.append({"label": f"#{i}: {spot_label(spec, spot)}",
                           "gpx": spot.gpx_path or "", "latlngs": _downsample(spot.points),
                           "metrics": spot_metrics(spec, spot), "warnings": spot_warnings(spec, spot)})
    warnings = list(dict.fromkeys(w for c in candidates for w in c.get("warnings", [])))
    if candidates:
        summary = (f"Found {len(candidates)} interval spot(s) - best: {candidates[0]['label']}. "
                   f"A {spec.rep_minutes:g}-minute {spec.kind} rep needs about {spec.rep_distance_m / METERS_PER_MILE:.1f} mi of road.")
    else:
        summary = "No verified suitable stretch found. Try a larger travel budget; a hard stop limit also requires traffic-control data."
    return {"kind": "interval_spot", "ok": ("partial" if warnings else True) if candidates else False,
            "summary": summary, "warnings": warnings, "candidates": candidates}


def _ride_request(r: dict[str, Any], address: str, home: str | None,
                  workdir: str) -> ServiceResult:
    from compose_route import parse_avoid
    from routes.geocode import geocode_flexible
    from routes.pipeline import build_providers, compose
    from routes.spec import RouteSpec
    from routes.edit_service import NEAR_MARGIN_LAT_DEG, NEAR_MARGIN_LON_DEG
    # Resolve relative place names against the actual start, not an invented
    # city from an LLM that has never seen the user's configured address.
    lat, lon, _ = _locate(address, "the start")
    near = (lat - NEAR_MARGIN_LAT_DEG, lon - NEAR_MARGIN_LON_DEG,
            lat + NEAR_MARGIN_LAT_DEG, lon + NEAR_MARGIN_LON_DEG)
    avoid = parse_avoid([_home(p, home) or p for p in r["avoid_places"]])
    via: list[LatLon] = []
    for place in r["via_places"]:
        resolved = _home(place, home) or place
        try:
            vlat, vlon, _ = geocode_flexible(resolved, near=near)
        except Exception as error:
            raise _lookup_failed(error, "the waypoint", place) from error
        via.append((vlat, vlon))
    down = _router_preflight()
    if down:
        return {"kind": "error", "ok": False, "summary": down, "candidates": []}
    if via:
        r["shape"] = "loop"  # via places imply a loop
    shapes = ["loop", "outback"] if r["shape"] == "both" else [r["shape"]]
    specs = [RouteSpec.from_imperial(address, r["distance_miles"], r["max_climb_ft"],
                                    r["maximize_climb"], shape=shape, avoid=avoid,
                                    minimize_climb=r["minimize_climb"], via=via,
                                    via_names=r["via_places"]) for shape in shapes]
    providers = build_providers("brouter")
    keepers = compose(specs, providers, out_dir=workdir)
    candidates: list[CandidateOut] = []
    for i, candidate in enumerate(keepers, 1):
        road = "major roads unknown" if candidate.major_m is None else f"{candidate.major_m / METERS_PER_MILE:.1f} mi major"
        repeat = "out-and-back" if candidate.shape == "outback" else f"{candidate.overlap_frac:.0%} repeat"
        candidates.append({"label": f"#{i} {candidate.shape}: {candidate.distance_mi:.1f} mi, {candidate.ascent_ft:.0f} ft, {repeat}, {road}",
                           "gpx": candidate.gpx_path or "", "latlngs": _downsample(candidate.points),
                           "warnings": candidate.warnings,
                           "metrics": {"distance_m": candidate.distance_m, "ascent_m": candidate.ascent_m,
                                       "major_m": candidate.major_m, "overlap_frac": candidate.overlap_frac}})
    warnings = list(dict.fromkeys(w for c in candidates for w in c.get("warnings", [])))
    summary = f"{len(candidates)} route(s) - best: {candidates[0]['label']}" if candidates else (
        ROUTER_DOWN_MSG if any(getattr(p, "unreachable", False) for p in providers) else
        "No route met the constraints - try a looser target or different distance.")
    return {"kind": "route", "ok": ("partial" if warnings else True) if candidates else False,
            "summary": summary, "warnings": warnings, "candidates": candidates}
