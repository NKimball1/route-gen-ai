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

from routes.editing import detour_around
from routes.geocode import geocode_flexible
from routes.gpx_out import write_track
from routes.preview import _parse_gpx, build_preview
from routes.providers import BRouterProvider
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

OUT_DIR = os.path.join("output", "routes")
LATEST = os.path.join(OUT_DIR, "latest.txt")


def current_route() -> str | None:
    if os.path.exists(LATEST):
        path = open(LATEST, encoding="utf-8-sig").read().strip()
        if os.path.exists(path):
            return path
    return None


def run_edit(route_path: str, avoid_place: str, radius_m: float = 1000.0,
             profile: str | None = None) -> str | None:
    """Detour route_path around avoid_place; returns the new GPX path."""
    points = _parse_gpx(route_path)
    if not points:
        print(f"could not read route: {route_path}")
        return None
    zlat, zlon, zname = geocode_flexible(avoid_place)
    print(f"Detouring around: {zname} (r={radius_m:.0f} m)")

    result = detour_around(points, (zlat, zlon, radius_m),
                           BRouterProvider(profile=profile))
    if result is None:
        print("The route never passes through that area — nothing to change.")
        return None

    base = os.path.basename(route_path).rsplit(".", 1)[0]
    base = base.split("_edit")[0]
    n = 1
    while os.path.exists(os.path.join(OUT_DIR, f"{base}_edit{n}.gpx")):
        n += 1
    out_path = os.path.join(OUT_DIR, f"{base}_edit{n}.gpx")

    desc = (f"{result.distance_m / METERS_PER_MILE:.1f} mi, "
            f"{result.ascent_m / METERS_PER_FOOT:.0f} ft "
            f"(edit: around {avoid_place})")
    write_track(result.points, f"{base} edit{n}", desc, out_path)
    build_preview([out_path, route_path], os.path.join(OUT_DIR, "preview.html"))
    with open(LATEST, "w") as f:
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
    ap.add_argument("--avoid", required=True,
                    help='place to route around, "name" or "name:radius_m"')
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    route_path = args.route or current_route()
    if route_path is None:
        print("No current route — compose one first, or pass --route.")
        return 1
    print(f"Editing: {route_path}")

    place, _, radius = args.avoid.rpartition(":")
    if place and radius.replace(".", "").isdigit():
        radius_m = float(radius)
    else:
        place, radius_m = args.avoid, 1000.0

    return 0 if run_edit(route_path, place, radius_m, args.profile) else 1


if __name__ == "__main__":
    sys.exit(main())
