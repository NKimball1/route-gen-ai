"""Climbing figures that match what the rider's devices will say.

BRouter's "filtered ascend" smooths aggressively and reads ~40% below what
RideWithGPS / Garmin report for the same route; riders trust their devices.
This computes total ascent from the track's own elevation profile with a
hysteresis threshold, calibrated against RideWithGPS on a real route
(hysteresis 10 m -> within ~2.5% of RWGPS's figure). Computed on the FINAL
trimmed geometry, which also fixes ascent-accounting artifacts on heavily
despurred candidates.
"""

HYSTERESIS_M = 10.0


def track_ascent(points, hysteresis_m: float = HYSTERESIS_M) -> float:
    """Total climb in meters over (lat, lon, ele) points."""
    total = 0.0
    low = high = None
    for p in points:
        e = p[2]
        if e is None:
            continue
        if low is None:
            low = high = e
            continue
        if e > high:
            high = e
        if high - e >= hysteresis_m or e < low:
            if high - low >= hysteresis_m:
                total += high - low
            low = high = e
    if low is not None and high - low >= hysteresis_m:
        total += high - low
    return total
