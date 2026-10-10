"""Spoke and Stretch: every fact about a piece of road the interval finder
considers is worked out here and read everywhere else.

A Spoke is one direction searched outward from the rider's start: the
router's road for that direction plus the area's traffic controls. It does
the per-direction work once (resampling, surface and road-class flags,
controls mapped onto the road, turns) so that measuring any Stretch along it
stays cheap inside the search loop.

A Stretch is a piece of a Spoke ridden in a stated direction. It answers
facts only; what is acceptable and how to rank lives in routes/intervals.py.
"""
import math
from bisect import bisect_left, bisect_right
from functools import cached_property
from typing import Iterator, Protocol, Sequence

from routes.interruptions import controls_along
from routes.power import DEFAULT_TOTAL_KG, seconds_for
from routes.spec import EARTH_RADIUS_M, Coord, LatLon, Leg, Point, Sample, Track

BIN_M: float = 100.0  # resample step: kills GPS-style elevation jitter in grades
# Controls just past a Stretch's ends still interrupt every Lap (you
# turn around there), and mapped positions carry a little noise.
CONTROL_PAD_M: float = 150.0
ON_WAY_M: float = 15.0   # a resampled point this close to a gravel/busy line is on it
TURN_DEG: float = 35.0  # a bearing change sharper than this between bins is a turn


def _hav_m(a: Coord, b: Coord) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlam = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _bearing_deg(a: Coord, b: Coord) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dlam = math.radians(b[1] - a[1])
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _resample(points: Track, step_m: float = BIN_M) -> list[Sample]:
    """Points at ~step_m spacing with cumulative distance: (lat, lon, ele, cum)."""
    out: list[Sample] = []
    cum = carry = 0.0
    last: Point | None = None
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


class RepPlan(Protocol):
    """What a Stretch needs to know of the rider's plan (IntervalSpec)."""
    rep_minutes: float
    kind: str                  # "flat", "incline", or "any" (either direction)
    watts: float | None
    total_kg: float

    @property
    def rep_distance_m(self) -> float: ...


def _lap_fits_rep(length_m: float, grade_pct: float, plan: RepPlan) -> bool:
    """One Lap of this length and grade is still within one Rep. With
    watts, the answer is TIME at the grade; an either-direction plan rides
    both ways, so the faster (downhill) Lap decides."""
    if plan.watts:
        if plan.kind == "any":
            grade_pct = -abs(grade_pct)
        return (seconds_for(length_m, grade_pct, plan.watts, plan.total_kg)
                <= plan.rep_minutes * 60.0)
    return length_m <= plan.rep_distance_m


def _on_ways_cum(rs: list[Sample], ways: list[list[LatLon]]) -> list[int]:
    """Prefix counts of resampled points lying on `ways` (within ON_WAY_M):
    points on ways in rs[i..j] = cum[j + 1] - cum[i]."""
    if not ways:
        return [0] * (len(rs) + 1)
    from routes.road_avoid import dist_to_road
    cum = [0]
    for p in rs:
        cum.append(cum[-1] + int(dist_to_road((p[0], p[1]), ways) <= ON_WAY_M))
    return cum


