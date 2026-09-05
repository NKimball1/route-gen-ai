"""Shared compose pipeline: specs in, ranked GPX routes out.

Both the CLI (compose_route.py) and the natural-language entry point (ask.py)
call this; a web backend would too.
"""
import os

from routes.geocode import geocode
from routes.gpx_out import write_gpx
from routes.preview import build_preview
from routes.providers import BRouterProvider, ORSProvider
from routes.scoring import rank
from routes.spec import METERS_PER_MILE, RouteSpec

OUT_DIR = os.path.join("output", "routes")


def build_providers(which: str = "all", profile: str = "fastbike-lowtraffic") -> list:
    providers = []
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


def compose(specs: list[RouteSpec], providers: list, candidates_per: int = 6,
            out_dir: str = OUT_DIR) -> list:
    """Run the full pipeline. Returns ranked keepers (also writes GPX+preview)."""
    spec = specs[0]
    lat, lon, place = geocode(spec.address)
    print(f"Start: {place} ({lat:.5f}, {lon:.5f})")

    candidates = []
    for p in providers:
        for s in specs:
            print(f"Generating {candidates_per} {s.shape} candidates via {p.name}...")
            candidates.extend(p.candidates(s, lat, lon, n=candidates_per))

    keepers, rejects = rank(spec, candidates)
    for c, reason in rejects:
        print(f"  reject [{c.provider} {c.seed}]: {reason}")
    if not keepers:
        print("No candidate met the constraints. Try more candidates or a "
              "looser target.")
        return []

    os.makedirs(out_dir, exist_ok=True)
    miles = spec.distance_m / METERS_PER_MILE
    goal = ("maxclimb" if spec.maximize_ascent
            else "minclimb" if spec.minimize_ascent else "ride")
    gpx_paths = []
    print(f"\n{'rank':<5}{'provider':<9}{'shape':<9}{'miles':>7}{'climb ft':>10}"
          f"{'repeat':>8}{'major':>7}  file")
    for i, c in enumerate(keepers, 1):
        fname = f"route_{miles:.0f}mi_{goal}_{i}_{c.shape}_{c.provider}.gpx"
        path = os.path.join(out_dir, fname)
        write_gpx(c, f"{miles:.0f}mi {goal} #{i} ({c.shape}, {c.provider}, {c.seed})",
                  path)
        gpx_paths.append(path)
        repeat = "n/a" if c.shape == "outback" else f"{c.overlap_frac:.0%}"
        major = "0" if c.major_m < 50 else f"{c.major_m / 1609.344:.1f}mi"
        print(f"{i:<5}{c.provider:<9}{c.shape:<9}{c.distance_mi:>7.1f}"
              f"{c.ascent_ft:>10.0f}{repeat:>8}{major:>7}  {path}")

    build_preview(gpx_paths, os.path.join(out_dir, "preview.html"))

    best = keepers[0]
    print(f"\nBest: rank 1 — {best.distance_mi:.1f} mi, "
          f"{best.ascent_ft:.0f} ft climbing ({best.provider}, {best.seed})")
    return keepers
