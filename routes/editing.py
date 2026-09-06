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


def _result(points, removed_m, added_m, detours=1) -> EditResult:
    return EditResult(
        points=points, distance_m=_cum(points)[-1],
        ascent_m=track_ascent(points),
        overlap_frac=repeated_fraction(points),
        detours=detours, removed_m=removed_m, added_m=added_m)


def extend_route(points, add_m: float, provider,
                 anchor_span_m: float = 1500.0) -> EditResult | None:
    """Make the route ~add_m longer by bowing one section outward — the
    same trick loop synthesis uses, applied to an existing route. Tries a
    few spots/sides and keeps the best distance fit."""
    from routes.providers import _bearing, _destination

    cum = _cum(points)
    total = cum[-1]
    best = None
    for frac, side in ((0.5, 1), (0.5, -1), (0.32, 1), (0.68, -1)):
        i = next(k for k in range(len(points)) if cum[k] >= frac * total)
        a = next(k for k in range(i, -1, -1)
                 if cum[i] - cum[k] >= anchor_span_m or k == 0)
        b = next(k for k in range(i, len(points))
                 if cum[k] - cum[i] >= anchor_span_m or k == len(points) - 1)
        if b - a < 2:
            continue
        bearing = _bearing(points[a][:2], points[b][:2])
        mid = ((points[a][0] + points[b][0]) / 2,
               (points[a][1] + points[b][1]) / 2)
        r = add_m / 2 / 1.2
        for _ in range(3):
            ext = _destination(mid[0], mid[1], bearing + 90 * side, r)
            leg = provider.route([points[a][:2], ext, points[b][:2]])
            if leg is None:
                break
            new_pts = points[:a + 1] + leg["points"] + points[b:]
            gained = _cum(new_pts)[-1] - total
            if gained <= 0:
                break
            cand = (abs(gained - add_m),
                    _result(new_pts, cum[b] - cum[a], leg["distance_m"]))
            if best is None or cand[0] < best[0]:
                best = cand
            if abs(gained - add_m) <= 0.15 * add_m:
                break
            r *= max(0.3, min(3.0, add_m / gained))
    return best[1] if best else None


def shorten_route(points, cut_m: float, provider) -> EditResult | None:
    """Make the route ~cut_m shorter by bridging one section directly.
    Samples cut positions along the middle of the route, keeps the bridge
    whose result lands nearest the target length."""
    cum = _cum(points)
    total = cum[-1]
    target = total - cut_m
    if target < 3000:
        print("  that would leave almost no ride — not shortening")
        return None
    best = None
    n = len(points)
    starts = [next(k for k in range(n) if cum[k] >= f * total)
              for f in (0.1, 0.22, 0.34, 0.46, 0.58, 0.7)]
    for i in starts:
        # walk forward looking for the cut that saves ~cut_m; if the route
        # can't give that much from this anchor, keep the best partial —
        # "10 miles shorter" on a route that can only lose 6 should yield
        # the 6 with a note, not a refusal
        j = i
        best_j, best_saved = None, 0.0
        while j < n - 1 and cum[j] < 0.9 * total:
            j += 1
            saved = (cum[j] - cum[i]) - 1.25 * _dist_m(points[i], points[j])
            if saved > best_saved:
                best_j, best_saved = j, saved
            if saved >= cut_m:
                break
        if best_j is None or best_saved < max(0.3 * cut_m, 400.0):
            continue
        j = best_j
        leg = provider.route([points[i][:2], points[j][:2]])
        if leg is None:
            continue
        new_pts = points[:i + 1] + leg["points"] + points[j:]
        new_total = _cum(new_pts)[-1]
        if new_total >= total:
            continue
        cand = (abs(new_total - target),
                _result(new_pts, cum[j] - cum[i], leg["distance_m"]))
        if best is None or cand[0] < best[0]:
            best = cand
    if best is None:
        return None
    achieved = total - best[1].distance_m
    if achieved < 0.85 * cut_m:
        print(f"  could only shorten by ~{achieved / 1609.344:.1f} mi "
              f"(asked ~{cut_m / 1609.344:.1f}) — the route has no bigger "
              "cuttable detour")
    return best[1]


def _is_loop(points, tolerance_m: float = 250.0) -> bool:
    return _dist_m(points[0], points[-1]) <= tolerance_m


def move_endpoint(points, target, provider,
                  at: str = "end") -> EditResult | None:
    """Make the ride start or end at target.

    Join the route where it passes NEAREST the target — a fixed anchor
    near the old start sent the new leg riding along the route's own
    corridor to reach it, re-riding the same stretch (field-reported
    backtracking). For a closed loop, rotate it so the ride begins/ends at
    the closest-approach point, then add one clean connecting leg; for an
    open route, drop the stretch before/after the join."""
    cum = _cum(points)
    total = cum[-1]

    if _is_loop(points):
        # rotate the loop so the join point is the seam
        j = min(range(len(points)), key=lambda k: _dist_m(points[k], target))
        rotated = points[j:] + points[1:j + 1]
        if at == "start":
            leg = provider.route([target, rotated[0][:2]])
            if leg is None:
                return None
            new_pts = leg["points"] + rotated
        else:
            leg = provider.route([rotated[-1][:2], target])
            if leg is None:
                return None
            new_pts = rotated + leg["points"]
        return _result(new_pts, 0.0, leg["distance_m"])

    if at == "start":
        # join at the closest approach within the first 60% of the ride
        limit = next(k for k in range(len(points)) if cum[k] >= 0.6 * total)
        j = min(range(limit + 1), key=lambda k: _dist_m(points[k], target))
        j = max(j, 1)
        leg = provider.route([target, points[j][:2]])
        if leg is None:
            return None
        return _result(leg["points"] + points[j:], cum[j], leg["distance_m"])

    lo = next(k for k in range(len(points)) if cum[k] >= 0.4 * total)
    j = min(range(lo, len(points)), key=lambda k: _dist_m(points[k], target))
    j = min(j, len(points) - 2)
    leg = provider.route([points[j][:2], target])
    if leg is None:
        return None
    return _result(points[:j + 1] + leg["points"], total - cum[j],
                   leg["distance_m"])


def connect_from(points, addr, provider,
                 with_return: bool = False) -> EditResult | None:
    """Prepend a leg from addr to the route's start ('ride there from my
    place'); optionally also append the leg home from the route's end."""
    leg_out = provider.route([addr, points[0][:2]])
    if leg_out is None:
        return None
    new_pts = leg_out["points"] + points
    added = leg_out["distance_m"]
    if with_return:
        leg_back = provider.route([points[-1][:2], addr])
        if leg_back is None:
            print("  could not route the return leg — added the outbound only")
        else:
            new_pts = new_pts + leg_back["points"]
            added += leg_back["distance_m"]
    return _result(new_pts, 0.0, added, detours=2 if with_return else 1)


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