class Spoke:
    """One routed direction from the start, ready to measure Stretches on."""

    def __init__(self, leg: Leg, controls: Sequence[Sequence[float]] | None) -> None:
        self._raw: Track = leg["points"]
        self._raw_cum = [0.0]
        for a, b in zip(self._raw, self._raw[1:]):
            self._raw_cum.append(self._raw_cum[-1] + _hav_m(a, b))
        self._rs: list[Sample] = _resample(self._raw)
        rs = self._rs
        # Surface and road class: flag each resampled point once; a Stretch
        # reads its gravel / busy share from a running count.
        self._unpaved_cum = _on_ways_cum(rs, leg.get("unpaved", []))
        self._busy_cum = _on_ways_cum(rs, leg.get("busy", []))
        # bad_next[k]: first index >= k that is busy or gravel (len(rs) if none)
        self._bad_next = [len(rs)] * (len(rs) + 1)
        for k in range(len(rs) - 1, -1, -1):
            bad = (self._busy_cum[k + 1] > self._busy_cum[k]
                   or self._unpaved_cum[k + 1] > self._unpaved_cum[k])
            self._bad_next[k] = k if bad else self._bad_next[k + 1]
        # Map every control onto the road once; a Stretch then counts hits
        # in its distance range with two bisects.
        self.stops_known = controls is not None
        hits = controls_along([(p[0], p[1]) for p in self._raw], controls or [])
        self._hit_pos = [h[0] for h in hits]
        self._hit_wt_cum = [0.0]
        for _, w in hits:
            self._hit_wt_cum.append(self._hit_wt_cum[-1] + w)
        # turns_cum[k]: turns at interior samples before k; a Stretch over
        # rs[i..j] turns at samples i+1 .. j-1.
        self._turns_cum = [0, 0]
        for k in range(1, len(rs) - 1):
            turn = abs(_bearing_deg(rs[k - 1][:2], rs[k][:2])
                       - _bearing_deg(rs[k][:2], rs[k + 1][:2]))
            if turn > 180.0:
                turn = 360.0 - turn
            self._turns_cum.append(self._turns_cum[-1] + int(turn > TURN_DEG))

    @property
    def length_m(self) -> float:
        return self._rs[-1][3] if self._rs else 0.0

    def _index(self, along_m: float) -> int:
        """The resampled point nearest `along_m` meters along the Spoke."""
        cums = [s[3] for s in self._rs]
        k = bisect_left(cums, along_m)
        if k == len(cums):
            return k - 1
        if k > 0 and along_m - cums[k - 1] <= cums[k] - along_m:
            return k - 1
        return k

    def stretch(self, from_m: float, to_m: float) -> "Stretch":
        """The Stretch between two points along the Spoke, ridden from
        `from_m` towards `to_m` (back towards the start if to_m < from_m).
        Ends snap to the Spoke's ~100 m measuring points."""
        i, j = self._index(from_m), self._index(to_m)
        return Stretch(self, min(i, j), max(i, j), reverse=j < i)

    def stretches_worth_trying(self, plan: RepPlan,
                               starting_within_m: float) -> Iterator["Stretch"]:
        """From each measuring point up to `starting_within_m` along the
        Spoke: the longest Stretch whose Lap still fits a Rep, then (when it
        differs) the one cut short just before the first busy or gravel
        point -- a full-length Stretch is no use if its last kilometer is on
        a county highway, and stopping short (then lapping) is how a rider
        would use that road. Each is oriented the way the plan rides it: an
        incline plan rides a descending piece the other way, uphill.
        A Spoke too short to measure (under five points) offers nothing."""
        rs = self._rs
        if len(rs) < 5:
            return
        for i in range(0, len(rs) - 3):
            if rs[i][3] > starting_within_m:
                break
            # Grow outward while one Lap (at the grade so far, ridden
            # outward) still fits a Rep.
            j_full = i
            while j_full + 1 < len(rs) and _lap_fits_rep(
                    rs[j_full + 1][3] - rs[i][3],
                    (rs[j_full + 1][2] - rs[i][2])
                    / max(rs[j_full + 1][3] - rs[i][3], 1.0) * 100.0, plan):
                j_full += 1
            ends = [j_full]
            k_bad = self._bad_next[i]
            if i < k_bad - 1 < j_full:
                ends.append(k_bad - 1)
            for j in ends:
                stretch = Stretch(self, i, j)
                if plan.kind == "incline" and stretch.mean_grade_pct < 0:
                    stretch = Stretch(self, i, j, reverse=True)
                yield stretch


