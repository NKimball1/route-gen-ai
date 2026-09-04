"""Remove out-and-back spur artifacts from a route track.

Loop synthesis places via-points geometrically; when one lands just off the
natural path, the router detours to touch it and retraces the same road back.
That shows up in the track as a palindrome: points after the turnaround tip
mirror the points before it. We detect those mirrors and excise them, keeping
the junction point, so the track stays continuous.

Provider-agnostic on purpose: works on any polyline, not just BRouter's.
"""
import math

EARTH_RADIUS_M = 6371000.0


def _hav_m(a, b) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _leg_len(points) -> float:
    return sum(_hav_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def _naive_ascent(points) -> float:
    ascent = 0.0
    for i in range(len(points) - 1):
        e0, e1 = points[i][2], points[i + 1][2]
        if e0 is not None and e1 is not None and e1 > e0:
            ascent += e1 - e0
    return ascent


def despur(points, tolerance_m: float = 10.0, min_spur_m: float = 40.0
           ) -> tuple[list, float, float]:
    """Return (cleaned points, removed distance m, removed ascent m)."""
    pts = list(points)
    removed_dist = removed_ascent = 0.0
    i = 1
    while i < len(pts) - 1:
        depth = 0
        while (i - 1 - depth >= 0 and i + 1 + depth < len(pts)
               and _hav_m(pts[i - 1 - depth], pts[i + 1 + depth]) < tolerance_m):
            depth += 1
        if depth:
            spur = pts[i - depth:i + depth + 1]
            if _leg_len(spur[:depth + 1]) >= min_spur_m:
                removed_dist += _leg_len(spur)
                removed_ascent += _naive_ascent(spur)
                # keep the junction point at i-depth, drop through i+depth
                pts = pts[:i - depth + 1] + pts[i + depth + 1:]
                i = max(1, i - depth)
                continue
        i += 1
    return pts, removed_dist, removed_ascent


def _resample(points, step_m: float = 25.0) -> list:
    """Uniformly spaced copy of the polyline (linear interpolation)."""
    out = [tuple(points[0][:3])]
    prev = points[0]
    carry = 0.0
    for p in points[1:]:
        seg = _hav_m(prev, p)
        while seg > 0 and carry + seg >= step_m:
            t = (step_m - carry) / seg
            lat = prev[0] + (p[0] - prev[0]) * t
            lon = prev[1] + (p[1] - prev[1]) * t
            ele = (prev[2] + (p[2] - prev[2]) * t
                   if prev[2] is not None and p[2] is not None else None)
            newp = (lat, lon, ele)
            out.append(newp)
            seg -= step_m - carry
            carry = 0.0
            prev = newp
        carry += seg
        prev = p
    if out[-1][:2] != tuple(points[-1][:2]):
        out.append(tuple(points[-1][:3]))
    return out


def corridor_despur(points, tolerance_m: float = 32.0, min_spur_m: float = 200.0
                    ) -> tuple[list, float, float]:
    """Catch tendrils the exact pass misses: the route goes out and comes
    back within ~tolerance of the same corridor, but on not-quite-identical
    geometry (parallel path, offset lanes). Works on a uniformly resampled
    copy so out/back samples align; returns the resampled track when it
    trimmed something, the original untouched otherwise.

    Caution: sustained switchback legs closer than tolerance would falsely
    match; min_spur_m and the corridor width are chosen for open-road
    riding, not alpine hairpin stacks.
    """
    rs = _resample(points)
    cleaned, removed_dist, removed_ascent = despur(
        rs, tolerance_m=tolerance_m, min_spur_m=min_spur_m)
    if removed_dist == 0.0:
        return list(points), 0.0, 0.0
    return cleaned, removed_dist, removed_ascent
