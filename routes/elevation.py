"""Climbing figures that match what the rider's devices will say.

Calibrated against RideWithGPS on two measured routes of opposite
character (hilly 45 mi: 2,483 ft; flat 48 mi: 1,600 ft). A single
hysteresis threshold can't fit both — on flat routes much of the real gain
is many sub-10 m rollers that a big threshold discards (first model read
33% low on the flat route). The fitted model: resample to 25 m, smooth
elevation with a ~225 m rolling mean (kills DEM noise), then sum climbs
with a 0.5 m threshold (keeps rollers). Max error across both calibration
routes: ~6%.
"""
from routes.despur import _resample

SMOOTH_WINDOW_SAMPLES = 9   # x 25 m resample step ~= 225 m
HYSTERESIS_M = 0.5


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
