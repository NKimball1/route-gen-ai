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

from routes.elevation import PROFILE_STEP_M, ascent, smoothed_profile
from routes.interruptions import controls_along, is_path_only
from routes.policy import CONTROL_PAD_M, ON_WAY_M, REP_FIT_TOLERANCE, TURN_DEG
from routes.power import DEFAULT_TOTAL_KG, seconds_for
from routes.spec import EARTH_RADIUS_M, Coord, LatLon, Leg, Point, Sample, Track

BIN_M: float = 100.0  # resample step: kills GPS-style elevation jitter in grades


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


def _laps(lap: float, rep: float) -> int:
    """Laps of `lap` (seconds or meters) one `rep` needs. A Rep's length
    comes from an assumed pace, so it is already a rough number: a Lap
    within REP_FIT_TOLERANCE of a full Rep is one Lap, not a turnaround
    for being 0.1% short."""
    if lap <= 0:
        return 1
    return max(1, math.ceil(rep * (1.0 - REP_FIT_TOLERANCE) / lap))


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


class _OnLines:
    """Is a point within a few meters of these lines? An area holds
    thousands of crossing nodes (every crosswalk in town) and a trail
    Spoke thousands of points, so a coarse grid of the cells the lines
    pass through rules out almost every node before the exact check."""
    CELL_DEG = 0.001   # ~110 m of latitude: far wider than any reach used here

    def __init__(self, lines: list[list[LatLon]]) -> None:
        self._lines = lines
        self._cells: set[tuple[int, int]] = set()
        for line in lines:
            for a, b in zip(line, line[1:] or line):
                # sample each segment at under a cell's spacing, so every
                # point on it lies in or next to a registered cell
                n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / self.CELL_DEG) + 1
                for t in range(n + 1):
                    self._cells.add(self._cell(a[0] + (b[0] - a[0]) * t / n,
                                               a[1] + (b[1] - a[1]) * t / n))

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return math.floor(lat / self.CELL_DEG), math.floor(lon / self.CELL_DEG)

    def within(self, lat: float, lon: float, reach_m: float) -> bool:
        i, j = self._cell(lat, lon)
        if not any((i + di, j + dj) in self._cells
                   for di in (-1, 0, 1) for dj in (-1, 0, 1)):
            return False
        from routes.road_avoid import dist_to_road
        return dist_to_road((lat, lon), self._lines) <= reach_m


