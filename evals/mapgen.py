"""Render a GPX as a SELF-CONTAINED SVG map plus an elevation profile.

Why not Leaflet: the case study has to stay readable when an external tile
service is slow, blocked, or gone. These SVGs are inline in the page, need
no network at view time, and carry no API key. The grey context roads are
fetched ONCE at build time from Overpass and cached under evals/cache/.

Data: route geometry from BRouter over OpenStreetMap extracts; context roads
and water from the OSM Overpass API. OSM data is (c) OpenStreetMap
contributors, ODbL -- the page carries that attribution.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Sequence

from evals import geo

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CONTEXT_CACHE = os.path.join(CACHE, "context_roads.json")

# Road classes drawn behind the route, thickest first.
CONTEXT_QUERY = """[out:json][timeout:90];
(
  way["highway"~"^(motorway|trunk|primary|secondary)$"]({bbox});
  way["natural"="water"]({bbox});
  way["waterway"="riverbank"]({bbox});
);
out geom;"""


def _load_cache() -> dict[str, Any]:
    try:
        with open(CONTEXT_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    os.makedirs(CACHE, exist_ok=True)
    tmp = CONTEXT_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CONTEXT_CACHE)


def fetch_context(bounds: tuple[float, float, float, float]
                  ) -> list[dict[str, Any]]:
    """Grey background geometry for one bbox, cached on disk."""
    key = ",".join(f"{v:.3f}" for v in bounds)
    cache = _load_cache()
    if key in cache:
        return cache[key]
    from routes.interruptions import query_overpass
    minlat, minlon, maxlat, maxlon = bounds
    q = CONTEXT_QUERY.format(
        bbox=f"{minlat:.4f},{minlon:.4f},{maxlat:.4f},{maxlon:.4f}")
    data = query_overpass(q)
    out: list[dict[str, Any]] = []
    if data:
        for el in data.get("elements", []):
            pts = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
            if len(pts) < 2:
                continue
            tags = el.get("tags", {})
            kind = ("water" if tags.get("natural") == "water"
                    or tags.get("waterway") else tags.get("highway", "road"))
            out.append({"kind": kind, "pts": _simplify(pts, 90.0)})
    # Only a real answer is cached. Overpass times out often enough that
    # caching an empty result would permanently bake a blank background
    # into one map and give no sign that anything had gone wrong.
    if out:
        _save_cache({**cache, key: out})
    else:
        print(f"  (no context geometry for {key}; will retry next build)")
    return out


def _simplify(pts: Sequence[tuple[float, float]],
              tol_m: float) -> list[tuple[float, float]]:
    """Drop points closer than tol_m to the previous kept one. Crude but
    enough: these are background lines a few pixels wide."""
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for p in pts[1:-1]:
        if geo.haversine_m(out[-1], p) >= tol_m:
            out.append(p)
    out.append(pts[-1])
    return out


def _projector(bounds: tuple[float, float, float, float],
               w: float, h: float, pad: float):
    minlat, minlon, maxlat, maxlon = bounds
    midlat = (minlat + maxlat) / 2
    kx = math.cos(math.radians(midlat))
    x0, x1 = minlon * kx, maxlon * kx
    y0, y1 = minlat, maxlat
    spanx = max(x1 - x0, 1e-9)
    spany = max(y1 - y0, 1e-9)
    scale = min((w - 2 * pad) / spanx, (h - 2 * pad) / spany)
    offx = (w - spanx * scale) / 2
    offy = (h - spany * scale) / 2

    def project(lat: float, lon: float) -> tuple[float, float]:
        return (offx + (lon * kx - x0) * scale,
                h - (offy + (lat - y0) * scale))    # SVG y grows downward
    return project, scale, kx


def _path(pts: Sequence[Sequence[float]], project, dp: int = 1) -> str:
    """SVG path data, rounded and with repeated points dropped.

    These maps are ~640 px wide, so a tenth of a pixel is already past what
    anyone can see; background roads round to whole pixels. Dropping the
    duplicates that rounding creates takes roughly a third off the page.
    """
    d: list[str] = []
    last: tuple[float, float] | None = None
    for i, pt in enumerate(pts):
        x, y = project(pt[0], pt[1])
        x, y = round(x, dp), round(y, dp)
        if last is not None and (x, y) == last:
            continue
        d.append(f"{'M' if not d else 'L'}{x:g} {y:g}")
        last = (x, y)
    return "".join(d)


def route_svg(points: Sequence[Sequence[float]], width: int = 640,
              height: int = 420, context: bool = True,
              markers: Sequence[tuple[float, float, str]] = (),
              title: str = "") -> str:
    """One route as an inline SVG. Colors come from CSS custom properties so
    the map follows the page's light/dark theme."""
    minlat, minlon, maxlat, maxlon = geo.bbox(points)
    # pad the bbox ~6% so the route never touches the frame
    dlat = max((maxlat - minlat) * 0.06, 0.004)
    dlon = max((maxlon - minlon) * 0.06, 0.005)
    bounds = (minlat - dlat, minlon - dlon, maxlat + dlat, maxlon + dlon)
    project, scale, kx = _projector(bounds, width, height, 8.0)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'xmlns="http://www.w3.org/2000/svg" role="img" '
             f'aria-label="{title or "Generated cycling route"}" '
             f'class="routemap">']
    parts.append(f'<rect width="{width}" height="{height}" fill="var(--map-bg)"/>')

    if context:
        try:
            ctx = fetch_context(bounds)
        except Exception:
            ctx = []
        water = [c for c in ctx if c["kind"] == "water"]
        roads = [c for c in ctx if c["kind"] != "water"]
        for c in water:
            parts.append(f'<path d="{_path(c["pts"], project, 0)}Z" '
                         f'fill="var(--map-water)" stroke="none"/>')
        widths = {"motorway": 2.2, "trunk": 2.0, "primary": 1.6,
                  "secondary": 1.1}
        for c in sorted(roads, key=lambda c: -widths.get(c["kind"], 1.0)):
            parts.append(
                f'<path d="{_path(c["pts"], project, 0)}" fill="none" '
                f'stroke="var(--map-road)" '
                f'stroke-width="{widths.get(c["kind"], 1.0)}" '
                f'stroke-linecap="round" stroke-linejoin="round"/>')

    parts.append(f'<path d="{_path(points, project)}" fill="none" '
                 f'stroke="var(--map-route-halo)" stroke-width="5.5" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')
    parts.append(f'<path d="{_path(points, project)}" fill="none" '
                 f'stroke="var(--map-route)" stroke-width="2.6" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>')

    sx, sy = project(points[0][0], points[0][1])
    parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5.5" '
                 f'fill="var(--map-start)" stroke="var(--map-bg)" '
                 f'stroke-width="2"><title>Start</title></circle>')
    end_gap = geo.haversine_m(points[0], points[-1])
    if end_gap > 300:
        ex, ey = project(points[-1][0], points[-1][1])
        parts.append(f'<rect x="{ex - 4.5:.1f}" y="{ey - 4.5:.1f}" width="9" '
                     f'height="9" fill="var(--map-end)" stroke="var(--map-bg)" '
                     f'stroke-width="2"><title>Finish</title></rect>')
    for lat, lon, label in markers:
        mx, my = project(lat, lon)
        parts.append(
            f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="4.5" fill="none" '
            f'stroke="var(--map-marker)" stroke-width="2.2" '
            f'stroke-dasharray="2.4 2.2"><title>{label}</title></circle>')

    # scale bar: a round number of miles
    px_per_m = scale / (110540.0)   # scale is px per degree latitude
    for miles in (1, 2, 5, 10, 20):
        bar = miles * geo.METERS_PER_MILE * px_per_m
        if bar > width * 0.16:
            break
    y = height - 16
    parts.append(f'<line x1="14" y1="{y}" x2="{14 + bar:.1f}" y2="{y}" '
                 f'stroke="var(--map-ink)" stroke-width="2"/>')
    parts.append(f'<line x1="14" y1="{y - 4}" x2="14" y2="{y + 4}" '
                 f'stroke="var(--map-ink)" stroke-width="2"/>')
    parts.append(f'<line x1="{14 + bar:.1f}" y1="{y - 4}" '
                 f'x2="{14 + bar:.1f}" y2="{y + 4}" '
                 f'stroke="var(--map-ink)" stroke-width="2"/>')
    parts.append(f'<text x="{18 + bar:.1f}" y="{y + 4}" font-size="11" '
                 f'fill="var(--map-ink)" font-family="ui-monospace, monospace">'
                 f'{miles} mi</text>')
    parts.append("</svg>")
    return "".join(parts)


