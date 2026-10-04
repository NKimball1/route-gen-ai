"""Route along a list of named streets, in order.

  python street_route.py --start "Olbrich Park, Madison WI" \\
      --streets "Capital City State Trail -> Dempsey Road -> Davies Street -> Major Avenue"

  python street_route.py --start "Monona Terrace, Madison WI" --loop \\
      --streets "Jenifer St; Rutledge St; Lakeland Ave"

Separate streets with '->', ';', or ', then'. --start adds a lead-in from
an address to the first street; --end (or --loop, which ends back at the
start) adds a finish. Each street is fetched from OpenStreetMap near the
ride, waypoints are placed ON it, and afterward the route is measured
against every street: any street it didn't really ride is reported.
Writes a unique output/routes/streets_<id>.gpx plus its map preview.
"""
import argparse
import os
import sys

from dotenv import load_dotenv

# before the route imports: some modules read settings (e.g. ROUTEGEN_TOTAL_KG)
# at import time
load_dotenv()

from routes.geocode import geocode
from routes.gpx_out import write_track
from routes.preview import build_preview
from routes.providers import BRouterProvider
from routes.spec import METERS_PER_MILE, LatLon
from routes.street_list import build_street_route, parse_street_list
from routes.storage import artifact_path, publish, transaction

OUT_DIR: str = os.path.join("output", "routes")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--streets", required=True, help="streets in riding order")
    ap.add_argument("--start", default=None, help="address to start from")
    ap.add_argument("--end", default=None, help="address to finish at")
    ap.add_argument("--loop", action="store_true", help="finish back at --start")
    ap.add_argument("--near", default=None,
                    help="area to search for the streets when there is no start/end")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    names = parse_street_list(args.streets)
    if not names:
        print("No streets given.")
        return 1

    def place(text: str | None) -> LatLon | None:
        if not text:
            return None
        lat, lon, name = geocode(text)
        print(f"  {text} -> {name}")
        return (lat, lon)

    start = place(args.start)
    end = start if args.loop else place(args.end)
    near = place(args.near)
    print(f"Routing along {len(names)} streets: {' -> '.join(names)}")
    result = build_street_route(names, BRouterProvider(profile=args.profile),
                                start=start, end=end, near=near)

    for s in result.streets:
        mark = "ok " if s.ridden else "!! "
        print(f"  {mark}{s.name:32s} {s.ridden_m / METERS_PER_MILE:5.2f} mi ridden on it")
    for p in result.problems:
        print(f"  PROBLEM: {p}")
    if not result.points:
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    out = artifact_path(OUT_DIR, "streets")
    miles = result.distance_m / METERS_PER_MILE
    with transaction(OUT_DIR):
        write_track(result.points, " -> ".join(names)[:80],
                    f"{miles:.1f} mi along {len(names)} named streets", out)
        build_preview([out], os.path.splitext(out)[0] + ".html")
        publish(out, OUT_DIR)
    print(f"{'Done' if result.ok else 'Done, with problems above'}: "
          f"{miles:.1f} mi -> {out}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
