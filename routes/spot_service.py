"""Interval search application and shared CLI/web descriptions."""
import os
from typing import Any
from routes.elevation import track_ascent
from routes.geocode import geocode
from routes.gpx_out import write_track
from routes.intervals import IntervalSpec, Spot, find_spots
from routes.places import Lookup, describe_stretch, road_at
from routes.power import mmss
from routes.preview import build_preview
from routes.providers import BRouterProvider
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE
from routes.storage import artifact_path

OUT_DIR = os.path.join("output", "spots")


def climb_ft_per_mile(s: Spot) -> float:
    """Total climbing per mile -- the honest flatness number (an average
    grade of 0% can hide 300 ft of rollers)."""
    return track_ascent(s.points) / METERS_PER_FOOT / max(s.length_mi, 0.01)


def where_lines(spots: list[Spot], lookup: Lookup = road_at) -> list[str]:
    """'#1: Hope Road, Madison (Femrite Drive -> Nora Road), starts at ...'"""
    return [f"#{i}: {(s.road_name or describe_stretch([(p[0], p[1]) for p in s.points], lookup))}  "
            f"(starts at {s.points[0][0]:.5f},{s.points[0][1]:.5f})"
            for i, s in enumerate(spots, 1)]


def run_spot_search(spec: IntervalSpec, profile: str | None = None,
                    out_dir: str = OUT_DIR, names: bool = True) -> list[Spot]:
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

    os.makedirs(out_dir, exist_ok=True)
    gpx_paths = []
    at_w = f"{'@' + format(spec.watts, '.0f') + 'W':>8}" if spec.watts else ""
    if spec.watts and spec.kind == "any":
        at_w += f"{'back':>8}"   # the same stretch ridden the other way
    print(f"\n{'rank':<5}{'len mi':>7}{'grade %':>9}{'ft/mi':>7}{'±%':>6}{'turns/km':>10}"
          f"{'stops':>7}{'unpaved':>9}{'busy':>6}{'ride out mi':>13}{at_w}  file")
    for i, s in enumerate(spots, 1):
        path = artifact_path(out_dir, f"spot_{spec.kind}_{spec.reps}x{spec.rep_minutes:.0f}_{i}")
        s.gpx_path = path
        desc = (f"{s.length_mi:.1f} mi @ {s.mean_grade_pct:+.1f}% "
                f"(±{s.grade_std_pct:.1f}), {s.turns_per_km:.1f} turns/km, "
                f"{s.n_controls if s.controls_known else '?'} stops/signals, "
                f"{s.dist_from_start_m / METERS_PER_MILE:.1f} mi from start")
        write_track(s.points, f"{spec.kind} spot #{i} ({spec.reps}x{spec.rep_minutes:.0f})",
                    desc, path)
        gpx_paths.append(path)
        t_w = (f"{mmss(s.stretch.lap_seconds(spec.watts, spec.total_kg)):>8}"
               if spec.watts else "")
        if spec.watts and spec.kind == "any":
            t_w += f"{mmss(s.stretch.lap_seconds(spec.watts, spec.total_kg, back=True)):>8}"
        print(f"{i:<5}{s.length_mi:>7.1f}{s.mean_grade_pct:>9.1f}"
              f"{climb_ft_per_mile(s):>7.0f}"
              f"{s.grade_std_pct:>6.1f}{s.turns_per_km:>10.1f}"
              f"{(str(s.n_controls) if s.controls_known else '?'):>7}"
              f"{s.unpaved_frac:>9.0%}{s.busy_frac:>6.0%}"
              f"{s.dist_from_start_m / METERS_PER_MILE:>13.1f}"
              f"{t_w}  {path}")

    build_preview(gpx_paths, os.path.splitext(gpx_paths[0])[0] + ".html")
    if names:
        for spot in spots:
            spot.road_name = describe_stretch([(p[0], p[1]) for p in spot.points])
        print("\nWhere:")
        for line in where_lines(spots):
            print("  " + line)

    print("\n" + best_summary(spec, spots[0]))
    return spots


def best_summary(spec: IntervalSpec, best: Spot) -> str:
    """The one line a rider plans around: where, how long a pass takes,
    and how many laps a rep needs."""
    timing = ""
    if spec.watts:
        one_pass = best.stretch.lap_seconds(spec.watts, spec.total_kg)
        if spec.kind == "any":
            # Both directions get ridden; show the timing of each pass.
            back = best.stretch.lap_seconds(spec.watts, spec.total_kg, back=True)
            timing = (f"; one pass takes {mmss(one_pass)} out / {mmss(back)} "
                      f"back at {spec.watts:.0f} W")
        else:
            timing = f"; one pass takes {mmss(one_pass)} at {spec.watts:.0f} W"
        timing += f" ({spec.total_kg:.0f} kg rider+bike)"
    laps = best.stretch.laps_per_rep(spec)
    note = "" if laps == 1 else f" (~{laps} laps per rep — expect turnarounds)"
    if not best.controls_known:
        note += " — stop/signal counts UNKNOWN this run (Overpass was down)"
    return (f"Best: {best.length_mi:.1f} mi at {best.mean_grade_pct:+.1f}%, "
            f"{best.dist_from_start_m / METERS_PER_MILE:.1f} mi ride out{timing}{note}")


def spot_warnings(spec: IntervalSpec, spot: Spot) -> list[str]:
    warnings = []
    if not spot.controls_known:
        warnings.append("Stop/signal counts are UNKNOWN because traffic-control data is unavailable.")
    laps = spot.stretch.laps_per_rep(spec)
    if laps > 1:
        warnings.append(f"{laps} laps per rep; turnarounds interrupt the effort.")
    return warnings


def spot_metrics(spec: IntervalSpec, spot: Spot) -> dict[str, Any]:
    return {"distance_m": spot.length_m, "mean_grade_pct": spot.mean_grade_pct,
            "climb_ft_per_mile": climb_ft_per_mile(spot),
            "stops": spot.n_controls if spot.controls_known else None,
            "unpaved_fraction": spot.unpaved_frac, "busy_fraction": spot.busy_frac,
            "travel_distance_m": spot.dist_from_start_m,
            "laps_per_rep": spot.stretch.laps_per_rep(spec),
            "road_name": spot.road_name,
            "seconds_out": (spot.stretch.lap_seconds(spec.watts, spec.total_kg)
                            if spec.watts else None),
            "seconds_back": (spot.stretch.lap_seconds(spec.watts, spec.total_kg, back=True)
                             if spec.watts else None)}


def spot_label(spec: IntervalSpec, spot: Spot) -> str:
    stops = str(spot.n_controls) if spot.controls_known else "UNKNOWN"
    road = (spot.road_name + ": ") if spot.road_name else ""
    label = (f"{road}{spot.length_mi:.1f} mi @ {spot.mean_grade_pct:+.1f}%, "
             f"{climb_ft_per_mile(spot):.0f} ft/mi, {stops} stops, "
             f"{spot.dist_from_start_m / METERS_PER_MILE:.1f} mi out, "
             f"{spot.unpaved_frac:.0%} unpaved, {spot.busy_frac:.0%} busy")
    laps = spot.stretch.laps_per_rep(spec)
    if laps > 1:
        label += f" - {laps} laps per rep (turnarounds)"
    if spec.watts:
        label += f"; {mmss(spot.stretch.lap_seconds(spec.watts, spec.total_kg))} at {spec.watts:g} W"
        if spec.kind == "any":
            back = spot.stretch.lap_seconds(spec.watts, spec.total_kg, back=True)
            label += f" out / {mmss(back)} back"
    return label
