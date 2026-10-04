"""Is a long straight segment a road, or a hole in the track?

Geometry alone cannot answer this, and two earlier heuristics in this harness
proved it:

* a raw gap threshold flagged straight rural miles;
* requiring an off-axis turn at both ends still flagged a *grid jog* --
  south on a section road, half a mile west on a cross road, south again.
  In a square-section road grid both turns are 90 degrees and the connector
  has no intermediate vertex, which is exactly the shape of a splice.

So stop guessing and ask. Route between the two endpoints with the same
engine that produced the track: if a road connects them in roughly the
straight-line distance, the segment IS a road and the router simply emitted
no vertex along it. If the road distance is much longer, or there is no road
at all, the track jumped.

Cheap: only the handful of segments already flagged get a query, and answers
are cached on disk.
"""
from __future__ import annotations

import json
import os
from typing import Any, Sequence

from evals import geo

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cache", "gap_roads.json")

# A road between two points is never shorter than the straight line, and a
# real connector is close to it. Beyond this the "road" is a detour, which
# means the straight segment was not one.
ROAD_RATIO_MAX: float = 1.30


def _load() -> dict[str, Any]:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, CACHE_PATH)


def gap_is_road(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """{'road': bool, 'straight_m', 'road_m', 'ratio', 'why'}.

    Unknown (router unavailable) returns road=None so the caller can report
    the gap without asserting anything it did not establish.
    """
    straight = geo.haversine_m(a, b)
    key = f"{a[0]:.5f},{a[1]:.5f}->{b[0]:.5f},{b[1]:.5f}"
    cache = _load()
    if key in cache:
        return cache[key]

    out: dict[str, Any] = {"straight_m": round(straight, 1)}
    try:
        from routes.providers import BRouterProvider, brouter_reachable
        p = BRouterProvider()
        if not brouter_reachable(p.base_url):
            out.update(road=None, why="routing server unavailable")
            return out                       # not cached: try again later
        leg = p.route([(a[0], a[1]), (b[0], b[1])])
    except Exception as e:                   # noqa: BLE001 - recorded
        out.update(road=None, why=f"router error: {type(e).__name__}")
        return out
    if leg is None:
        out.update(road=False, road_m=None, ratio=None,
                   why="the router found no road between the two ends")
    else:
        road_m = float(leg["distance_m"])
        ratio = road_m / straight if straight else float("inf")
        out.update(road=ratio <= ROAD_RATIO_MAX, road_m=round(road_m, 1),
                   ratio=round(ratio, 2),
                   why=(f"road distance {road_m:.0f} m vs straight line "
                        f"{straight:.0f} m (x{ratio:.2f}); "
                        + ("a road runs along it"
                           if ratio <= ROAD_RATIO_MAX else
                           "the only road between the ends is a detour, so "
                           "the straight segment is not one")))
    _save({**cache, key: out})
    return out


def classify_gaps(points: Sequence[Sequence[float]],
                  gaps: Sequence[dict[str, float]]
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                             list[dict[str, Any]]]:
    """Split flagged gaps into (breaks, roads, unknown)."""
    breaks: list[dict[str, Any]] = []
    roads: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for g in gaps:
        i = int(g["index"])
        verdict = gap_is_road(points[i - 1], points[i])
        row = {**g, **verdict}
        if verdict.get("road") is True:
            roads.append(row)
        elif verdict.get("road") is False:
            breaks.append(row)
        else:
            unknown.append(row)
    return breaks, roads, unknown
