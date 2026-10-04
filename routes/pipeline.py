"""Shared compose pipeline: specs in, ranked GPX routes out.

The CLI and shared natural-language service call the same pipeline.
"""
import os
from dataclasses import replace

from routes.geocode import geocode
from routes.gpx_out import write_gpx
from routes.preview import build_preview
from routes.providers import BRouterProvider, ORSProvider
from routes.scoring import rank
from routes.storage import artifact_path, current_route, record_parent, select_route, note_outcome, transaction
from routes.spec import (MAJOR_DISPLAY_MIN_M, METERS_PER_MILE,
                         RouteCandidate, RouteSpec)

Provider = BRouterProvider | ORSProvider

OUT_DIR: str = os.path.join("output", "routes")


def build_providers(which: str = "brouter",
                    profile: str | None = None) -> list[Provider]:
    providers: list[Provider] = []
    if which in ("brouter", "all"):
        providers.append(BRouterProvider(profile=profile))
    if which in ("ors", "all"):
        ors = ORSProvider()
        if ors.available:
            providers.append(ors)
        elif which == "ors":
            raise SystemExit(
                "ORS_API_KEY not set — get a free key at https://openrouteservice.org")
        else:
            print("(skipping ors: ORS_API_KEY not set)")
    return providers


def compose(specs: list[RouteSpec], providers: list[Provider],
            candidates_per: int = 6,
            out_dir: str = OUT_DIR) -> list[RouteCandidate]:
    with transaction(out_dir):
        return _compose(specs, providers, candidates_per, out_dir)


def _compose(specs: list[RouteSpec], providers: list[Provider],
            candidates_per: int = 6,
            out_dir: str = OUT_DIR) -> list[RouteCandidate]:
    """Run the full pipeline. Returns ranked keepers (also writes GPX+preview)."""
    if not specs:
        raise ValueError("At least one route specification is required.")
    if candidates_per < 1:
        raise ValueError("Request at least one routing candidate.")
    spec = specs[0]
    lat, lon, place = geocode(spec.address)
    print(f"Start: {place} ({lat:.5f}, {lon:.5f})")

    # For max-climb requests, scout real climbs (starred Strava segments +
    # our own elevation search) and add candidates routed THROUGH them —
    # blind bearing search finds hilly directions but stops short of summits.
    if spec.maximize_ascent and not spec.via:
        brouter = next((p for p in providers
                        if isinstance(p, BRouterProvider)), None)
        if brouter is not None:
            from routes.climbs import find_climbs
            print("Scouting climbs to target...")
            for climb in find_climbs(lat, lon, spec.distance_m / 2 / 1.3,
                                     brouter, use_strava=spec.use_strava):
                specs = specs + [replace(spec, shape="loop",
                                         via=[climb.start, climb.end],
                                         via_names=[climb.name])]

    candidates: list[RouteCandidate] = []
    for p in providers:
        for s in specs:
            label = s.shape + (f" via '{s.via_names[0]}'" if s.via_names else "")
            print(f"Generating {candidates_per} {label} candidates via {p.name}...")
            candidates.extend(p.candidates(s, lat, lon, n=candidates_per))

    keepers, rejects = rank(spec, candidates)
    for c, reason in rejects:
        print(f"  reject [{c.provider} {c.seed}]: {reason}")
    if not keepers:
        # "Nothing came back" and "nothing was good enough" are different
        # failures, and telling a user to loosen their target when the
        # routing server is down sends them to fix the wrong thing.
        if any(getattr(p, "unreachable", False) for p in providers):
            print("The routing server never answered, so no candidates could "
                  "be generated. This is a server problem, not a problem "
                  "with the request.")
        else:
            print("No candidate met the constraints. Try more candidates or a "
                  "looser target.")
        return []

    os.makedirs(out_dir, exist_ok=True)
    miles = spec.distance_m / METERS_PER_MILE
    goal = ("maxclimb" if spec.maximize_ascent
            else "minclimb" if spec.minimize_ascent else "ride")
    gpx_paths: list[str] = []
    print(f"\n{'rank':<5}{'provider':<9}{'shape':<9}{'miles':>7}{'climb ft':>10}"
          f"{'repeat':>8}{'major':>7}  file")
    parent = current_route(out_dir)
    for i, c in enumerate(keepers, 1):
        path = artifact_path(out_dir, f"route_{miles:.0f}mi_{goal}_{i}_{c.shape}_{c.provider}")
        c.gpx_path = path
        write_gpx(c, f"{miles:.0f}mi {goal} #{i}", path)
        gpx_paths.append(path)
        if parent:
            record_parent(path, parent)
        repeat = "n/a" if c.shape == "outback" else f"{c.overlap_frac:.0%}"
        major = "0" if c.major_m < MAJOR_DISPLAY_MIN_M else f"{c.major_m / METERS_PER_MILE:.1f}mi"
        print(f"{i:<5}{c.provider:<9}{c.shape:<9}{c.distance_mi:>7.1f}"
              f"{c.ascent_ft:>10.0f}{repeat:>8}{major:>7}  {path}")

    build_preview(gpx_paths, os.path.splitext(gpx_paths[0])[0] + ".html")

    # the winner becomes "the current route" that edit requests refine
    select_route(gpx_paths[0], out_dir)
    note_outcome(out_dir, True)

    best = keepers[0]
    print(f"\nBest: rank 1 — {best.distance_mi:.1f} mi, "
          f"{best.ascent_ft:.0f} ft climbing ({best.provider}, {best.seed})")
    return keepers
