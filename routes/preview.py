"""Render GPX route files as a Leaflet map grid for eyeballing candidates.

Usage:
    python -m routes.preview output/routes/route_30mi_loop_1_brouter.gpx ...

Writes output/routes/preview.html (open it in a browser; needs internet for
map tiles). Distance/ascent shown are recomputed from the GPX points.
"""
import json
import math
import os
import re
import sys

from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

EARTH_RADIUS_M = 6371000.0

PAGE = """<!DOCTYPE html><html><head><title>Route candidates</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body {{ margin: 0; font-family: system-ui, sans-serif; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 8px; padding: 8px; }}
.cell {{ height: 46vh; min-height: 320px; position: relative; }}
.cell .label {{ position: absolute; top: 6px; left: 50px; z-index: 1000;
  background: rgba(255,255,255,.9); padding: 2px 8px; border-radius: 4px; font-size: 13px; }}
</style></head><body><div class="grid" id="grid"></div><script>
var ROUTES = {routes_json};
ROUTES.forEach(function(r, i) {{
  var cell = document.createElement('div');
  cell.className = 'cell'; cell.id = 'map' + i;
  cell.innerHTML = '<div class="label">' + r.label + '</div>';
  document.getElementById('grid').appendChild(cell);
  var m = L.map(cell, {{fadeAnimation: false, zoomAnimation: false}});
  L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',
              {{attribution: '&copy; OpenStreetMap'}}).addTo(m);
  var line = L.polyline(r.points, {{color: 'red', weight: 3}}).addTo(m);
  L.circleMarker(r.points[0], {{radius: 6, color: 'green'}}).addTo(m);
  var fit = function() {{ m.invalidateSize(); m.fitBounds(line.getBounds(), {{animate: false}}); }};
  // Fit repeatedly for a moment: a hidden or still-laying-out page reports a
  // zero-size map, which makes the first fitBounds land on zoom 0.
  fit(); setTimeout(fit, 300); setTimeout(fit, 1000);
  new ResizeObserver(fit).observe(cell);
}});
</script></body></html>
"""


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _parse_gpx(path: str) -> list[tuple[float, float, float | None]]:
    text = open(path, encoding="utf-8").read()
    pts = []
    for m in re.finditer(
            r'<trkpt lat="([\-0-9.]+)" lon="([\-0-9.]+)"'
            r'(?:\s*/>|>\s*<ele>([\-0-9.]+)</ele>)', text):
        lat, lon, ele = float(m.group(1)), float(m.group(2)), m.group(3)
        pts.append((lat, lon, float(ele) if ele else None))
    return pts


def _stats(points) -> tuple[float, float]:
    dist = sum(_haversine_m(points[i][:2], points[i + 1][:2])
               for i in range(len(points) - 1))
    ascent, smooth_threshold = 0.0, 2.0  # ignore sub-2m jitter between points
    last_ele = None
    for _, _, ele in points:
        if ele is None:
            continue
        if last_ele is not None and ele - last_ele > smooth_threshold:
            ascent += ele - last_ele
        if last_ele is None or abs(ele - last_ele) > smooth_threshold:
            last_ele = ele
    return dist, ascent


def _parse_desc(path: str) -> str | None:
    m = re.search(r"<desc>([^<]+)</desc>", open(path, encoding="utf-8").read())
    return m.group(1) if m else None


def build_preview(gpx_paths: list[str], out_path: str) -> None:
    routes = []
    for path in gpx_paths:
        points = _parse_gpx(path)
        if not points:
            print(f"skipping {path}: no track points found")
            continue
        desc = _parse_desc(path)
        if desc is None:  # foreign GPX without our stats block: derive roughly
            dist_m, ascent_m = _stats(points)
            desc = (f"{dist_m / METERS_PER_MILE:.1f} mi, "
                    f"~{ascent_m / METERS_PER_FOOT:.0f} ft")
        label = f"{os.path.basename(path)} — {desc}"
        routes.append({"label": label, "points": [[p[0], p[1]] for p in points]})
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(PAGE.format(routes_json=json.dumps(routes)))
    print(f"wrote {out_path} ({len(routes)} routes)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    build_preview(sys.argv[1:], os.path.join("output", "routes", "preview.html"))
