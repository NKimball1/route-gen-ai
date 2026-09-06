"""Shared request handler: plain-English text in, structured results out.

Both the CLI (ask.py) and the web API (api.py) call this — one brain, two
mouths. Captures the pipeline's progress prints as a log and returns the
result candidates with map-drawable geometry.
"""
import io
import os
import sys
import threading

from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

HOME_WORDS = ("home", "my house", "my home", "house")


class _StdoutRouter(io.TextIOBase):
    """Routes print() by THREAD to each job's own log buffer.

    contextlib.redirect_stdout swaps sys.stdout globally — with concurrent
    web jobs, one job's context exit steals or drops another job's output
    (found via a two-user test: user B's log came back empty). This router
    is installed once; each job thread points its writes at its own buffer,
    everything else falls through to the real stdout."""

    def __init__(self, fallback):
        self.fallback = fallback
        self._local = threading.local()

    def set_target(self, target):
        self._local.target = target

    def clear_target(self):
        self._local.target = None

    def _t(self):
        return getattr(self._local, "target", None) or self.fallback

    def write(self, s):
        return self._t().write(s)

    def flush(self):
        f = getattr(self._t(), "flush", None)
        if f:
            f()


if not isinstance(sys.stdout, _StdoutRouter):
    sys.stdout = _StdoutRouter(sys.stdout)


def _downsample(points, max_pts: int = 800):
    step = max(1, len(points) // max_pts)
    out = [[round(p[0], 5), round(p[1], 5)] for p in points[::step]]
    if points and out[-1] != [round(points[-1][0], 5), round(points[-1][1], 5)]:
        out.append([round(points[-1][0], 5), round(points[-1][1], 5)])
    return out


def handle_request(text: str, log_sink=None,
                   default_address: str | None = None,
                   workdir: str | None = None) -> dict:
    """Run a plain-English request end to end. Returns
    {kind, log, candidates: [{label, gpx, latlngs, stats...}]}.
    `log_sink`: optional file-like that receives progress lines live.
    `default_address`: used when the request names no start (a web user's
    configured starting point); falls back to ROUTEGEN_HOME_ADDRESS.
    `workdir`: this session's workspace — GPX output and the current-route
    pointer live here, so concurrent web sessions never share state.
    Defaults to the CLI's shared output/routes."""
    buf = log_sink if log_sink is not None else io.StringIO()
    if workdir is None:
        workdir = os.path.join("output", "routes")
    os.makedirs(workdir, exist_ok=True)

    router = sys.stdout if isinstance(sys.stdout, _StdoutRouter) else None
    route_here = router is not None and buf is not router
    if route_here:
        router.set_target(buf)
    try:
        result = _dispatch(text, default_address, workdir)
    finally:
        if route_here:
            router.clear_target()
    result["log"] = buf.getvalue() if hasattr(buf, "getvalue") else ""
    return result


def _dispatch(text: str, default_address: str | None, workdir: str) -> dict:
    from routes.nl import parse_request
    req = parse_request(text)
    usage = req.pop("_usage")
    print(f"Parsed ({usage['model']}, {usage['input_tokens']}in/"
          f"{usage['output_tokens']}out tokens): "
          f"{ {k: v for k, v in req.items() if v} }")
    if req.get("notes"):
        print(f"Note: couldn't map: {req['notes']}")

    if req["request_type"] == "edit_route":
        from edit_route import current_route, run_edit
        from routes.preview import _parse_desc, _parse_gpx
        route_path = current_route(workdir)
        if route_path is None:
            print("No current route to edit — compose one first.")
            return {"kind": "error", "candidates": []}
        print(f"Editing: {route_path}")
        e = req["edit"]
        out = run_edit(route_path, e["place"], e["radius_m"],
                       mode=e.get("mode", "avoid"), out_dir=workdir)
        if out is None:
            # keep the unchanged route on screen — a failed edit must never
            # leave the user staring at an empty map
            return {"kind": "edit", "candidates": [{
                "label": f"unchanged: {os.path.basename(route_path)} — "
                         f"{_parse_desc(route_path)}",
                "gpx": route_path,
                "latlngs": _downsample(_parse_gpx(route_path)),
            }]}
        return {"kind": "edit", "candidates": [{
            "label": f"{os.path.basename(out)} — {_parse_desc(out)}",
            "gpx": out, "latlngs": _downsample(_parse_gpx(out)),
        }, {
            "label": f"original: {os.path.basename(route_path)}",
            "gpx": route_path,
            "latlngs": _downsample(_parse_gpx(route_path)),
        }]}

    address = req.get("address")
    if not address or address.strip().lower() in HOME_WORDS:
        address = default_address or os.environ.get("ROUTEGEN_HOME_ADDRESS")
    if not address:
        print("No start address — set your starting point (or include an "
              "address in the request).")
        return {"kind": "error", "candidates": []}

    if req["request_type"] == "interval_spot":
        from find_spot import run_spot_search
        from routes.intervals import IntervalSpec
        iv = req["interval"]
        spec = IntervalSpec(address, iv["reps"], iv["rep_minutes"], iv["kind"],
                            iv["max_travel_minutes"])
        spots = run_spot_search(spec, out_dir=workdir)
        cands = []
        for i, s in enumerate(spots, 1):
            path = os.path.join(
                workdir,
                f"spot_{spec.kind}_{spec.reps}x{spec.rep_minutes:.0f}_{i}.gpx")
            cands.append({
                "label": (f"#{i}: {s.length_mi:.1f} mi @ {s.mean_grade_pct:+.1f}%"
                          f", {s.n_controls} stops, "
                          f"{s.dist_from_start_m / METERS_PER_MILE:.1f} mi out"),
                "gpx": path, "latlngs": _downsample(s.points),
            })
        return {"kind": "interval_spot", "candidates": cands}

    from compose_route import parse_avoid
    from routes.geocode import geocode
    from routes.pipeline import build_providers, compose
    from routes.spec import RouteSpec
    r = req["route"]
    avoid = parse_avoid(r["avoid_places"])
    via, via_names = [], []
    for place in r["via_places"]:
        vlat, vlon, vname = geocode(place)
        print(f"Via: {vname}")
        via.append((vlat, vlon))
        via_names.append(place)
    if via:
        r["shape"] = "loop"
    shapes = ["loop", "outback"] if r["shape"] == "both" else [r["shape"]]
    specs = [RouteSpec.from_imperial(address, r["distance_miles"],
                                     r["max_climb_ft"], r["maximize_climb"],
                                     shape=s, avoid=avoid,
                                     minimize_climb=r["minimize_climb"],
                                     via=via, via_names=via_names)
             for s in shapes]
    keepers = compose(specs, build_providers(), out_dir=workdir)
    miles = specs[0].distance_m / METERS_PER_MILE
    goal = ("maxclimb" if specs[0].maximize_ascent
            else "minclimb" if specs[0].minimize_ascent else "ride")
    cands = []
    for i, c in enumerate(keepers, 1):
        path = os.path.join(workdir,
                            f"route_{miles:.0f}mi_{goal}_{i}_{c.shape}_{c.provider}.gpx")
        major = "" if c.major_m < 50 else f", {c.major_m / 1609.344:.1f} mi major"
        cands.append({
            "label": (f"#{i} {c.shape}: {c.distance_mi:.1f} mi, "
                      f"{c.ascent_m / METERS_PER_FOOT:.0f} ft"
                      f", {c.overlap_frac:.0%} repeat{major}"),
            "gpx": path, "latlngs": _downsample(c.points),
        })
    return {"kind": "route", "candidates": cands}
