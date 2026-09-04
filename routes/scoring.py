"""Deterministic candidate filtering and ranking (no LLM judgment — ADR 0004)."""
from routes.spec import RouteCandidate, RouteSpec


def rank(spec: RouteSpec, candidates: list[RouteCandidate]
         ) -> tuple[list[RouteCandidate], list[tuple[RouteCandidate, str]]]:
    """Split candidates into (ranked keepers, rejects with reasons)."""
    keepers, rejects = [], []
    for c in candidates:
        error = abs(c.distance_m - spec.distance_m) / spec.distance_m
        if error > spec.distance_tolerance:
            rejects.append((c, f"distance {c.distance_mi:.1f} mi outside "
                               f"±{spec.distance_tolerance:.0%} of target"))
        elif spec.max_ascent_m is not None and c.ascent_m > spec.max_ascent_m:
            rejects.append((c, f"ascent {c.ascent_ft:.0f} ft exceeds cap"))
        else:
            keepers.append(c)

    if spec.maximize_ascent:
        keepers.sort(key=lambda c: c.ascent_m, reverse=True)
    elif spec.minimize_ascent:
        keepers.sort(key=lambda c: c.ascent_m)
    else:
        keepers.sort(key=lambda c: abs(c.distance_m - spec.distance_m))
    return keepers, rejects
