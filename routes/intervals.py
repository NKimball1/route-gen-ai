"""Find a Stretch of road suited to structured intervals.

The idea: a Spot is a continuous Stretch of quiet road with the right
gradient character — flat and steady for threshold work, a consistent
slight climb for VO2 reps. We search by routing Spokes outward from the
start in many directions (the router already prefers quiet roads); each
Spoke offers the Stretches worth trying along it (routes/stretch.py
measures them), and this module keeps the search policy: what is
acceptable and how to rank.

- flat spots:    minimize |mean grade|, grade variability, and turn density
- incline spots: mean grade near the sweet spot (~4%), climbing one way,
                 low variability, low turn density

Turn density is a proxy for junction interruptions. Overpass supplies mapped
stop signs and signals; unavailable control data remains explicitly unknown.
"""
from dataclasses import dataclass, field

from routes.power import DEFAULT_TOTAL_KG, seconds_for, speed_mps
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE, Router, Track
from routes.stretch import Spoke, Stretch

# Speed assumptions for turning rep duration into stretch length when
# the rider gives no power figure. With watts, physics decides instead.
FLAT_SPEED_MPH: float = 20.0      # threshold pace on flat road
INCLINE_SPEED_MPH: float = 11.0   # VO2 pace into a grade
TRAVEL_SPEED_MPH: float = 15.0    # easy riding out to the spot
# Grade the power model assumes while SIZING the search window (the
# scorer's own ideal for each kind); each found stretch is then timed
# at its real grade.
ASSUMED_GRADE_PCT: dict[str, float] = {"flat": 0.0, "incline": 4.0,
                                       "any": 0.0}


@dataclass
class IntervalSpec:
    address: str
    reps: int
    rep_minutes: float
    kind: str                      # "flat", "incline", or "any"
    # "any": grade is not a goal; what matters is that the stretch works
    # ridden in EITHER direction (out-and-back reps), so it is scored on
    # how evenly the two directions take the rep time.
    max_travel_minutes: float = 30.0
    watts: float | None = None     # target power; sizes reps by physics
    total_kg: float = DEFAULT_TOTAL_KG   # rider + bike, for the physics
    # Hard limit on stops/signals per stretch. The scorer already prefers
    # fewer, but "no interruptions" is a requirement, not a preference.
    # Can't be enforced when Overpass is down (counts unknown).
    max_stops: int | None = None

    @property
    def rep_distance_m(self) -> float:
        if self.watts:
            v = speed_mps(self.watts, ASSUMED_GRADE_PCT[self.kind],
                          self.total_kg)
            return v * self.rep_minutes * 60.0
        mph = INCLINE_SPEED_MPH if self.kind == "incline" else FLAT_SPEED_MPH
        return self.rep_minutes * mph / 60.0 * METERS_PER_MILE

    def rep_fits(self, length_m: float, grade_pct: float) -> bool:
        """Is a stretch of this length and grade still within one rep?
        With watts, the answer is TIME at the stretch's own grade — the
        search window can then grow past the assumed-grade distance on a
        gentler road, or stop short on a steeper one."""
        if self.watts:
            if self.kind == "any":
                # both directions get ridden: grow until even the faster
                # (downhill) pass fills the rep
                grade_pct = -abs(grade_pct)
            return (seconds_for(length_m, grade_pct, self.watts, self.total_kg)
                    <= self.rep_minutes * 60.0)
        return length_m <= self.rep_distance_m

    @property
    def travel_radius_m(self) -> float:
        return self.max_travel_minutes * TRAVEL_SPEED_MPH / 60.0 * METERS_PER_MILE


@dataclass
class Spot:
    """A Stretch the finder recommends, plus the ride out to it and its road."""
    points: Track = field(repr=False)  # the stretch itself
    length_m: float = 0.0
    mean_grade_pct: float = 0.0
    grade_std_pct: float = 0.0
    turns_per_km: float = 0.0
    n_controls: int = 0                # stop signs/signals/etc. on the stretch
    controls_known: bool = True        # False: Overpass was down; count is unknown
    unpaved_frac: float = 0.0          # share of the stretch on gravel/compacted/dirt
    busy_frac: float = 0.0             # share on secondary-or-bigger roads
    control_wt_per_km: float = 0.0     # severity-weighted interruptions per km
    dist_from_start_m: float = 0.0     # riding distance out to the stretch
    bearing: float = 0.0
    score: float = 0.0
    gpx_path: str | None = None
    road_name: str = ""

    @property
    def length_mi(self) -> float:
        return self.length_m / METERS_PER_MILE

    @property
    def climb_ft(self) -> float:
        return self.length_m * self.mean_grade_pct / 100.0 / METERS_PER_FOOT

    def seconds_at(self, watts: float,
                   total_kg: float = DEFAULT_TOTAL_KG,
                   reverse: bool = False) -> float:
        """How long one pass of this stretch takes at `watts`, using the
        stretch's own mean grade — the number a rider plans a rep around.
        `reverse`: ridden the other way (grade sign flipped)."""
        grade = -self.mean_grade_pct if reverse else self.mean_grade_pct
        return seconds_for(self.length_m, grade, watts, total_kg)


