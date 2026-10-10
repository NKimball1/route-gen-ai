"""Traffic-interruption data from OpenStreetMap via the Overpass API.

Stop signs, traffic signals, yields, and level crossings are tagged nodes in
OSM. One Overpass query fetches every control in the search area; counting
them along a candidate stretch is then pure local math. Free, no API key.

Whose stop is it? A stop sign mapped on a SIDE street sits ~10-20 m off
the main road, so a wide match counted it against riders who have the
right of way. Measured on the Madison-area map (2026-10-07), side-street
signs within 40 m of a road outnumbered the road's own 5,037 to 2,082.
Stops, yields and rail crossings now count only on the routed line itself;
signals, which stop everyone, keep a wide reach.

Road crossings (highway=crossing) stop a rider on a trail, signed or not,
but the same tag marks crosswalks across a road: they count only where the
route rides a path through them (see trail_crossing and the Spoke).
"""
import glob
import hashlib
import json
import math
import os
import time
from typing import Any, Callable, Sequence

import requests
from routes.policy import (CONTROL_ON_ROUTE_M, SIGNAL_REACH_M,
                           TRAIL_CROSSING_WEIGHT)
from routes.spec import METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ

# (lat, lon, weight, reach_m, path_only): one traffic control, how badly it
# breaks an effort, how close to the route it must be to apply to the
# rider, and whether it applies only where the rider is on a path (a road
# crossing: see trail_crossing). Hand-built 3-tuples (tests, older callers)
# use controls_along's tolerance and apply everywhere.
Control = tuple[float, float, float, float, bool]
# (meters along the polyline, weight): a control mapped onto a route
ControlHit = tuple[float, float]

OVERPASS_URLS: list[str] = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# A mirror that ANSWERS "busy" (rate limited, gateway timeout) is alive and
# often serves the same query moments later; one that never answers is
# down, and waiting on it again would only double an outage.
OVERPASS_BUSY_HTTP: tuple[int, ...] = (429, 502, 503, 504)
OVERPASS_RETRY_WAIT_S: float = 5.0

# How badly each control type breaks an interval effort.
WEIGHTS: dict[str, float] = {
    "traffic_signals": 1.5,
    "stop": 1.0,
    "level_crossing": 1.0,
    "give_way": 0.4,
}

# Nodes within this distance are one intersection.
CLUSTER_M: float = 35.0
# Cached control lists carry each control's reach and whether it applies
# only on a path; the "3" keeps lists cached before trail crossings existed
# (no crossings, 4-element controls) from being read back.
CONTROLS_PREFIX: str = "controls3_"

QUERY: str = """[out:json][timeout:60];
(
  node["highway"~"^(stop|give_way|traffic_signals|crossing)$"]({bbox});
  node["railway"="level_crossing"]({bbox});
);
out;"""


def trail_crossing(lat: float, lon: float) -> Control:
    """A highway=crossing node. The same tag marks a trail's crossing of a
    road and a pedestrian crosswalk on a road: it interrupts a rider ON
    the path (the Spoke checks where its router line rides a path) and
    never a rider on the road being crossed."""
    return (lat, lon, TRAIL_CROSSING_WEIGHT, CONTROL_ON_ROUTE_M, True)


def is_path_only(control: Sequence[float]) -> bool:
    return len(control) > 4 and bool(control[4])


