"""Edit an existing route: detour around a place, keep the rest.

  python edit_route.py --avoid "Pheasant Branch Conservancy, Middleton WI:1200"
  python edit_route.py --route output\\routes\\route_50mi_minclimb_1_loop_brouter.gpx --avoid "..."

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

from routes.editing import (connect_from, detour_around, extend_route,
                            move_endpoint, route_via, shorten_route)
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


def run_edit(route_path: str, place: str | None = None,
             radius_m: float = 1000.0, mode: str = "avoid",
             profile: str | None = None, out_dir: str = OUT_DIR,
             miles_delta: float | None = None,
             connect_return: bool = False) -> str | None:
    """Edit route_path. Modes: avoid, via, extend, shorten, move_start,
    move_end, connect. Returns the new GPX path (None = unchanged)."""
    points = _parse_gpx(route_path)
    if not points:
        print(f"could not read route: {route_path}")
        return None
    provider = BRouterProvider(profile=profile)

    if mode in ("extend", "shorten"):
        if not miles_delta or miles_delta <= 0:
            print("How much longer/shorter? Give a number of miles.")
            return None
        meters = miles_delta * METERS_PER_MILE
        if mode == "extend":
            print(f"Extending by ~{miles_delta:.0f} mi")
            result = extend_route(points, meters, provider)
        else:
            print(f"Shortening by ~{miles_delta:.0f} mi")
            result = shorten_route(points, meters, provider)
        if result is None:
            print("Couldn't find a good way to do that — route unchanged.")
            return None
        place = f"{mode} {miles_delta:.0f}mi"
    else:
        if not place:
            print("That edit needs a place/address.")
            return None
        # bias place lookup to the route's own neighborhood (~15 km margin)
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        near = (min(lats) - 0.15, min(lons) - 0.2,
                max(lats) + 0.15, max(lons) + 0.2)
        zlat, zlon, zname = geocode_flexible(place, near=near)
        if mode == "via":
            print(f"Routing through: {zname}")
            result = route_via(points, (zlat, zlon),
                               provider, buffer_m=max(radius_m, 1200.0))
        elif mode in ("move_start", "move_end"):
            where = "start" if mode == "move_start" else "end"
            print(f"Moving the {where} to: {zname}")
            result = move_endpoint(points, (zlat, zlon), provider, at=where)
        elif mode == "connect":
            print(f"Connecting from: {zname}"
                  + (" (and back at the end)" if connect_return else ""))
            result = connect_from(points, (zlat, zlon), provider,
                                  with_return=connect_return)
        else:
            print(f"Detouring around: {zname} (r={radius_m:.0f} m)")
            result = detour_around(points, (zlat, zlon, radius_m), provider)
            if result is None:
                print("The route never passes through that area — nothing "
                      "to change.")
        if result is None:
            return None

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.basename(route_path).rsplit(".", 1)[0]
    base = base.split("_edit")[0]
    n = 1
    while os.path.exists(os.path.join(out_dir, f"{base}_edit{n}.gpx")):
        n += 1
    out_path = os.path.join(out_dir, f"{base}_edit{n}.gpx")

    verbs = {"via": "via", "avoid": "around", "extend": "",
             "shorten": "", "move_start": "start at",
             "move_end": "end at", "connect": "connect"}
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
    return out_path


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
    return 0 if run_edit(route_path, place, radius_m, mode,
                         args.profile) else 1


if __name__ == "__main__":
    sys.exit(main())