# Climbing per km at which a "flat" stretch has no flatness credit left
# (~80 ft/mi). Average grade alone can't see rollers: a road that climbs
# and descends 30 m averages 0% (Paulson Rd ranked as the flattest 2x20
# stretch with 370 ft of climbing). Paved-trail flat is ~2.5 m/km.
ROLLING_ZERO_M_PER_KM: float = 15.0
# Interval stretches are for road bikes at threshold: crushed-limestone
# trails kept making the lists (Military Ridge, a third-limestone 'flat'
# pick). A window more than this share unpaved is skipped outright.
MAX_UNPAVED_FRAC: float = 0.10


def _score(spec: IntervalSpec, length: float, mean_grade: float,
           grade_std: float, turns_per_km: float,
           control_wt: float = 0.0,
           climb_m_per_km: float = 0.0) -> float:
    # Longer is better up to the full rep distance (you can lap a shorter
    # stretch, but every turnaround interrupts the effort).
    len_score = min(length / spec.rep_distance_m, 1.0)
    rolling_score = max(0.0, 1.0 - climb_m_per_km / ROLLING_ZERO_M_PER_KM)
    if spec.kind == "flat":
        grade_score = min(max(0.0, 1.0 - abs(mean_grade) / 1.5),  # 0 at 1.5%
                          rolling_score)
        steady_score = max(0.0, 1.0 - grade_std / 3.0)
    elif spec.kind == "any":
        # symmetry: how evenly the two directions take the rep. With
        # watts this is the ratio of pass times (1.0 on the flat, ~0.8
        # at 1.5%, ~0.6 at 3%); without, a gentler grade penalty than
        # "flat" -- rolling is fine, a hill is not.
        if spec.watts:
            up = seconds_for(length, abs(mean_grade), spec.watts, spec.total_kg)
            down = seconds_for(length, -abs(mean_grade), spec.watts,
                               spec.total_kg)
            grade_score = down / up if up > 0 else 0.0
        else:
            grade_score = max(0.0, 1.0 - abs(mean_grade) / 4.0)
        # symmetric pass times don't make rollers smooth: hold power over
        # them and it spikes on every rise
        grade_score = min(grade_score, rolling_score)
        steady_score = max(0.0, 1.0 - grade_std / 4.0)
    else:
        grade_score = max(0.0, 1.0 - abs(abs(mean_grade) - 4.0) / 3.0)  # peak at 4%
        steady_score = max(0.0, 1.0 - grade_std / 4.0)
    turn_score = max(0.0, 1.0 - turns_per_km / 4.0)
    # What matters for an interval is interruptions PER REP: lapping a short
    # stretch re-encounters its controls, so scale the window's weighted
    # control count to one rep distance. This term dominates — a single
    # signal per rep halves it, and a clean short stretch should beat a long
    # stretch with stops (turnarounds interrupt less than traffic lights).
    wt_per_rep = control_wt * spec.rep_distance_m / max(length, 1.0)
    control_score = 1.0 / (1.0 + wt_per_rep)
    return (0.25 * len_score + 0.25 * grade_score + 0.1 * steady_score
            + 0.05 * turn_score + 0.35 * control_score)


