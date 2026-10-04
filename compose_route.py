"""Route composer CLI: turn a ride request into Garmin-ready GPX files.

Examples (the two target prompts):

  # ~30 mile loop, under 1000 ft of climbing
  python compose_route.py --address "123 Main St, Madison WI" --miles 30 --max-climb-ft 1000

  # 50 miles, as much climbing as possible, loop or out-and-back
  python compose_route.py --address "Boulder, CO" --miles 50 --maximize-climb --shape both

  # avoid a road/area (repeatable; ":radius_m" optional, default 800)
  python compose_route.py --address "..." --miles 30 --avoid "Verona Rd, Madison WI:1500"

Provider: BRouter by default; ORS requires --provider ors/all and ORS_API_KEY.
GPX files land in output/routes/.

For plain-English requests, use ask.py instead.
"""
import argparse
import sys

from dotenv import load_dotenv

# before the route imports: some modules read settings (e.g. ROUTEGEN_TOTAL_KG)
# at import time
load_dotenv()

from routes.geocode import geocode
from routes.constraints import resolve_avoid
from routes.pipeline import build_providers, compose
from routes.spec import NoGo, RouteSpec


def parse_avoid(items: list[str]) -> list[NoGo]:
    """Compatibility helper; application callers also retain road constraints."""
    return resolve_avoid(items).routing_zones


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", required=True, help="start/end address")
    ap.add_argument("--miles", type=float, required=True, help="target distance")
    ap.add_argument("--max-climb-ft", type=float, default=None,
                    help="reject candidates climbing more than this")
    ap.add_argument("--maximize-climb", action="store_true",
                    help="rank candidates by most climbing")
    ap.add_argument("--minimize-climb", action="store_true",
                    help="rank candidates by least climbing")
    ap.add_argument("--candidates", type=int, default=6,
                    help="loop candidates per provider (default 6)")
    ap.add_argument("--provider", choices=["brouter", "ors", "all"], default="brouter")
    ap.add_argument("--profile", default=None,
                    help="BRouter profile (default: fastbike-quiet on the "
                         "self-hosted server, fastbike-lowtraffic on public)")
    ap.add_argument("--shape", choices=["loop", "outback", "both"], default="loop",
                    help="loop (default), outback, or both competing together")
    ap.add_argument("--avoid", action="append", default=[],
                    help='no-go area, "place name" or "place name:radius_m"')
    ap.add_argument("--via", action="append", default=[],
                    help="place the route must pass through (repeatable, ordered)")
    ap.add_argument("--use-strava", action="store_true",
                    help="include your explicitly configured personal Strava starred climbs")
    args = ap.parse_args()

    avoidance = resolve_avoid(args.avoid)
    via, via_names = [], []
    for place in args.via:
        vlat, vlon, vname = geocode(place)
        print(f"Via: {vname}")
        via.append((vlat, vlon))
        via_names.append(place)
    shapes = ["loop", "outback"] if args.shape == "both" else [args.shape]
    specs = [RouteSpec.from_imperial(args.address, args.miles, args.max_climb_ft,
                                     args.maximize_climb, shape=s, avoid=avoidance.routing_zones,
                                     minimize_climb=args.minimize_climb,
                                     via=via, via_names=via_names)
             for s in shapes]

    for spec in specs:
        spec.avoid_roads = avoidance.roads
        spec.avoid_areas = avoidance.areas
        spec.use_strava = args.use_strava

    providers = build_providers(args.provider, args.profile)
    keepers = compose(specs, providers, candidates_per=args.candidates)
    return 0 if keepers else 1


if __name__ == "__main__":
    sys.exit(main())
