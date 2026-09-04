"""Repeated-road detection: what fraction of a route rides the same road twice.

Despurring catches immediate turnarounds; this catches the 'lollipop stick' —
the same road traversed out and back with a big loop in between, which no
contiguous-mirror detector can see. Used to reject loop candidates that
pretend to be loops.
"""
import math

CELL_M = 35.0        # grid cell size: two passes on one road share cells
STEP_M = 60.0        # sampling step along the route
MIN_SEPARATION = 8   # samples apart before a revisit counts (not just slow curves)


def _hav_m(a, b) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def repeated_fraction(points) -> float:
    """Fraction of the route's length spent on cells already visited earlier
    (ignoring the immediate neighborhood). points are (lat, lon, ...)."""
    if len(points) < 3:
        return 0.0
    # resample to ~STEP_M spacing
    samples = [points[0]]
    carry = 0.0
    for k in range(1, len(points)):
        carry += _hav_m(points[k - 1], points[k])
        if carry >= STEP_M:
            samples.append(points[k])
            carry = 0.0
    if len(samples) < MIN_SEPARATION + 2:
        return 0.0

    lat0 = samples[0][0]
    kx = 111320.0 * math.cos(math.radians(lat0))

    def cell(p):
        return (int(p[0] * 110540.0 / CELL_M), int(p[1] * kx / CELL_M))

    seen: dict = {}   # cell -> first sample index
    repeated = 0
    for i, p in enumerate(samples):
        # a pass covers its cell and neighbors (tolerates GPS-scale offsets)
        c = cell(p)
        hit = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                j = seen.get((c[0] + dx, c[1] + dy))
                if j is not None and i - j >= MIN_SEPARATION:
                    hit = j
                    break
            if hit is not None:
                break
        if hit is not None:
            repeated += 1
        if c not in seen:
            seen[c] = i
    return repeated / len(samples)