def elevation_svg(points: Sequence[Sequence[float]], width: int = 640,
                  height: int = 90) -> str:
    """Elevation against distance. Returns '' when the track has no elevation."""
    eles = [(i, p[2]) for i, p in enumerate(points)
            if len(p) > 2 and p[2] is not None]
    if len(eles) < 4:
        return ""
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + geo.haversine_m(points[i - 1], points[i]))
    total = cum[-1] or 1.0
    lo = min(e for _, e in eles)
    hi = max(e for _, e in eles)
    span = max(hi - lo, 10.0)
    pad = 6
    pts = []
    for i, e in eles:
        x = pad + (cum[i] / total) * (width - 2 * pad)
        y = height - pad - ((e - lo) / span) * (height - 2 * pad - 4)
        pts.append(f"{x:.1f},{y:.1f}")
    area = (f"M{pad},{height - pad} L" + " L".join(pts)
            + f" L{width - pad},{height - pad} Z")
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Elevation profile" class="elevation">'
        f'<path d="{area}" fill="var(--map-ele-fill)" stroke="none"/>'
        f'<polyline points="{" ".join(pts)}" fill="none" '
        f'stroke="var(--map-ele)" stroke-width="1.6"/>'
        f'<text x="{pad}" y="12" font-size="10" fill="var(--map-ink)" '
        f'font-family="ui-monospace, monospace">'
        f'{geo.fmt_ft(hi - lo):.0f} ft range</text>'
        f'</svg>')


def render_gpx(path: str, **kwargs: Any) -> tuple[str, str]:
    pts = geo.parse_gpx_strict(path)
    return route_svg(pts, **kwargs), elevation_svg(pts)
