"""Route composer CLI: turn a ride request into Garmin-ready GPX files.

Examples (the two target prompts):

  # ~30 mile loop, under 1000 ft of climbing
  python compose_route.py --address "123 Main St, Madison WI" --miles 30 --max-climb-ft 1000

  # 50 miles, as much climbing as possible, loop or out-and-back
  python compose_route.py --address "Boulder, CO" --miles 50 --maximize-climb --shape both

  # avoid a road/area (repeatable; ":radius_m" optional, default 800)
  python compose_route.py --address "..." --miles 30 --avoid "Verona Rd, Madison WI:1500"

Providers: brouter (no key needed) always runs; ors runs if ORS_API_KEY is set
(free key from https://openrouteservice.org). GPX files land in output/routes/.

For plain-English requests, use ask.py instead.
"""
import argparse
import sys

from routes.geocode import geocode
from routes.pipeline import build_providers, compose
from routes.spec import RouteSpec


def parse_avoid(items: list[str]) -> list[tuple[float, float, float]]:
    zones = []
    for item in items:
        place, _, radius = item.rpartition(":")
        if place and radius.replace(".", "").isdigit():
            radius_m = float(radius)
        else:
            place, radius_m = item, 800.0
        lat, lon, name = geocode(place)
        print(f"Avoiding: {name} (r={radius_m:.0f} m)")
        zones.append((lat, lon, radius_m))
    return zones


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
    ap.add_argument("--profile", default="fastbike-lowtraffic",
                    help="BRouter profile: fastbike-lowtraffic (default), "
                         "fastbike-verylowtraffic, trekking, safety")
    ap.add_argument("--shape", choices=["loop", "outback", "both"], default="loop",
                    help="loop (default), outback, or both competing together")
    ap.add_argument("--avoid", action="append", default=[],
                    help='no-go area, "place name" or "place name:radius_m"')
    args = ap.parse_args()

    avoid = parse_avoid(args.avoid)
    shapes = ["loop", "outback"] if args.shape == "both" else [args.shape]
    specs = [RouteSpec.from_imperial(args.address, args.miles, args.max_climb_ft,
                                     args.maximize_climb, shape=s, avoid=avoid)
             for s in shapes]

    providers = build_providers(args.provider, args.profile)
    keepers = compose(specs, providers, candidates_per=args.candidates)
    return 0 if keepers else 1


if __name__ == "__main__":
    sys.exit(main())
