"""Interval spot finder CLI: locate a stretch of road for structured intervals.

Examples:

  # 2x20 threshold: flat, uninterrupted, within a 30 min easy ride
  python find_spot.py --address "123 Main St, Madison WI" --reps 2 --rep-minutes 20 --kind flat --max-travel-minutes 30

  # 4x5 VO2: a steady slight climb close to home
  python find_spot.py --address "123 Main St, Madison WI" --reps 4 --rep-minutes 5 --kind incline --max-travel-minutes 20

Writes each top spot as a GPX stretch to output/spots/ plus a map preview.
For plain-English requests, use ask.py instead.
"""
import argparse
import os
import sys

from routes.geocode import geocode
from routes.gpx_out import write_track
from routes.intervals import IntervalSpec, find_spots
from routes.preview import build_preview
from routes.providers import BRouterProvider

OUT_DIR = os.path.join("output", "spots")


def run_spot_search(spec: IntervalSpec, profile: str = "fastbike-lowtraffic",
                    out_dir: str = OUT_DIR) -> list:
    lat, lon, place = geocode(spec.address)
    print(f"Start: {place} ({lat:.5f}, {lon:.5f})")
    print(f"Looking for a {spec.kind} stretch ~{spec.rep_distance_m / 1609.344:.1f} mi "
          f"long within ~{spec.travel_radius_m / 1609.344:.0f} mi "
          f"({spec.max_travel_minutes:.0f} min easy riding)...")

    provider = BRouterProvider(profile=profile)
    spots = find_spots(spec, lat, lon, provider)
    if not spots:
        print("No suitable stretch found — try a larger travel radius.")
        return []

    os.makedirs(out_dir, exist_ok=True)
    gpx_paths = []
    print(f"\n{'rank':<5}{'len mi':>7}{'grade %':>9}{'±%':>6}{'turns/km':>10}"
          f"{'ride out mi':>13}  file")
    for i, s in enumerate(spots, 1):
        fname = f"spot_{spec.kind}_{spec.reps}x{spec.rep_minutes:.0f}_{i}.gpx"
        path = os.path.join(out_dir, fname)
        desc = (f"{s.length_mi:.1f} mi @ {s.mean_grade_pct:+.1f}% "
                f"(±{s.grade_std_pct:.1f}), {s.turns_per_km:.1f} turns/km, "
                f"{s.dist_from_start_m / 1609.344:.1f} mi from start")
        write_track(s.points, f"{spec.kind} spot #{i} ({spec.reps}x{spec.rep_minutes:.0f})",
                    desc, path)
        gpx_paths.append(path)
        print(f"{i:<5}{s.length_mi:>7.1f}{s.mean_grade_pct:>9.1f}"
              f"{s.grade_std_pct:>6.1f}{s.turns_per_km:>10.1f}"
              f"{s.dist_from_start_m / 1609.344:>13.1f}  {path}")

    build_preview(gpx_paths, os.path.join(out_dir, "preview.html"))

    best = spots[0]
    laps = max(1, round(spec.rep_distance_m / best.length_m + 0.49))
    note = "" if laps == 1 else f" (~{laps} laps per rep — expect turnarounds)"
    print(f"\nBest: {best.length_mi:.1f} mi at {best.mean_grade_pct:+.1f}%, "
          f"{best.dist_from_start_m / 1609.344:.1f} mi ride out{note}")
    return spots


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", required=True)
    ap.add_argument("--reps", type=int, required=True)
    ap.add_argument("--rep-minutes", type=float, required=True)
    ap.add_argument("--kind", choices=["flat", "incline"], required=True)
    ap.add_argument("--max-travel-minutes", type=float, default=30.0)
    ap.add_argument("--profile", default="fastbike-lowtraffic")
    args = ap.parse_args()

    spec = IntervalSpec(args.address, args.reps, args.rep_minutes, args.kind,
                        args.max_travel_minutes)
    return 0 if run_spot_search(spec, args.profile) else 1


if __name__ == "__main__":
    sys.exit(main())