class Stretch:
    """A measured piece of a Spoke, ridden in a stated direction."""

    def __init__(self, spoke: Spoke, i: int, j: int, reverse: bool = False) -> None:
        self._spoke = spoke
        self._i, self._j = i, j       # resampled indices in Spoke order, i < j
        self.reverse = reverse        # ridden back towards the Spoke's start

    @cached_property
    def length_m(self) -> float:
        rs = self._spoke._rs
        return rs[self._j][3] - rs[self._i][3]

    @property
    def starts_at_m(self) -> float:
        """How far along the Spoke a Lap starts, in the riding direction."""
        rs = self._spoke._rs
        return rs[self._j][3] if self.reverse else rs[self._i][3]

    @cached_property
    def mean_grade_pct(self) -> float:
        """Average grade in the riding direction."""
        rs, i, j = self._spoke._rs, self._i, self._j
        rise = rs[i][2] - rs[j][2] if self.reverse else rs[j][2] - rs[i][2]
        return rise / self.length_m * 100.0 if self.length_m else 0.0

    @cached_property
    def grade_std_pct(self) -> float:
        """How unsteady the grade is (std of ~100 m grades); the same either way."""
        rs, i, j = self._spoke._rs, self._i, self._j
        grades: list[float] = []
        for k in range(i, j):
            d = rs[k + 1][3] - rs[k][3]
            if d > 0:
                grades.append((rs[k + 1][2] - rs[k][2]) / d * 100.0)
        mean = (rs[j][2] - rs[i][2]) / self.length_m * 100.0 if self.length_m else 0.0
        var = (sum((g - mean) ** 2 for g in grades) / len(grades)) if grades else 0.0
        return math.sqrt(var)

    @cached_property
    def turns_per_km(self) -> float:
        cum = self._spoke._turns_cum
        turns = cum[self._j] - cum[self._i + 1] if self._j > self._i + 1 else 0
        return turns / max(self.length_m / 1000.0, 0.001)

    @cached_property
    def climb_m_per_km(self) -> float:
        """Meters climbed per km, riding in the stated direction."""
        rs, i, j = self._spoke._rs, self._i, self._j
        if self.reverse:
            climb = sum(max(0.0, rs[k][2] - rs[k + 1][2]) for k in range(j - 1, i - 1, -1))
        else:
            climb = sum(max(0.0, rs[k + 1][2] - rs[k][2]) for k in range(i, j))
        return climb / max(self.length_m / 1000.0, 0.001)

    def lap_seconds(self, watts: float, total_kg: float = DEFAULT_TOTAL_KG,
                    back: bool = False) -> float:
        """How long one Lap takes holding `watts`, at the Stretch's average
        grade; `back`: the Lap ridden the other way."""
        grade = -self.mean_grade_pct if back else self.mean_grade_pct
        return seconds_for(self.length_m, grade, watts, total_kg)

    def fits_rep(self, plan: RepPlan) -> bool:
        """One Lap is within one Rep of the plan (an either-direction plan:
        the faster Lap is). The search grows a Stretch while this holds."""
        return _lap_fits_rep(self.length_m, self.mean_grade_pct, plan)

    def _share(self, cum: list[int]) -> float:
        return (cum[self._j + 1] - cum[self._i]) / (self._j - self._i + 1)

    @property
    def gravel_share(self) -> float:
        """Share of the Stretch on gravel, compacted or dirt surfaces."""
        return self._share(self._spoke._unpaved_cum)

    @property
    def busy_share(self) -> float:
        """Share of the Stretch on secondary-or-bigger roads."""
        return self._share(self._spoke._busy_cum)

    @cached_property
    def _hit_range(self) -> tuple[int, int]:
        # Pad the range: a light AT the turnaround point still interrupts
        # every Lap, and mapped positions carry noise.
        rs, pos = self._spoke._rs, self._spoke._hit_pos
        return (bisect_left(pos, rs[self._i][3] - CONTROL_PAD_M),
                bisect_right(pos, rs[self._j][3] + CONTROL_PAD_M))

    @property
    def stops_known(self) -> bool:
        """False when traffic-control data was unavailable: then `stops`
        reads 0 but means unknown."""
        return self._spoke.stops_known

    @property
    def stops(self) -> int:
        """Stop signs, signals and crossings met on one Lap."""
        a, b = self._hit_range
        return b - a

    @property
    def stop_weight(self) -> float:
        """Stops weighted by how much each one interrupts (a signal > a sign)."""
        a, b = self._hit_range
        return self._spoke._hit_wt_cum[b] - self._spoke._hit_wt_cum[a]

    @property
    def stop_weight_per_km(self) -> float:
        return self.stop_weight / max(self.length_m / 1000.0, 0.001)

    @cached_property
    def points(self) -> Track:
        """The road's own points (every bend kept), in riding order."""
        spoke, rs = self._spoke, self._spoke._rs
        piece = spoke._raw[bisect_left(spoke._raw_cum, rs[self._i][3]):
                           bisect_left(spoke._raw_cum, rs[self._j][3]) + 1]
        return list(reversed(piece)) if self.reverse else piece
