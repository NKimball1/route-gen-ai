"""Edit an existing route: detour around a place, keep the rest.

  python edit_route.py --avoid "Pheasant Branch Conservancy, Middleton WI:1200"
  python edit_route.py --via "Colectivo Coffee, Monroe St Madison"
  python edit_route.py --route path\\to\\some.gpx --avoid "..."

Without --route, edits the current route (the last composed or edited
winner, tracked in output/routes/latest.txt). The edited route becomes the
new current route, so edits chain. Radius defaults to 1000 m; append
":<meters>" to the place to widen/narrow the zone.
"""
import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from routes.editing import (anchor_at, connect_from, detour_around,
                            extend_route, move_endpoint, route_via,
                            route_via_chain, shorten_route)
from routes.geocode import geocode_flexible
from routes.gpx_out import write_track
from routes.preview import _parse_gpx, build_preview
from routes.providers import BRouterProvider
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

OUT_DIR = os.path.join("output", "routes")


def current_route(workdir: str = OUT_DIR) -> str | None:
    """The session's current route (per-workdir, so web sessions don't
    share state — see routes/service.py)."""
    latest = os.path.join(workdir, "latest.txt")
    if os.path.exists(latest):
        path = open(latest, encoding="utf-8-sig").read().strip()
        if os.path.exists(path):
            return path
    return None


def predecessor(route_path: str) -> str | None:
    """The version this edit was built on: editN -> editN-1 -> the base
    file. Edit files persist in the workdir, so undo is just a pointer
    move."""
    base, ext = os.path.splitext(route_path)
    if "_edit" not in base:
        return None
    stem, n = base.rsplit("_edit", 1)
    if not n.isdigit():
        return None
    prev = stem + ext if int(n) <= 1 else f"{stem}_edit{int(n) - 1}{ext}"
    return prev if os.path.exists(prev) else None


