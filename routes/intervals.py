"""Find a stretch of road suited to structured intervals.

The idea: an interval spot is a continuous stretch of quiet road with the
right gradient character — flat and steady for threshold work, a consistent
slight climb for VO2 reps. We search by routing spokes outward from the start
in many directions (the router already prefers quiet roads), then slide a
window along each spoke's geometry and score every window:

- flat spots:    minimize |mean grade|, grade variability, and turn density
- incline spots: mean grade near the sweet spot (~4%), climbing one way,
                 low variability, low turn density

Turn density is a proxy for interruptions (junctions where you'd have to
brake); true stop-sign/traffic-light data would need an Overpass query and is
a known upgrade path.
"""
import math
from dataclasses import dataclass, field

from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

EARTH_RADIUS_M = 6371000.0

# Speed assumptions for turning rep duration into stretch length.
FLAT_SPEED_MPH = 20.0      # threshold pace on flat road
INCLINE_SPEED_MPH = 11.0   # VO2 pace into a grade
TRAVEL_SPEED_MPH = 15.0    # easy riding out to the spot


@dataclass
class IntervalSpec:
    address: str
    reps: int
    rep_minutes: float
    kind: str                      # "flat" or "incline"
    max_travel_minutes: float = 30.0

    @property
    def rep_distance_m(self) -> float:
        mph = FLAT_SPEED_MPH if self.kind == "flat" else INCLINE_SPEED_MPH
        return self.rep_minutes * mph / 60.0 * METERS_PER_MILE

    @property
    def travel_radius_m(self) -> float:
        return self.max_travel_minutes * TRAVEL_SPEED_MPH / 60.0 * METERS_PER_MILE


@dataclass
class IntervalSpot:
    points: list = field(repr=False)   # (lat, lon, ele) along the stretch
    length_m: float = 0.0
    mean_grade_pct: float = 0.0
    grade_std_pct: float = 0.0
    turns_per_km: float = 0.0
    dist_from_start_m: float = 0.0     # riding distance out to the stretch
    bearing: float = 0.0
    score: float = 0.0

    @property
    def length_mi(self) -> float:
        return self.length_m / METERS_PER_MILE

    @property
    def climb_ft(self) -> float:
        return self.length_m * self.mean_grade_pct / 100.0 / METERS_PER_FOOT


def _hav_m(a, b) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _bearing_deg(a, b) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dlam = math.radians(b[1] - a[1])
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


BIN_M = 100.0  # resample step: kills GPS-style elevation jitter in grades


def _resample(points, step_m: float = BIN_M):
    """Points at ~step_m spacing with cumulative distance: (lat, lon, ele, cum)."""
    out = []
    cum = carry = 0.0
    last = None
    for p in points:
        if p[2] is None:
            continue
        if last is None:
            out.append((p[0], p[1], p[2], 0.0))
        else:
            d = _hav_m(last, p)
            cum += d
            carry += d
            if carry >= step_m:
                out.append((p[0], p[1], p[2], cum))
                carry = 0.0
        last = p
    return out


def _window_stats(rs, i, j):
    """Stats for resampled slice rs[i..j]."""
    length = rs[j][3] - rs[i][3]
    grades, turns = [], 0
    for k in range(i, j):
        d = rs[k + 1][3] - rs[k][3]
        if d > 0:
            grades.append((rs[k + 1][2] - rs[k][2]) / d * 100.0)
    for k in range(i + 1, j):
        turn = abs(_bearing_deg(rs[k - 1][:2], rs[k][:2])
                   - _bearing_deg(rs[k][:2], rs[k + 1][:2]))
        if turn > 180.0:
            turn = 360.0 - turn
        if turn > 35.0:
            turns += 1
    mean = sum(grades) / len(grades) if grades else 0.0
    var = (sum((g - mean) ** 2 for g in grades) / len(grades)) if grades else 0.0
    return length, mean, math.sqrt(var), turns / max(length / 1000.0, 0.001)


def _score(spec: IntervalSpec, length, mean_grade, grade_std, turns_per_km) -> float:
    # Longer is better up to the full rep distance (you can lap a shorter
    # stretch, but every turnaround interrupts the effort).
    len_score = min(length / spec.rep_distance_m, 1.0)
    if spec.kind == "flat":
        grade_score = max(0.0, 1.0 - abs(mean_grade) / 1.5)      # 0 at 1.5%
        steady_score = max(0.0, 1.0 - grade_std / 3.0)
    else:
        grade_score = max(0.0, 1.0 - abs(abs(mean_grade) - 4.0) / 3.0)  # peak at 4%
        steady_score = max(0.0, 1.0 - grade_std / 4.0)
    turn_score = max(0.0, 1.0 - turns_per_km / 4.0)
    return 0.35 * len_score + 0.3 * grade_score + 0.15 * steady_score + 0.2 * turn_score


def find_spots(spec: IntervalSpec, lat: float, lon: float, provider,
               n_spokes: int = 12, top: int = 3) -> list[IntervalSpot]:
    """Search spokes around the start for the best interval stretches."""
    from routes.providers import _destination

    spots = []
    for i in range(n_spokes):
        bearing = 360.0 * i / n_spokes
        dest = _destination(lat, lon, bearing, spec.travel_radius_m / 1.2)
        leg = provider.route([(lat, lon), dest])
        if leg is None:
            continue
        rs = _resample(leg["points"])
        if len(rs) < 5:
            continue
        # Slide a window of up to rep_distance along the spoke; step ~250 m.
        best_for_spoke = None
        i0 = 0
        for i0 in range(0, len(rs) - 3):
            j = i0
            while j + 1 < len(rs) and rs[j + 1][3] - rs[i0][3] <= spec.rep_distance_m:
                j += 1
            length, mean, std, tpk = _window_stats(rs, i0, j)
            if length < 0.35 * spec.rep_distance_m or length < 400:
                continue
            score = _score(spec, length, mean, std, tpk)
            spot = IntervalSpot(
                points=[p[:3] for p in rs[i0:j + 1]],
                length_m=length, mean_grade_pct=mean, grade_std_pct=std,
                turns_per_km=tpk, dist_from_start_m=rs[i0][3],
                bearing=bearing, score=score,
            )
            if best_for_spoke is None or spot.score > best_for_spoke.score:
                best_for_spoke = spot
        if best_for_spoke is not None:
            spots.append(best_for_spoke)

    # For incline spots a downhill window is the same road ridden the other
    # way: flip the sign so scoring saw it, but report positive grade.
    for s in spots:
        if spec.kind == "incline" and s.mean_grade_pct < 0:
            s.points = list(reversed(s.points))
            s.mean_grade_pct = -s.mean_grade_pct
    spots.sort(key=lambda s: s.score, reverse=True)
    return spots[:top]
