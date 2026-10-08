"""Deterministic candidate filtering and ranking (no LLM judgment — ADR 0004)."""
from collections import Counter
from typing import Sequence

from routes.spec import METERS_PER_MILE, RouteCandidate, RouteSpec
from routes.geometry import waypoints_in_order, enters_circle
from routes.policy import (AVOID_ROAD_TOLERANCE_M,
                           MAX_LOOP_REPEAT_FRACTION, MAX_MAJOR_ROAD_M)
from routes.road_avoid import on_road_meters


# Reject reasons start with these, so a summary can tell them apart.
ROAD_REJECT = "still rides {road}, which the request excludes"
NOTHING_FITS = "No route met the constraints - try a looser target or different distance."
_KINDS = (("still rides ", "rode an excluded road"),
          ("still enters ", "entered an excluded area"),
          ("does not visit ", "missed a requested place"),
          ("does not have ", "had the wrong shape"),
          ("distance ", "missed the distance"),
          ("ascent ", "climbed over the limit"),
          ("% of the route", "repeated too much road"),
          (" on major ", "used major highways"))


def _kind(reason: str) -> str:
    return next((label for key, label in _KINDS if key in reason), "other")


def why_nothing_fits(rejects: Sequence[tuple[RouteCandidate, str]]) -> str:
    """The sentence a rider sees when every candidate was rejected.

    "Try a looser target" is the wrong advice when the target was fine and
    one constraint ruled out everything: avoiding the road the start sits
    on rejects every candidate, and no distance change will help."""
    if not rejects:
        return NOTHING_FITS
    kinds = Counter(_kind(reason) for _, reason in rejects)
    if set(kinds) == {"rode an excluded road"}:
        prefix, suffix = ROAD_REJECT.split("{road}")
        roads = sorted({r.removeprefix(prefix).removesuffix(suffix) for _, r in rejects})
        return (f"Every route found rides {' / '.join(roads)}, which you asked to avoid - "
                "usually because the start is on or right next to it. Start a short way "
                "off it, or drop that avoid.")
    if set(kinds) == {"entered an excluded area"}:
        return ("Every route found passes through the area you asked to avoid. "
                "Try a smaller radius, or a start farther from it.")
    if set(kinds) == {"missed a requested place"}:
        return ("No route reached every place you named, in that order. "
                "Try fewer places, a different order, or a longer ride.")
    tally = ", ".join(f"{n} {kind}" for kind, n in kinds.most_common())
    return f"{NOTHING_FITS} Of {len(rejects)} candidates: {tally}."


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
            rejects.append((c, ROAD_REJECT.format(road=next(
                road.name for road in spec.avoid_roads
                if on_road_meters(c.points, road.ways) > AVOID_ROAD_TOLERANCE_M))))
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