def find_spots(spec: IntervalSpec, lat: float, lon: float, provider: Router,
               n_spokes: int = 12, top: int = 3) -> list[Spot]:
    """Search Spokes around the start for the best interval Stretches.
    Every Stretch is measured by its Spoke (routes/stretch.py); this
    function only decides what is acceptable and how to rank."""
    from routes.interruptions import fetch_controls
    from routes.providers import _destination

    controls = fetch_controls(lat, lon, spec.travel_radius_m + 2000)
    controls_known = controls is not None
    if spec.max_stops is not None and not controls_known:
        print("Cannot verify the requested stop limit while traffic-control data is unavailable.")
        return []
    print(f"  {len(controls)} traffic controls (stops/signals/crossings) in area"
          if controls is not None else
          "  warning: no traffic-control data (Overpass down?) — scoring "
          "without interruption counts; stop counts will read '?'")

    spots: list[Spot] = []
    for i in range(n_spokes):
        bearing = 360.0 * i / n_spokes
        dest = _destination(lat, lon, bearing, spec.travel_radius_m / 1.2)
        leg = provider.route([(lat, lon), dest])
        if leg is None:
            continue
        if any(p[2] is None for p in leg["points"]):
            print("  skipping a spoke with missing elevation data")
            continue
        spoke = Spoke(leg, controls)
        best: tuple[float, Stretch] | None = None
        # "within N minutes" is riding distance, not the crow-flies reach
        # of the spoke: a winding road runs past the budget before the
        # spoke's endpoint does (a 10 mi ask once returned 12.5 mi out)
        for stretch in spoke.stretches_worth_trying(
                spec, starting_within_m=spec.travel_radius_m):
            if _acceptable(spec, stretch):
                score = _rank(spec, stretch)
                if best is None or score > best[0]:
                    best = (score, stretch)
        if best is not None:
            score, stretch = best
            spots.append(Spot(
                points=stretch.points, length_m=stretch.length_m,
                mean_grade_pct=stretch.mean_grade_pct,
                grade_std_pct=stretch.grade_std_pct,
                turns_per_km=stretch.turns_per_km, n_controls=stretch.stops,
                control_wt_per_km=stretch.stop_weight_per_km,
                controls_known=stretch.stops_known,
                unpaved_frac=stretch.gravel_share, busy_frac=stretch.busy_share,
                dist_from_start_m=stretch.starts_at_m,
                bearing=bearing, score=score,
            ))

    spots.sort(key=lambda s: s.score, reverse=True)
    return _dedupe(spots)[:top]


def _acceptable(spec: IntervalSpec, stretch: Stretch) -> bool:
    """Search policy: long enough to be worth a Rep, reachable within the
    ride-out budget, paved, and within the rider's stop limit."""
    if stretch.length_m < 0.35 * spec.rep_distance_m or stretch.length_m < 400:
        return False
    if stretch.starts_at_m > spec.travel_radius_m:
        return False
    if stretch.gravel_share > MAX_UNPAVED_FRAC:
        return False
    if (spec.max_stops is not None and stretch.stops_known
            and stretch.stops > spec.max_stops):
        return False
    return True


def _rank(spec: IntervalSpec, stretch: Stretch) -> float:
    # each share of the stretch on a busy road costs that share of the score
    return _score(spec, stretch.length_m, stretch.mean_grade_pct,
                  stretch.grade_std_pct, stretch.turns_per_km,
                  stretch.stop_weight,
                  climb_m_per_km=stretch.climb_m_per_km) * (1.0 - stretch.busy_share)


# Two spokes a few degrees apart often share their first miles of road, so
# the same stretch came back as #1, #2 and #3. Comparing midpoints missed a
# piece sitting inside a longer stretch (Highway 12 Path, listed twice) and
# half-overlapping sections of one trail -- and merged parallel roads that
# are genuinely different spots. Two stretches are one spot when at least
# DUPLICATE_OVERLAP of the shorter lies within ON_SAME_ROAD_M of the other.
ON_SAME_ROAD_M: float = 40.0
DUPLICATE_OVERLAP: float = 0.5


def _overlap(a: Spot, b: Spot) -> float:
    """Fraction of the shorter stretch lying on the other's road."""
    from routes.road_avoid import dist_to_road
    short, long_ = (a, b) if a.length_m <= b.length_m else (b, a)
    way = [[(p[0], p[1]) for p in long_.points]]
    pts = short.points
    on = sum(1 for p in pts if dist_to_road(p, way) <= ON_SAME_ROAD_M)
    return on / len(pts) if pts else 0.0


def _dedupe(spots: list[Spot]) -> list[Spot]:
    """Drop stretches that are the same road as a better-scored one.
    `spots` must already be sorted best-first."""
    kept: list[Spot] = []
    for s in spots:
        if all(_overlap(s, k) < DUPLICATE_OVERLAP for k in kept):
            kept.append(s)
    return kept
