"""Interval spot finder CLI: locate a stretch of road for structured intervals.

Examples:

  # 2x20 threshold: flat, uninterrupted, within a 30 min easy ride
  python find_spot.py --address "123 Main St, Madison WI" --reps 2 --rep-minutes 20 --kind flat --max-travel-minutes 30

  # 4x5 VO2: a steady slight climb close to home
  python find_spot.py --address "123 Main St, Madison WI" --reps 4 --rep-minutes 5 --kind incline --max-travel-minutes 20

  # 4x4 at 285 W, grade doesn't matter, ridden out-and-back: sized by
  # physics, and each result shows the time in both directions
  python find_spot.py --address "123 Main St, Madison WI" --reps 4 --rep-minutes 4 --kind any --watts 285

  # 4x10 at 250 W with no stop signs or signals at all
  python find_spot.py --address "123 Main St, Madison WI" --reps 4 --rep-minutes 10 --kind flat --watts 250 --max-stops 0

Each result is named by road ("Hope Road (Femrite Drive -> Nora Road)";
--no-names skips the lookups) and shows climbing per mile and the shares
that are unpaved and on busy (secondary-or-bigger) roads. Writes each top spot as a GPX stretch to output/spots/
plus a map preview.
For plain-English requests, use ask.py instead.
"""
import argparse
import sys
from dotenv import load_dotenv

# before the route imports: some modules read settings (e.g. ROUTEGEN_TOTAL_KG)
# at import time
load_dotenv()

from routes.intervals import IntervalSpec
from routes.spot_service import (run_spot_search as run_spot_search,
                                 best_summary as best_summary,
                                 where_lines as where_lines,
                                 climb_ft_per_mile as climb_ft_per_mile)

__all__ = ['run_spot_search', 'best_summary', 'where_lines', 'climb_ft_per_mile']

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", required=True)
    ap.add_argument("--reps", type=int, required=True)
    ap.add_argument("--rep-minutes", type=float, required=True)
    ap.add_argument("--kind", choices=["flat", "incline", "any"], required=True,
                    help="any: grade doesn't matter, but the stretch must "
                         "work ridden in either direction (out-and-back reps)")
    ap.add_argument("--max-travel-minutes", type=float, default=30.0)
    ap.add_argument("--watts", type=float, default=None,
                    help="target power: size reps by physics, and report "
                         "how long each stretch takes at that power")
    ap.add_argument("--total-kg", type=float, default=None,
                    help="rider + bike mass for the physics (default: "
                         "ROUTEGEN_TOTAL_KG or 84)")
    ap.add_argument("--max-stops", type=int, default=None,
                    help="drop any stretch with more stop signs/signals than "
                         "this (0 = none at all)")
    ap.add_argument("--no-names", action="store_true",
                    help="skip the road-name lookups (one second each)")
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()

    spec = IntervalSpec(args.address, args.reps, args.rep_minutes, args.kind,
                        args.max_travel_minutes, watts=args.watts,
                        max_stops=args.max_stops,
                        **({"total_kg": args.total_kg} if args.total_kg else {}))
    return 0 if run_spot_search(spec, args.profile, names=not args.no_names) else 1


if __name__ == "__main__":
    sys.exit(main())
