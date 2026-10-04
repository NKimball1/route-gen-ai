"""Polyline measurements shared by constraint validation and routing."""
import math
from typing import Sequence

from routes.despur import _hav_m
from routes.spec import Coord, LatLon, METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ


def segment_projection(point: Coord, start: Coord, end: Coord) -> tuple[float, float]:
    """Distance to a segment and the clamped fraction along it."""
    scale_x = METERS_PER_DEG_LON_EQ * math.cos(math.radians(start[0]))
    px = (point[1] - start[1]) * scale_x
    py = (point[0] - start[0]) * METERS_PER_DEG_LAT
    dx = (end[1] - start[1]) * scale_x
    dy = (end[0] - start[0]) * METERS_PER_DEG_LAT
    length2 = dx * dx + dy * dy
    fraction = max(0.0, min(1.0, (px * dx + py * dy) / length2)) if length2 else 0.0
    return math.hypot(px - fraction * dx, py - fraction * dy), fraction


def waypoints_in_order(points: Sequence[Coord], targets: Sequence[LatLon],
                       tolerance_m: float | Sequence[float]) -> bool:
    """tolerance_m is one distance for every target, or one per target."""
    tolerances = ([tolerance_m] * len(targets) if isinstance(tolerance_m, (int, float))
                  else list(tolerance_m))
    previous = 0.0
    for target, tolerance in zip(targets, tolerances, strict=True):
        cum = 0.0
        hits: list[tuple[float, float]] = []
        for a, b in zip(points, points[1:]):
            length = _hav_m(a, b)
            distance, fraction = segment_projection(target, a, b)
            position = cum + length * fraction
            if position >= previous and distance <= tolerance:
                hits.append((distance, position))
            cum += length
        if not hits:
            return False
        # Earliest valid encounter keeps future waypoints reachable even
        # when the route visits the same place twice.
        previous = min(position for _, position in hits)
    return True


def enters_circle(points: Sequence[Coord], center: LatLon, radius_m: float) -> bool:
    return any(segment_projection(center, a, b)[0] < radius_m
               for a, b in zip(points, points[1:]))