def run_edit(route_path: str, place: str | None = None,
             radius_m: float = 1000.0, mode: str = "avoid",
             profile: str | None = None, out_dir: str = OUT_DIR,
             miles_delta: float | None = None,
             connect_return: bool = False,
             places: list[str] | None = None):
    """Edit route_path. Modes: avoid, via, extend, shorten, move_start,
    move_end, anchor, connect. Returns (new GPX path or None, a
    human-readable outcome message, ok: True | "partial" | False).
    Avoid edits VERIFY the outcome: the final route is measured against
    the zone rather than trusting that every section rerouted."""
    points = _parse_gpx(route_path)
    if not points:
        return None, f"Couldn't read the route file ({route_path}).", False
    provider = BRouterProvider(profile=profile)

    # bias place lookups to the route's own neighborhood (~15 km margin)
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    near = (min(lats) - 0.15, min(lons) - 0.2,
            max(lats) + 0.15, max(lons) + 0.2)

    skipped: list[str] = []
    if mode in ("extend", "shorten"):
        if not miles_delta or miles_delta <= 0:
            return None, "How much longer/shorter? Give a number of miles.", False
        meters = miles_delta * METERS_PER_MILE
        if mode == "extend":
            print(f"Extending by ~{miles_delta:.0f} mi")
            result = extend_route(points, meters, provider)
        else:
            print(f"Shortening by ~{miles_delta:.0f} mi")
            result = shorten_route(points, meters, provider)
        if result is None:
            return None, (f"Couldn't find a good way to {mode} this route "
                          "by that much — it's unchanged."), False
        place = f"{mode} {miles_delta:.0f}mi"
    elif mode == "via" and places and len(places) > 1:
        targets, names = [], []
        for p in places:
            try:
                zlat, zlon, zname = geocode_flexible(p, near=near)
                print(f"Waypoint: {zname}")
                targets.append((zlat, zlon))
                names.append(p)
            except ValueError:
                skipped.append(p)
        if not targets:
            return None, ("Couldn't locate any of those places near the "
                          f"route ({', '.join(places)}) — try road names "
                          "plus the city, or landmarks."), False
        result = route_via_chain(points, targets, provider,
                                 buffer_m=max(radius_m, 1200.0))
        if result is None:
            return None, ("Couldn't route through those places — the "
                          "route is unchanged."), False
        place = " + ".join(names)
    else:
        if not place:
            return None, "That edit needs a place or address.", False
        try:
            zlat, zlon, zname = geocode_flexible(place, near=near)
        except ValueError as e:
            return None, (f"Couldn't find {place!r} near your route "
                          f"({e}). The route is unchanged."), False
        if mode == "via":
            print(f"Routing through: {zname}")
            result = route_via(points, (zlat, zlon),
                               provider, buffer_m=max(radius_m, 1200.0))
            if result is None:
                return None, (f"Couldn't splice the route through "
                              f"{place!r} — it's unchanged."), False
        elif mode in ("move_start", "move_end"):
            where = "start" if mode == "move_start" else "end"
            print(f"Moving the {where} to: {zname}")
            result = move_endpoint(points, (zlat, zlon), provider, at=where)
            if result is None:
                return None, (f"Couldn't move the {where} there — the "
                              "route is unchanged."), False
        elif mode == "anchor":
            print(f"Making it a round trip from: {zname}")
            result = anchor_at(points, (zlat, zlon), provider)
            if result is None:
                return None, ("The ride already starts and ends there — "
                              "nothing to change."), False
        elif mode == "connect":
            print(f"Connecting from: {zname}"
                  + (" (and back at the end)" if connect_return else ""))
            result = connect_from(points, (zlat, zlon), provider,
                                  with_return=connect_return)
            if result is None:
                return None, ("Couldn't route from there to the ride — "
                              "the route is unchanged."), False
        else:
            print(f"Detouring around: {zname} (r={radius_m:.0f} m)")
            result = detour_around(points, (zlat, zlon, radius_m), provider)
            if result is not None and result.detours == 0                     and result.failed_detours > 0:
                return None, (f"Couldn't avoid {place!r}: "
                              f"{result.fail_reason}. The route is "
                              "unchanged."), False
            if result is None:
                return None, (f"The route never passes through {place!r} — "
                              "nothing to change."), False

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(route_path).rsplit(".", 1)[0]
    base = base.split("_edit")[0]
    n = 1
    while os.path.exists(os.path.join(out_dir, f"{base}_edit{n}.gpx")):
        n += 1
    out_path = os.path.join(out_dir, f"{base}_edit{n}.gpx")

    verbs = {"via": "via", "avoid": "around", "extend": "",
             "shorten": "", "move_start": "start at",
             "move_end": "end at", "connect": "connect",
             "anchor": "round trip from"}
    desc = (f"{result.distance_m / METERS_PER_MILE:.1f} mi, "
            f"{result.ascent_m / METERS_PER_FOOT:.0f} ft "
            f"(edit: {verbs.get(mode, mode)} {place})".replace(":  ", ": "))
    write_track(result.points, f"{base} edit{n}", desc, out_path)
    build_preview([out_path, route_path], os.path.join(out_dir, "preview.html"))
    with open(os.path.join(out_dir, "latest.txt"), "w") as f:
        f.write(out_path)

    delta = result.added_m - result.removed_m
    print(f"Replaced {result.removed_m / METERS_PER_MILE:.1f} mi with "
          f"{result.added_m / METERS_PER_MILE:.1f} mi across "
          f"{result.detours} detour(s) ({delta / METERS_PER_MILE:+.1f} mi)")
    print(f"Edited route: {result.distance_m / METERS_PER_MILE:.1f} mi, "
          f"{result.ascent_m / METERS_PER_FOOT:.0f} ft, "
          f"{result.overlap_frac:.0%} repeat -> {out_path}")
    message = (f"Done — {verbs.get(mode, mode)} {place}: now "
               f"{result.distance_m / METERS_PER_MILE:.1f} mi "
               f"({delta / METERS_PER_MILE:+.1f} mi).")
    ok = True
    if skipped:
        message += f" Couldn't locate and skipped: {', '.join(skipped)}."
        ok = "partial"
    if getattr(result, "failed_detours", 0):
        message = (f"Partly done — rerouted {result.detours} section(s) "
                   f"around {place}, but {result.failed_detours} couldn't "
                   f"be ({result.fail_reason}). "
                   f"Now {result.distance_m / METERS_PER_MILE:.1f} mi.")
        ok = "partial"
    if mode == "avoid":
        # verify the OUTCOME: does the final route actually clear the zone?
        from routes.editing import _dist_m
        min_d = min(_dist_m(p, (zlat, zlon)) for p in result.points)
        if min_d >= radius_m:
            if ok is True:
                message += " Verified clear of the area."
        elif ok is True:
            message = (f"Partly done — the route was rerouted but still "
                       f"passes within {min_d:.0f} m of {place}. "
                       f"Now {result.distance_m / METERS_PER_MILE:.1f} mi.")
            ok = "partial"
    return out_path, message, ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--route", default=None,
                    help="GPX to edit (default: the current route)")
    ap.add_argument("--avoid", default=None,
                    help='place to route around, "name" or "name:radius_m"')
    ap.add_argument("--via", default=None,
                    help="place to route through instead")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    if bool(args.avoid) == bool(args.via):
        print("Pass exactly one of --avoid or --via.")
        return 1

    route_path = args.route or current_route()
    if route_path is None:
        print("No current route — compose one first, or pass --route.")
        return 1
    print(f"Editing: {route_path}")

    raw = args.avoid or args.via
    place, _, radius = raw.rpartition(":")
    if place and radius.replace(".", "").isdigit():
        radius_m = float(radius)
    else:
        place, radius_m = raw, 1000.0

    mode = "via" if args.via else "avoid"
    out, message, _ok = run_edit(route_path, place, radius_m, mode,
                                 args.profile)
    print(message)
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