def bbox_around(lat: float, lon: float, radius_m: float) -> str:
    dlat = radius_m / METERS_PER_DEG_LAT
    dlon = radius_m / (METERS_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
    return f"{lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon}"


# ---- disk cache ----
# The public Overpass servers time out often (four searches in one week lost
# their stop counts to it), and stop signs change on a scale of years, not
# days. Answers are kept on disk: reused while fresh, and when every mirror
# is down the last good copy -- of any age -- beats "unknown".
CACHE_DIR: str = os.environ.get("ROUTEGEN_OVERPASS_CACHE",
                                os.path.join("output", "cache", "overpass"))
CACHE_FRESH_S: float = 7 * 86400.0

BBox4 = tuple[float, float, float, float]   # (south, west, north, east)


def _read_cache(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        return entry if isinstance(entry, dict) and "ts" in entry else None
    except (OSError, ValueError):
        return None


def _write_cache(path: str, entry: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry, f)
        os.replace(tmp, path)   # never leave a half-written cache file
    except OSError:
        pass   # a cache that can't be written is just a cache miss


def _age(entry: dict[str, Any]) -> str:
    days = (time.time() - float(entry["ts"])) / 86400.0
    return f"{days * 24:.0f} h" if days < 1 else f"{days:.0f} days"


def _post_one(url: str, query: str) -> tuple[dict[str, Any] | None, bool]:
    """(answer, busy): busy means the mirror replied that it is overloaded."""
    try:
        resp = requests.post(
            url, data={"data": query},
            headers={"User-Agent": "route-gen-ai/0.1"},
            timeout=90)
        if resp.status_code in OVERPASS_BUSY_HTTP:
            print(f"  overpass {url.split('/')[2]}: busy (HTTP {resp.status_code})")
            return None, True
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data, False
    except (requests.RequestException, ValueError) as e:
        print(f"  overpass {url.split('/')[2]}: failed ({e})")
        return None, False


def _post_overpass(query: str) -> dict[str, Any] | None:
    """POST an Overpass QL query, trying mirrors. None if all fail.

    Mirrors that answered "busy" get one more try after a short pause."""
    busy: list[str] = []
    for url in OVERPASS_URLS:
        data, is_busy = _post_one(url, query)
        if data is not None:
            return data
        if is_busy:
            busy.append(url)
    for url in busy:
        time.sleep(OVERPASS_RETRY_WAIT_S)
        data, _ = _post_one(url, query)
        if data is not None:
            return data
    return None


def query_overpass(query: str) -> dict | None:
    """Run an Overpass query through the disk cache: a fresh cached answer
    skips the network; if every mirror fails, a stale one is used."""
    key = hashlib.sha256(query.encode()).hexdigest()[:24]
    path = os.path.join(CACHE_DIR, f"q_{key}.json")
    cached = _read_cache(path)
    if cached is not None and time.time() - float(cached["ts"]) < CACHE_FRESH_S:
        return cached["data"]
    data = _post_overpass(query)
    if data is not None:
        _write_cache(path, {"ts": time.time(), "data": data})
        return data
    if cached is not None:
        print(f"  overpass down -- using cached map data from {_age(cached)} ago")
        return cached["data"]
    return None


def _bbox4(lat: float, lon: float, radius_m: float) -> BBox4:
    s, w, n, e = (float(v) for v in bbox_around(lat, lon, radius_m).split(","))
    return s, w, n, e


def _covering_controls(want: BBox4, fresh_only: bool) -> tuple[list[Control], dict[str, Any]] | None:
    """Controls from any cached fetch whose area contains `want` (freshest
    first), trimmed to `want`. Searches ask for slightly different areas
    every time; one big fetch around home should answer all of them."""
    best: dict[str, Any] | None = None
    for path in glob.glob(os.path.join(CACHE_DIR, CONTROLS_PREFIX + "*.json")):
        entry = _read_cache(path)
        if entry is None or "bbox" not in entry:
            continue
        s, w, n, e = entry["bbox"]
        if not (s <= want[0] and w <= want[1] and n >= want[2] and e >= want[3]):
            continue
        if fresh_only and time.time() - float(entry["ts"]) >= CACHE_FRESH_S:
            continue
        if best is None or float(entry["ts"]) > float(best["ts"]):
            best = entry
    if best is None:
        return None
    inside = [(la, lo, wt, reach, bool(path_only))
              for la, lo, wt, reach, path_only in best["controls"]
              if want[0] <= la <= want[2] and want[1] <= lo <= want[3]]
    return inside, best


def fetch_controls(lat: float, lon: float,
                   radius_m: float) -> list[Control] | None:
    """All traffic controls within radius of (lat, lon), road crossings
    included (they apply only where a route rides a path).
    None when Overpass did not answer and nothing cached covers the area --
    'no data' and 'no controls' are different facts, and a result table
    must not show the first as 0."""
    want = _bbox4(lat, lon, radius_m)
    hit = _covering_controls(want, fresh_only=True)
    if hit is not None:
        return hit[0]
    data = query_overpass(QUERY.format(bbox=bbox_around(lat, lon, radius_m)))
    if data is None:
        stale = _covering_controls(want, fresh_only=False)
        if stale is not None:
            print(f"  overpass down -- using traffic controls cached "
                  f"{_age(stale[1])} ago")
            return stale[0]
        return None
    signals: list[Control] = []
    on_route: list[Control] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("highway") == "crossing":
            if tags.get("crossing") != "no":     # "no": crossing is not allowed here
                on_route.append(trail_crossing(el["lat"], el["lon"]))
            continue
        kind = tags.get("highway") or ("level_crossing"
                                       if tags.get("railway") == "level_crossing"
                                       else None)
        weight = WEIGHTS.get(kind) if kind else None
        if not weight:
            continue
        if kind == "traffic_signals":
            signals.append((el["lat"], el["lon"], weight, SIGNAL_REACH_M, False))
        else:
            on_route.append((el["lat"], el["lon"], weight, CONTROL_ON_ROUTE_M, False))
    # Only signal heads cluster: merging a side-street stop with the main
    # road's own would move it off the line and lose it.
    clustered = _cluster(signals) + on_route
    key = hashlib.sha256(repr(want).encode()).hexdigest()[:24]
    _write_cache(os.path.join(CACHE_DIR, f"{CONTROLS_PREFIX}{key}.json"),
                 {"ts": time.time(), "bbox": list(want), "controls": clustered})
    return clustered


def _cluster(controls: Sequence[Sequence[float]],
             radius_m: float = CLUSTER_M) -> list[Control]:
    """Merge control nodes within radius into one (a signalized intersection
    is typically mapped as one node per corner — that's one light, not four)."""
    merged: list[Control] = []
    for c in controls:
        lat, lon, weight = c[0], c[1], c[2]
        reach = c[3] if len(c) > 3 else SIGNAL_REACH_M
        for i, (mlat, mlon, mweight, mreach, _) in enumerate(merged):
            dy = (lat - mlat) * METERS_PER_DEG_LAT
            dx = (lon - mlon) * METERS_PER_DEG_LON_EQ * math.cos(math.radians(lat))
            if dx * dx + dy * dy <= radius_m * radius_m:
                merged[i] = (mlat, mlon, max(mweight, weight), max(mreach, reach), False)
                break
        else:
            merged.append((lat, lon, weight, reach, False))
    return merged


def _project(lat0: float,
             lon0: float) -> Callable[[float, float], tuple[float, float]]:
    """Local equirectangular meters projection around (lat0, lon0)."""
    kx = METERS_PER_DEG_LON_EQ * math.cos(math.radians(lat0))

    def to_xy(lat: float, lon: float) -> tuple[float, float]:
        return (lon - lon0) * kx, (lat - lat0) * METERS_PER_DEG_LAT
    return to_xy


def controls_along(points: Sequence[tuple[float, ...]],
                   controls: Sequence[Sequence[float]],
                   tolerance_m: float = SIGNAL_REACH_M) -> list[ControlHit]:
    """Map controls onto a polyline: sorted (cum_distance_m, weight) for each
    control within its reach of the line (a control without its own reach
    uses tolerance_m). Hits within CLUSTER_M of each other along the line are
    one intersection (an all-way stop has a sign on both approaches of our
    road), keeping the heavier weight. `points` are (lat, lon, ...); when a
    4th element is present it is taken as that point's cumulative road
    distance, keeping positions comparable to the caller's own cum values
    (chord sums drift hundreds of meters behind road distance over ~10 km)."""
    if not points or not controls:
        return []
    to_xy = _project(points[0][0], points[0][1])
    xy = [to_xy(p[0], p[1]) for p in points]
    if len(points[0]) > 3:
        cum = [p[3] for p in points]
    else:
        cum = [0.0]
        for k in range(1, len(xy)):
            cum.append(cum[-1] + math.dist(xy[k - 1], xy[k]))

    hits: list[ControlHit] = []
    for c in controls:
        clat, clon, weight = c[0], c[1], c[2]
        reach = c[3] if len(c) > 3 else tolerance_m
        cx, cy = to_xy(clat, clon)
        best_d: float | None = None
        best_pos = 0.0
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
        if best_d is not None and best_d <= reach:
            hits.append((best_pos, weight))
    hits.sort()
    merged: list[ControlHit] = []
    for pos, weight in hits:
        if merged and pos - merged[-1][0] <= CLUSTER_M:
            merged[-1] = (merged[-1][0], max(merged[-1][1], weight))
        else:
            merged.append((pos, weight))
    return merged
