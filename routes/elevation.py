"""Climbing figures that match what the rider's devices will say.

Calibration history (each field measurement improved the model):
1. Single 10 m hysteresis, fit on one hilly route vs RideWithGPS — read
   33% low on a flat route (sub-10 m rollers all discarded).
2. Two-point RWGPS fit -> 225 m smooth + 0.5 m threshold.
3. A ridden barometric .fit file (compare_ride.py) showed the rider's
   Garmin reads ~8-16% ABOVE RideWithGPS on identical ground — the
   instruments themselves disagree, so ±10% is the attainable accuracy.
   Current fit centers the model in the instrument spread: 25 m resample,
   ~125 m rolling-mean smooth, 1 m threshold. Flat route: 1,689 ft
   (RWGPS 1,600 / Garmin-derived 1,870); hilly: 2,927 (RWGPS 2,483).
"""
from routes.despur import _resample

SMOOTH_WINDOW_SAMPLES = 5   # x 25 m resample step ~= 125 m
HYSTERESIS_M = 1.0


def _smooth(eles, w: int = SMOOTH_WINDOW_SAMPLES):
    out = []
    for i in range(len(eles)):
        lo, hi = max(0, i - w // 2), min(len(eles), i + w // 2 + 1)
        vals = [e for e in eles[lo:hi] if e is not None]
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def track_ascent(points, hysteresis_m: float = HYSTERESIS_M) -> float:
    """Total climb in meters over (lat, lon, ele) points."""
    rs = _resample(points)
    eles = _smooth([p[2] for p in rs])
    total = 0.0
    low = high = None
    for e in eles:
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
