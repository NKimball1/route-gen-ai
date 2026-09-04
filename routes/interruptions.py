"""Traffic-interruption data from OpenStreetMap via the Overpass API.

Stop signs, traffic signals, yields, and level crossings are tagged nodes in
OSM. One Overpass query fetches every control in the search area; counting
them along a candidate stretch is then pure local math. Free, no API key.

Known over-count: a control tagged for the CROSS street can sit within
tolerance of our road and count against us. That errs toward quieter spots,
which is the right direction for interval hunting.
"""
import math

import requests

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# How badly each control type breaks an interval effort.
WEIGHTS = {
    "traffic_signals": 1.5,
    "stop": 1.0,
    "level_crossing": 1.0,
    "give_way": 0.4,
}

QUERY = """[out:json][timeout:60];
(
  node["highway"~"^(stop|give_way|traffic_signals)$"]({bbox});
  node["railway"="level_crossing"]({bbox});
);
out;"""


def fetch_controls(lat: float, lon: float, radius_m: float) -> list:
    """All traffic controls within radius of (lat, lon): (lat, lon, weight)."""
    dlat = radius_m / 110540.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    bbox = f"{lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon}"
    data = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(
                url, data={"data": QUERY.format(bbox=bbox)},
                headers={"User-Agent": "route-gen-ai-interval-finder/0.1"},
                timeout=90)
            resp.raise_for_status()
            data = resp.json()
            break
        except (requests.RequestException, ValueError) as e:
            print(f"  overpass {url.split('/')[2]}: failed ({e})")
    if data is None:
        return []
    controls = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        kind = tags.get("highway") or ("level_crossing"
                                       if tags.get("railway") == "level_crossing"
                                       else None)
        weight = WEIGHTS.get(kind)
        if weight:
            controls.append((el["lat"], el["lon"], weight))
    return controls


def _project(lat0: float, lon0: float):
    """Local equirectangular meters projection around (lat0, lon0)."""
    kx = 111320.0 * math.cos(math.radians(lat0))

    def to_xy(lat, lon):
        return (lon - lon0) * kx, (lat - lat0) * 110540.0
    return to_xy


def controls_along(points, controls, tolerance_m: float = 40.0) -> list:
    """Map controls onto a polyline: sorted (cum_distance_m, weight) for each
    control within tolerance of the line. `points` are (lat, lon, ...)."""
    if not points or not controls:
        return []
    to_xy = _project(points[0][0], points[0][1])
    xy = [to_xy(p[0], p[1]) for p in points]
    cum = [0.0]
    for k in range(1, len(xy)):
        cum.append(cum[-1] + math.dist(xy[k - 1], xy[k]))

    hits = []
    for clat, clon, weight in controls:
        cx, cy = to_xy(clat, clon)
        best_d, best_pos = None, 0.0
        for k in range(len(xy) - 1):
            ax, ay = xy[k]
            bx, by = xy[k + 1]
            vx, vy = bx - ax, by - ay
            seg_len2 = vx * vx + vy * vy
            t = 0.0 if seg_len2 == 0 else max(
                0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / seg_len2))
            d = math.dist((cx, cy), (ax + t * vx, ay + t * vy))
            if best_d is None or d < best_d:
                best_d = d
                best_pos = cum[k] + t * math.sqrt(seg_len2)
        if best_d is not None and best_d <= tolerance_m:
            hits.append((best_pos, weight))
    hits.sort()
    return hits
