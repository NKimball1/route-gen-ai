"""Avoid a road AS A ROAD, not as a point.

A named road is a line, and geocoding gives one point somewhere on it —
no-go circles around that point can guard entirely the wrong kilometer
(field case: 'avoid Whitney Way' circled a spot 1 km south of where the
route actually rode the road, twice). Instead: fetch the road's real
geometry from OSM, find where the route travels ALONG it, chain no-gos
down the road's shape, and verify by measuring on-road meters afterward.
"""
import math
import re

from routes.interruptions import bbox_around, query_overpass
from routes.spec import (METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ,
                         METERS_PER_MILE)

ROAD_QUERY = """[out:json][timeout:60];
way["highway"]["name"~"{name}",i]({bbox});
out geom;"""

ON_ROAD_M = 28.0  # within this of the centerline counts as riding the road
# shorter on-road stretches are mere crossings, not riding the road
MIN_RIDING_RUN_M = 60.0

ROAD_WORDS = re.compile(
    r"\b(road|rd|street|st|way|avenue|ave|drive|dr|lane|ln|"
    r"boulevard|blvd|parkway|pkwy|highway|hwy|route|pike|path|"
    r"trail|court|ct|circle|cir|terrace|ter|place|pl)\b", re.I)


def looks_like_road(name: str) -> bool:
    """Gate for road-as-line avoidance: only names that read as roads.
    A park named after a nearby road must not trigger road mode."""
    return bool(ROAD_WORDS.search(name.split(",")[0]))


def fetch_road(name: str, lat: float, lon: float,
               radius_m: float) -> list[list[tuple[float, float]]]:
    """Geometry of every way whose name matches (matches 'Whitney Way' to
    North/South Whitney Way too). Returns a list of polylines."""
    safe = re.sub(r"[^\w\s'-]", "", name).strip()
    if not safe:
        return []
    data = query_overpass(ROAD_QUERY.format(
        name=safe, bbox=bbox_around(lat, lon, radius_m)))
    if data is None:
        return []
    ways = []
    for el in data.get("elements", []):
        geom = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
        if len(geom) >= 2:
            ways.append(geom)
    return ways


def _seg_dist_m(p, a, b) -> float:
    """Meters from point p to segment a-b (local equirectangular)."""
    kx = METERS_PER_DEG_LON_EQ * math.cos(math.radians(a[0]))
    px, py = (p[1] - a[1]) * kx, (p[0] - a[0]) * METERS_PER_DEG_LAT
    bx, by = (b[1] - a[1]) * kx, (b[0] - a[0]) * METERS_PER_DEG_LAT
    seg2 = bx * bx + by * by
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
    return math.hypot(px - t * bx, py - t * by)


def dist_to_road(p, ways) -> float:
    best = float("inf")
    for way in ways:
        for a, b in zip(way, way[1:]):
            # cheap prefilter: skip far segments
            if abs(p[0] - a[0]) > 0.02 or abs(p[1] - a[1]) > 0.03:
                continue
            d = _seg_dist_m(p, a, b)
            if d < best:
                best = d
    return best


def on_road_meters(points, ways) -> float:
    """How much of the route rides along the road."""
    from routes.editing import _dist_m
    total = 0.0
    for a, b in zip(points, points[1:]):
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if dist_to_road(mid, ways) <= ON_ROAD_M:
            total += _dist_m(a, b)
    return total


def road_nogos(ways, center, span_m: float,
               keep_clear: list, r_m: float = 110.0,
               cap: int = 36) -> list[tuple[float, float, float]]:
    """No-go circles chained along the road near `center`, skipping any
    that would swallow a keep_clear point (the leg's own endpoints)."""
    from routes.editing import _dist_m
    nogos = []
    for way in ways:
        for pt in way:
            if _dist_m(pt, center) > span_m:
                continue
            if any(_dist_m(pt, kc) < r_m + 140.0 for kc in keep_clear):
                continue
            if any(_dist_m(pt, (n[0], n[1])) < r_m for n in nogos):
                continue
            nogos.append((pt[0], pt[1], r_m))
            if len(nogos) >= cap:
                return nogos
    return nogos


def detour_around_road(points, ways, provider,
                       buffer_m: float = 700.0):
    """Reroute every stretch where the route rides along the road.
    Returns an EditResult (failed sections counted) or None when the
    route never rides the road."""
    from routes.editing import (_cum, _dist_m, _result)

    cum = _cum(points)
    riding = [dist_to_road(p, ways) <= ON_ROAD_M for p in points]
    if not any(riding):
        return None

    runs, s = [], None
    for i, flag in enumerate(riding + [False]):
        if flag and s is None:
            s = i
        elif not flag and s is not None:
            if cum[i - 1] - cum[s] >= MIN_RIDING_RUN_M:
                runs.append((s, i - 1))
            s = None
    if not runs:
        return None

    gaps = []
    for s, e in runs:
        a = s
        while a > 0 and cum[s] - cum[a] < buffer_m:
            a -= 1
        b = e
        while b < len(points) - 1 and cum[b] - cum[e] < buffer_m:
            b += 1
        if gaps and a <= gaps[-1][1]:
            gaps[-1] = (gaps[-1][0], b)
        else:
            gaps.append((a, b))

    new_points = []
    cursor = 0
    removed = added = 0.0
    failed = 0
    fail_reason = ""
    for a, b in gaps:
        mid = ((points[a][0] + points[b][0]) / 2,
               (points[a][1] + points[b][1]) / 2)
        span = max(_dist_m(points[a], points[b]), cum[b] - cum[a]) / 2 + 1500.0
        nogos = road_nogos(ways, mid, span,
                           keep_clear=[points[a][:2], points[b][:2]])
        leg = provider.route([points[a][:2], points[b][:2]],
                             avoid=nogos) if nogos else None
        if leg is None:
            failed += 1
            fail_reason = "no way around that stretch"
            print(f"  couldn't reroute the stretch at "
                  f"{cum[a] / METERS_PER_MILE:.1f} mi — keeping it")
            continue
        new_points.extend(points[cursor:a + 1])
        new_points.extend(leg["points"])
        cursor = b
        removed += cum[b] - cum[a]
        added += leg["distance_m"]
    new_points.extend(points[cursor:])

    result = _result(new_points, removed, added,
                     detours=len(gaps) - failed)
    result.failed_detours = failed
    result.fail_reason = fail_reason
    return result
