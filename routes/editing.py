"""Localized route editing: detour an existing route around a place,
keeping the rest untouched.

The riding loop this enables: generate -> ride/preview -> "find a way
around X" -> keep 95% of the route, splice a detour over the one bad spot.

Mechanics: find every contiguous stretch where the route passes within the
avoid-zone, back off a buffer on each side, ask the router for a fresh leg
between those points with a no-go circle over the zone, and splice.
"""
import math
from dataclasses import dataclass

from routes.elevation import track_ascent
from routes.overlap import repeated_fraction
from routes.spec import METERS_PER_MILE


@dataclass
class EditResult:
    points: list
    distance_m: float
    ascent_m: float
    overlap_frac: float
    detours: int
    removed_m: float   # route length replaced
    added_m: float     # detour length spliced in


def _dist_m(a, b) -> float:
    dy = (a[0] - b[0]) * 110540.0
    dx = (a[1] - b[1]) * 111320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _cum(points) -> list[float]:
    out = [0.0]
    for k in range(1, len(points)):
        out.append(out[-1] + _dist_m(points[k - 1], points[k]))
    return out


def route_via(points, target, provider,
              buffer_m: float = 1500.0) -> EditResult | None:
    """Reroute the section of `points` nearest to `target` (lat, lon) so it
    passes THROUGH the target — 'go down the commuter path instead'. The
    fresh leg runs A -> target -> B where A/B sit buffer_m up- and
    down-route of the closest approach; the target is protected from
    despurring (riding out-and-back onto a path tip can be the point)."""
    tlat, tlon = target
    cum = _cum(points)
    dists = [_dist_m(p, (tlat, tlon)) for p in points]
    i = min(range(len(points)), key=lambda k: dists[k])
    if dists[i] > 8000:
        print(f"  that place is {dists[i] / 1000:.1f} km from the route — "
              "too far to splice through; generate a fresh route instead")
        return None
    a = i
    while a > 0 and cum[i] - cum[a] < buffer_m:
        a -= 1
    b = i
    while b < len(points) - 1 and cum[b] - cum[i] < buffer_m:
        b += 1
    leg = provider.route([points[a][:2], (tlat, tlon), points[b][:2]],
                         protect=[(tlat, tlon)])
    if leg is None:
        print("  could not route through that place — leaving the route as is")
        return None
    new_points = points[:a + 1] + leg["points"] + points[b:]
    return EditResult(
        points=new_points,
        distance_m=_cum(new_points)[-1],
        ascent_m=track_ascent(new_points),
        overlap_frac=repeated_fraction(new_points),
        detours=1,
        removed_m=cum[b] - cum[a],
        added_m=leg["distance_m"],
    )


def detour_around(points, zone, provider,
                  buffer_m: float = 700.0) -> EditResult | None:
    """Reroute every part of `points` that enters `zone` (lat, lon, radius_m).
    Returns None when the route never touches the zone."""
    zlat, zlon, zr = zone
    cum = _cum(points)
    inside = [_dist_m(p, (zlat, zlon)) <= zr for p in points]
    if not any(inside):
        return None

    # contiguous contact runs, then widen each by the buffer and merge overlaps
    runs = []
    s = None
    for i, flag in enumerate(inside + [False]):
        if flag and s is None:
            s = i
        elif not flag and s is not None:
            runs.append((s, i - 1))
            s = None
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
    nogo = [(zlat, zlon, zr + 100.0)]
    for a, b in gaps:
        leg = provider.route([points[a][:2], points[b][:2]], avoid=nogo)
        if leg is None:
            print(f"  detour leg failed at {cum[a] / METERS_PER_MILE:.1f} mi — "
                  "keeping the original section")
            continue
        new_points.extend(points[cursor:a + 1])
        new_points.extend(leg["points"])
        cursor = b
        removed += cum[b] - cum[a]
        added += leg["distance_m"]
    new_points.extend(points[cursor:])

    return EditResult(
        points=new_points,
        distance_m=_cum(new_points)[-1],
        ascent_m=track_ascent(new_points),
        overlap_frac=repeated_fraction(new_points),
        detours=len(gaps),
        removed_m=removed,
        added_m=added,
    )
