"""Route composer prototype: turn a ride request into Garmin-ready GPX files.

Examples (the two target prompts):

  # ~30 mile loop, under 1000 ft of climbing
  python compose_route.py --address "123 Main St, Madison WI" --miles 30 --max-climb-ft 1000

  # 50 miles, as much climbing as possible
  python compose_route.py --address "Boulder, CO" --miles 50 --maximize-climb

Providers: brouter (no key needed) always runs; ors runs if ORS_API_KEY is set
(free key from https://openrouteservice.org). Use --provider to force one.
GPX files land in output/routes/.
"""
import argparse
import os
import sys

from routes.geocode import geocode
from routes.gpx_out import write_gpx
from routes.preview import build_preview
from routes.providers import BRouterProvider, ORSProvider
from routes.scoring import rank
from routes.spec import RouteSpec

OUT_DIR = os.path.join("output", "routes")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", required=True, help="start/end address")
    ap.add_argument("--miles", type=float, required=True, help="target distance")
    ap.add_argument("--max-climb-ft", type=float, default=None,
                    help="reject candidates climbing more than this")
    ap.add_argument("--maximize-climb", action="store_true",
                    help="rank candidates by most climbing")
    ap.add_argument("--candidates", type=int, default=6,
                    help="loop candidates per provider (default 6)")
    ap.add_argument("--provider", choices=["brouter", "ors", "all"], default="all")
    args = ap.parse_args()

    spec = RouteSpec.from_imperial(args.address, args.miles,
                                   args.max_climb_ft, args.maximize_climb)

    lat, lon, place = geocode(args.address)
    print(f"Start: {place} ({lat:.5f}, {lon:.5f})")

    providers = []
    if args.provider in ("brouter", "all"):
        providers.append(BRouterProvider())
    if args.provider in ("ors", "all"):
        ors = ORSProvider()
        if ors.available:
            providers.append(ors)
        elif args.provider == "ors":
            print("ORS_API_KEY not set — get a free key at https://openrouteservice.org")
            return 1
        else:
            print("(skipping ors: ORS_API_KEY not set)")

    candidates = []
    for p in providers:
        print(f"Generating {args.candidates} candidates via {p.name}...")
        candidates.extend(p.candidates(spec, lat, lon, n=args.candidates))

    keepers, rejects = rank(spec, candidates)
    for c, reason in rejects:
        print(f"  reject [{c.provider} {c.seed}]: {reason}")
    if not keepers:
        print("No candidate met the constraints. Try more --candidates or a "
              "looser target.")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    goal = "maxclimb" if spec.maximize_ascent else "loop"
    gpx_paths = []
    print(f"\n{'rank':<5}{'provider':<9}{'miles':>7}{'climb ft':>10}  file")
    for i, c in enumerate(keepers, 1):
        fname = f"route_{args.miles:.0f}mi_{goal}_{i}_{c.provider}.gpx"
        path = os.path.join(OUT_DIR, fname)
        write_gpx(c, f"{args.miles:.0f}mi {goal} #{i} ({c.provider}, {c.seed})", path)
        gpx_paths.append(path)
        print(f"{i:<5}{c.provider:<9}{c.distance_mi:>7.1f}{c.ascent_ft:>10.0f}  {path}")

    build_preview(gpx_paths, os.path.join(OUT_DIR, "preview.html"))

    best = keepers[0]
    print(f"\nBest: rank 1 — {best.distance_mi:.1f} mi, "
          f"{best.ascent_ft:.0f} ft climbing ({best.provider}, {best.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
