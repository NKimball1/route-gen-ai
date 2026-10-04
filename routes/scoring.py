"""Deterministic candidate filtering and ranking (no LLM judgment — ADR 0004)."""
from routes.spec import METERS_PER_MILE, RouteCandidate, RouteSpec
from routes.geometry import waypoints_in_order, enters_circle
from routes.policy import (AVOID_ROAD_TOLERANCE_M,
                           MAX_LOOP_REPEAT_FRACTION, MAX_MAJOR_ROAD_M)
from routes.road_avoid import on_road_meters


def rank(spec: RouteSpec, candidates: list[RouteCandidate], *,
         allowed_shapes: set[str] | None = None
         ) -> tuple[list[RouteCandidate], list[tuple[RouteCandidate, str]]]:
    """Split candidates into (ranked keepers, rejects with reasons)."""
    keepers, rejects = [], []
    shapes = allowed_shapes or ({"loop", "outback"} if spec.shape == "both" else {spec.shape})
    for c in candidates:
        error = abs(c.distance_m - spec.distance_m) / spec.distance_m
        if c.shape not in shapes:
            rejects.append((c, "does not have the requested route shape"))
        elif spec.via and not waypoints_in_order(c.points, spec.via, spec.via_tolerances()):
            rejects.append((c, "does not visit every waypoint in the requested order"))
        elif any(on_road_meters(c.points, road.ways) > AVOID_ROAD_TOLERANCE_M
                 for road in spec.avoid_roads):
            rejects.append((c, "still rides a road the request excludes"))
        elif any(enters_circle(c.points, (la, lo), radius)
                             for la, lo, radius in (spec.avoid if spec.avoid_areas is None else spec.avoid_areas)):
            rejects.append((c, "still enters an excluded area"))
        elif error > spec.distance_tolerance:
            rejects.append((c, f"distance {c.distance_mi:.1f} mi outside "
                               f"±{spec.distance_tolerance:.0%} of target"))
        elif spec.max_ascent_m is not None and c.ascent_m > spec.max_ascent_m:
            rejects.append((c, f"ascent {c.ascent_ft:.0f} ft exceeds cap"))
        elif c.shape == "loop" and c.overlap_frac > MAX_LOOP_REPEAT_FRACTION:
            rejects.append((c, f"{c.overlap_frac:.0%} of the route rides the "
                               f"same road twice"))
        elif c.major_m is not None and c.major_m > MAX_MAJOR_ROAD_M:
            rejects.append((c, f"{c.major_m / METERS_PER_MILE:.1f} mi on major "
                               f"highways (trunk/primary)"))
        else:
            if c.major_m is None and not c.warnings:
                c.warnings.append("Major-road exposure is unknown for this routing provider.")
            keepers.append(c)

    if spec.maximize_ascent:
        keepers.sort(key=lambda c: c.ascent_m, reverse=True)
    elif spec.minimize_ascent:
        keepers.sort(key=lambda c: c.ascent_m)
    elif spec.via:
        # A loop that flows through the via places organically beats one that
        # anchors to their exact coordinates, regardless of distance fit.
        keepers.sort(key=lambda c: (not c.natural,
                                    abs(c.distance_m - spec.distance_m)))
    else:
        keepers.sort(key=lambda c: abs(c.distance_m - spec.distance_m))
    return keepers, rejects