class Spoke:
    """One routed direction from the start, ready to measure Stretches on.
    Built from the router's leg (points with elevation, plus its optional
    `unpaved` / `busy` lines) and the area's traffic controls, or None when
    they could not be fetched (stop counts are then unknown)."""

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
        # in its distance range with two bisects. A road crossing counts
        # only where this Spoke rides a path through it: on the road being
        # crossed it is a crosswalk, someone else's stop.
        self.stops_known = controls is not None
        on_path = _OnLines(leg.get("path", []))
        mine = [c for c in controls or []
                if not is_path_only(c) or on_path.within(c[0], c[1], c[3])]
        hits = controls_along([(p[0], p[1]) for p in self._raw], mine)
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
        self._lap_cums: dict[tuple[float, float], tuple[list[float], list[float]]] = {}

    def _seconds_cum(self, watts: float, total_kg: float) -> tuple[list[float], list[float]]:
        """Running riding time at steady `watts` piece by piece (~100 m)
        along the grade profile, (outward, back towards the start): a
        Stretch over rs[i..j] takes cum[j] - cum[i] either way. Worked out
        once per rider, so timing a Stretch in the search loop is cheap."""
        key = (watts, total_kg)
        cums = self._lap_cums.get(key)
        if cums is None:
            out, back = [0.0], [0.0]
            for a, b in zip(self._rs, self._rs[1:]):
                d = b[3] - a[3]
                grade = (b[2] - a[2]) / d * 100.0 if d > 0 else 0.0
                out.append(out[-1] + seconds_for(d, grade, watts, total_kg))
                back.append(back[-1] + seconds_for(d, -grade, watts, total_kg))
            cums = self._lap_cums[key] = (out, back)
        return cums

    @property
    def length_m(self) -> float:
        return self._rs[-1][3] if self._rs else 0.0

    @cached_property
    def _profile(self) -> list[float | None]:
        """The calibrated elevation model's smoothed profile of the whole
        Spoke, built once: a Stretch's climbing is the model's threshold
        run over its slice of it."""
        return smoothed_profile(self._raw) if self._raw else []

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

        def as_ridden(i: int, j: int) -> Stretch:
            uphill_is_back = plan.kind == "incline" and rs[j][2] < rs[i][2]
            return Stretch(self, i, j, reverse=uphill_is_back)

        for i in range(0, len(rs) - 3):
            if rs[i][3] > starting_within_m:
                break
            # Grow outward while one Lap, ridden the way the plan rides
            # it, still fits a Rep.
            j_full = i
            while j_full + 1 < len(rs) and as_ridden(i, j_full + 1).fits_rep(plan):
                j_full += 1
            ends = [j_full]
            k_bad = self._bad_next[i]
            if i < k_bad - 1 < j_full:
                ends.append(k_bad - 1)
            for j in ends:
                yield as_ridden(i, j)


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
    def climb_m(self) -> float:
        """Meters climbed riding in the stated direction, by the calibrated
        elevation model (routes/elevation.py): the figure the rider's
        devices would show, and the one the finder ranks on."""
        profile, rs = self._spoke._profile, self._spoke._rs
        last = len(profile) - 1
        a = min(round(rs[self._i][3] / PROFILE_STEP_M), last)
        b = min(round(rs[self._j][3] / PROFILE_STEP_M), last)
        piece = profile[a:b + 1]
        return ascent(reversed(piece) if self.reverse else piece)

    @property
    def climb_m_per_km(self) -> float:
        """Meters climbed per km, riding in the stated direction."""
        return self.climb_m / max(self.length_m / 1000.0, 0.001)

    def lap_seconds(self, watts: float, total_kg: float = DEFAULT_TOTAL_KG,
                    back: bool = False) -> float:
        """How long one Lap takes holding `watts`, along the Stretch's grade
        profile (~100 m pieces), not its average grade: a 10% ramp costs
        more time than the gentle parts around it give back, so averaging
        it away made Laps 8-10% fast on a real ride (White Crossing).
        `back`: the Lap ridden the other way."""
        if watts <= 0:
            return math.inf
        out, home = self._spoke._seconds_cum(watts, total_kg)
        cum = home if self.reverse != back else out
        return cum[self._j] - cum[self._i]

    def _deciding_lap_seconds(self, watts: float, plan: RepPlan) -> float:
        """The Lap that decides how a Rep fits: this way, or for an
        either-direction plan (out-and-back Reps) the faster of the two."""
        lap = self.lap_seconds(watts, plan.total_kg)
        if plan.kind == "any":
            lap = min(lap, self.lap_seconds(watts, plan.total_kg, back=True))
        return lap

    def fits_rep(self, plan: RepPlan) -> bool:
        """One Lap is within one Rep of the plan: by Lap time with watts
        (an either-direction plan: the faster Lap), by distance without.
        The search grows a Stretch while this holds."""
        if plan.watts:
            return self._deciding_lap_seconds(plan.watts, plan) <= plan.rep_minutes * 60.0
        return self.length_m <= plan.rep_distance_m

    def laps_per_rep(self, plan: RepPlan) -> int:
        """How many Laps one Rep of the plan needs: by Lap time with watts
        (an either-direction plan: the faster Lap decides), by distance
        without."""
        if plan.watts:
            return _laps(self._deciding_lap_seconds(plan.watts, plan),
                         plan.rep_minutes * 60.0)
        return _laps(self.length_m, plan.rep_distance_m)

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
