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
--no-names skips the lookups) and shows climbing per mile and the share
that's unpaved. Writes each top spot as a GPX stretch to output/spots/
plus a map preview.
For plain-English requests, use ask.py instead.
"""
import argparse
import math
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from routes.elevation import track_ascent
from routes.geocode import geocode
from routes.gpx_out import write_track
from routes.intervals import IntervalSpec, IntervalSpot, find_spots
from routes.places import Lookup, describe_stretch, road_at
from routes.power import mmss
from routes.preview import build_preview
from routes.providers import BRouterProvider
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

OUT_DIR = os.path.join("output", "spots")


def climb_ft_per_mile(s: IntervalSpot) -> float:
    """Total climbing per mile -- the honest flatness number (an average
    grade of 0% can hide 300 ft of rollers)."""
    return track_ascent(s.points) / METERS_PER_FOOT / max(s.length_mi, 0.01)


def where_lines(spots: list[IntervalSpot], lookup: Lookup = road_at) -> list[str]:
    """'#1: Hope Road, Madison (Femrite Drive -> Nora Road), starts at ...'"""
    return [f"#{i}: {describe_stretch([(p[0], p[1]) for p in s.points], lookup)}  "
            f"(starts at {s.points[0][0]:.5f},{s.points[0][1]:.5f})"
            for i, s in enumerate(spots, 1)]


def run_spot_search(spec: IntervalSpec, profile: str | None = None,
                    out_dir: str = OUT_DIR, names: bool = True) -> list:
    lat, lon, place = geocode(spec.address)
    print(f"Start: {place} ({lat:.5f}, {lon:.5f})")
    sized = (f" at {spec.watts:.0f} W" if spec.watts else "")
    print(f"Looking for a {spec.kind} stretch ~{spec.rep_distance_m / METERS_PER_MILE:.1f} mi "
          f"long ({spec.rep_minutes:.0f} min{sized}) within "
          f"~{spec.travel_radius_m / METERS_PER_MILE:.0f} mi "
          f"({spec.max_travel_minutes:.0f} min easy riding)...")

    provider = BRouterProvider(profile=profile)
    spots = find_spots(spec, lat, lon, provider)
    if not spots:
        hint = (" or allow a stop (--max-stops)" if spec.max_stops is not None
                else "")
        print(f"No suitable stretch found — try a larger travel radius{hint}.")
        return []
    if spec.max_stops is not None and not spots[0].controls_known:
        print("  note: stop counts are unknown this run, so --max-stops "
              "couldn't be enforced")

    os.makedirs(out_dir, exist_ok=True)
    gpx_paths = []
    at_w = f"{'@' + format(spec.watts, '.0f') + 'W':>8}" if spec.watts else ""
    if spec.watts and spec.kind == "any":
        at_w += f"{'back':>8}"   # the same stretch ridden the other way
    print(f"\n{'rank':<5}{'len mi':>7}{'grade %':>9}{'ft/mi':>7}{'±%':>6}{'turns/km':>10}"
          f"{'stops':>7}{'unpaved':>9}{'ride out mi':>13}{at_w}  file")
    for i, s in enumerate(spots, 1):
        fname = f"spot_{spec.kind}_{spec.reps}x{spec.rep_minutes:.0f}_{i}.gpx"
        path = os.path.join(out_dir, fname)
        desc = (f"{s.length_mi:.1f} mi @ {s.mean_grade_pct:+.1f}% "
                f"(±{s.grade_std_pct:.1f}), {s.turns_per_km:.1f} turns/km, "
                f"{s.n_controls if s.controls_known else '?'} stops/signals, "
                f"{s.dist_from_start_m / METERS_PER_MILE:.1f} mi from start")
        write_track(s.points, f"{spec.kind} spot #{i} ({spec.reps}x{spec.rep_minutes:.0f})",
                    desc, path)
        gpx_paths.append(path)
        t_w = (f"{mmss(s.seconds_at(spec.watts, spec.total_kg)):>8}"
               if spec.watts else "")
        if spec.watts and spec.kind == "any":
            t_w += f"{mmss(s.seconds_at(spec.watts, spec.total_kg, reverse=True)):>8}"
        print(f"{i:<5}{s.length_mi:>7.1f}{s.mean_grade_pct:>9.1f}"
              f"{climb_ft_per_mile(s):>7.0f}"
              f"{s.grade_std_pct:>6.1f}{s.turns_per_km:>10.1f}"
              f"{(str(s.n_controls) if s.controls_known else '?'):>7}"
              f"{s.unpaved_frac:>9.0%}"
              f"{s.dist_from_start_m / METERS_PER_MILE:>13.1f}"
              f"{t_w}  {path}")

    build_preview(gpx_paths, os.path.join(out_dir, "preview.html"))
    if names:
        print("\nWhere:")
        for line in where_lines(spots):
            print("  " + line)

    print("\n" + best_summary(spec, spots[0]))
    return spots


def best_summary(spec: IntervalSpec, best: IntervalSpot) -> str:
    """The one line a rider plans around: where, how long a pass takes,
    and how many laps a rep needs."""
    if spec.watts:
        # laps from TIME at the stretch's real grade, not from the
        # search window's assumed one
        one_pass = best.seconds_at(spec.watts, spec.total_kg)
        if spec.kind == "any":
            # out-and-back: the slower direction decides whether a pass
            # fills the rep, and the rider needs both numbers
            back = best.seconds_at(spec.watts, spec.total_kg, reverse=True)
            timing = (f"; one pass takes {mmss(one_pass)} out / {mmss(back)} "
                      f"back at {spec.watts:.0f} W")
            one_pass = min(one_pass, back)
        else:
            timing = f"; one pass takes {mmss(one_pass)} at {spec.watts:.0f} W"
        timing += f" ({spec.total_kg:.0f} kg rider+bike)"
        laps = max(1, math.ceil(spec.rep_minutes * 60.0 / one_pass - 0.05))
    else:
        laps = max(1, round(spec.rep_distance_m / best.length_m + 0.49))
        timing = ""
    note = "" if laps == 1 else f" (~{laps} laps per rep — expect turnarounds)"
    if not best.controls_known:
        note += " — stop/signal counts UNKNOWN this run (Overpass was down)"
    return (f"Best: {best.length_mi:.1f} mi at {best.mean_grade_pct:+.1f}%, "
            f"{best.dist_from_start_m / METERS_PER_MILE:.1f} mi ride out{timing}{note}")


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
