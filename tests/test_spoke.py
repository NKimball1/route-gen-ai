"""The Spoke seam (routes/stretch.py): build a Spoke from one routed road
plus the area's traffic controls, then ask about Stretches along it."""
import math

import pytest

from routes.intervals import IntervalSpec
from routes.spec import EARTH_RADIUS_M, METERS_PER_MILE
from routes.stretch import Spoke

LAT_STEP = 0.0011  # ~122 m of latitude per step


def road(n, lat0=43.0, lon=-89.5, ele_fn=lambda k: 300.0):
    return [(lat0 + k * LAT_STEP, lon, ele_fn(k)) for k in range(n)]


def leg(points, **lines):
    return {"points": points, "distance_m": 0.0, "ascent_m": 0.0,
            "major_m": 0.0, **lines}


def whole(spoke):
    return spoke.stretch(0.0, spoke.length_m)


def on_road(points, controls=()):
    """The whole of a Spoke routed along `points`, as one Stretch.
    `controls=None`: traffic-control data was unavailable."""
    return whole(Spoke(leg(list(points)),
                       controls=None if controls is None else list(controls)))


def straight(length_m, grade_pct=0.0, controls=()):
    """A Stretch of exactly `length_m` due north at a steady `grade_pct`:
    a two-point road, so nothing snaps to the measuring points."""
    north = (43.0 + math.degrees(length_m / EARTH_RADIUS_M), -89.5,
             300.0 + length_m * grade_pct / 100.0)
    return on_road([(43.0, -89.5, 300.0), north], controls)


def no_power_plan(rep_m, kind="flat"):
    """A plan without watts whose Rep needs `rep_m` of road (at 20 mph)."""
    return IntervalSpec("x", 2, rep_m / METERS_PER_MILE * 3.0, kind)


def test_a_flat_straight_road_is_a_flat_steady_turnless_stretch():
    s = whole(Spoke(leg(road(40)), controls=[]))
    assert s.length_m > 4000
    assert abs(s.mean_grade_pct) < 0.1
    assert s.grade_std_pct < 0.1
    assert s.turns_per_km == 0.0
    assert s.climb_m_per_km == 0.0


def test_a_steady_climb_reads_its_grade_in_the_riding_direction():
    spoke = Spoke(leg(road(40, ele_fn=lambda k: 300.0 + k * 4.9)), controls=[])  # ~4%
    up = spoke.stretch(0.0, 3000.0)
    down = spoke.stretch(3000.0, 0.0)
    assert 3.5 < up.mean_grade_pct < 4.5 and up.grade_std_pct < 0.5
    assert down.mean_grade_pct == -up.mean_grade_pct
    assert down.length_m == up.length_m
    assert down.points == list(reversed(up.points))
    assert up.points[0][2] < up.points[-1][2]        # points come in riding order
    assert 35 < up.climb_m_per_km < 45 and down.climb_m_per_km == 0.0


def test_sub_metre_elevation_jitter_is_not_climbing():
    """The calibrated elevation model (routes/elevation.py) ignores DEM
    noise under its 1 m threshold; a Stretch ranked on summed 100 m rises
    read this road as ~3 m/km of climbing while the rider was shown 0."""
    jitter = whole(Spoke(leg(road(60, ele_fn=lambda k: 300.0 + 0.8 * (k % 2))), controls=[]))
    assert jitter.climb_m_per_km < 0.5


def test_rollers_climb_what_the_calibrated_model_says_either_way():
    """5 m rollers on a 1 km wavelength: ~10 m up per km, ridden either way
    (smoothing softens each crest a little)."""
    rollers = road(60, ele_fn=lambda k: 300.0 + 5.0 * math.sin(2 * math.pi * k * 122.0 / 1000.0))
    spoke = Spoke(leg(rollers), controls=[])
    out, back = spoke.stretch(0.0, 7000.0), spoke.stretch(7000.0, 0.0)
    assert 8.5 < out.climb_m_per_km < 10.5
    assert 8.5 < back.climb_m_per_km < 10.5


def test_stops_on_a_stretch_and_at_its_turnaround_count_with_their_weight():
    pts = road(60)                                   # ~7.2 km north
    signal = (pts[10][0], pts[10][1], 2.0)           # ~1.2 km along
    stop_sign = (pts[30][0], pts[30][1], 1.0)        # ~3.6 km along
    spoke = Spoke(leg(pts), controls=[signal, stop_sign])
    both = spoke.stretch(500.0, 4500.0)
    assert both.stops_known and both.stops == 2
    assert both.stop_weight == 3.0
    assert both.stop_weight_per_km == 3.0 / (both.length_m / 1000.0)
    # a light just past the turnaround still interrupts every Lap
    assert spoke.stretch(1300.0, 3500.0).stops == 2
    assert spoke.stretch(4500.0, 7000.0).stops == 0
    assert spoke.stretch(4500.0, 500.0).stops == 2   # either direction


def test_stops_are_unknown_not_zero_without_control_data():
    s = whole(Spoke(leg(road(40)), controls=None))
    assert s.stops_known is False
    assert s.stops == 0


def test_gravel_and_busy_road_shares_come_from_the_routers_lines():
    pts = road(60)
    gravel = [(p[0], p[1]) for p in pts[:20]]        # first ~2.4 km
    busy = [(p[0], p[1]) for p in pts[40:]]          # from ~4.9 km on
    spoke = Spoke(leg(pts, unpaved=[gravel], busy=[busy]), controls=[])
    on_gravel = spoke.stretch(0.0, 2000.0)
    assert on_gravel.gravel_share == 1.0 and on_gravel.busy_share == 0.0
    on_busy = spoke.stretch(5500.0, 7000.0)
    assert on_busy.busy_share == 1.0 and on_busy.gravel_share == 0.0
    quiet = spoke.stretch(3000.0, 4500.0)
    assert quiet.gravel_share == 0.0 and quiet.busy_share == 0.0
    half = spoke.stretch(3700.0, 6100.0)
    assert 0.4 < half.busy_share < 0.6


def climb_spoke():
    return Spoke(leg(road(40, ele_fn=lambda k: 300.0 + k * 4.9)), controls=[])  # ~4%


def test_a_lap_up_a_climb_takes_longer_than_the_lap_back_down():
    up = climb_spoke().stretch(0.0, 1500.0)
    down = climb_spoke().stretch(1500.0, 0.0)
    assert up.lap_seconds(285, 84) > up.lap_seconds(285, 84, back=True)
    assert down.lap_seconds(285, 84) == up.lap_seconds(285, 84, back=True)
    assert 3.5 * 60 < up.lap_seconds(285, 84) < 4.5 * 60  # ~1.5 km at 4%: ~22 km/h at 285 W
    assert up.lap_seconds(350, 84) < up.lap_seconds(285, 84)
    assert up.lap_seconds(285, 70) < up.lap_seconds(285, 84)


def test_a_stretch_fits_a_rep_by_lap_time_and_out_and_backs_by_the_faster_lap():
    up = climb_spoke().stretch(0.0, 1500.0)
    between = (up.lap_seconds(285, 84) + up.lap_seconds(285, 84, back=True)) / 2 / 60
    climb_plan = IntervalSpec("x", 5, between, "incline", watts=285, total_kg=84)
    either_way = IntervalSpec("x", 5, between, "any", watts=285, total_kg=84)
    assert not up.fits_rep(climb_plan)        # the climb outlasts the Rep
    assert up.fits_rep(either_way)            # the faster, downhill Lap is within it
    long_rep = IntervalSpec("x", 5, 6.0, "incline", watts=285, total_kg=84)
    assert up.fits_rep(long_rep)


def ramp_spoke():
    """~3 km, flat then a 10% ramp then flat: ~2.4% on average, but the
    ramp costs far more time than the average grade implies."""
    return Spoke(leg(road(25, ele_fn=lambda k: 300.0 + 12.2 * min(max(k - 10, 0), 5))),
                 controls=[])


def test_a_lap_over_a_ramp_takes_longer_than_steady_power_on_its_average_grade():
    from routes.power import seconds_for
    up = whole(ramp_spoke())
    assert 2.0 < up.mean_grade_pct < 3.0
    assert up.lap_seconds(285, 84) > 1.05 * seconds_for(up.length_m, up.mean_grade_pct, 285, 84)


@pytest.mark.parametrize("kind", ["incline", "any"])
def test_a_stretch_fits_a_rep_exactly_when_its_lap_does(kind):
    up = whole(ramp_spoke())
    lap = up.lap_seconds(285, 84)
    if kind == "any":
        lap = min(lap, up.lap_seconds(285, 84, back=True))
    assert up.fits_rep(IntervalSpec("x", 5, lap * 1.01 / 60, kind, watts=285, total_kg=84))
    assert not up.fits_rep(IntervalSpec("x", 5, lap * 0.99 / 60, kind, watts=285, total_kg=84))


def test_without_power_a_stretch_fits_a_rep_by_distance():
    plan = IntervalSpec("x", 2, 4.0, "flat")               # ~2.1 km at 20 mph
    spoke = Spoke(leg(road(40)), controls=[])
    assert spoke.stretch(0.0, 1900.0).fits_rep(plan)
    assert not spoke.stretch(0.0, 2400.0).fits_rep(plan)


def test_from_each_start_it_offers_the_longest_stretch_that_fits_a_rep():
    plan = IntervalSpec("x", 2, 4.0, "flat")               # ~2.1 km Reps
    spoke = Spoke(leg(road(60)), controls=[])
    offered = list(spoke.stretches_worth_trying(plan, starting_within_m=1000.0))
    starts = sorted({s.starts_at_m for s in offered})
    assert starts[0] == 0.0 and 900.0 < starts[-1] <= 1000.0
    for s in offered:
        assert s.fits_rep(plan)
        assert plan.rep_distance_m - 130.0 < s.length_m  # one ~122 m step more would not fit


def test_it_also_offers_the_stretch_cut_short_before_a_busy_road():
    plan = IntervalSpec("x", 2, 4.0, "flat")
    pts = road(60)
    busy = [(p[0], p[1]) for p in pts[12:]]                # busy from ~1.5 km
    spoke = Spoke(leg(pts, busy=[busy]), controls=[])
    from_start = [s for s in spoke.stretches_worth_trying(plan, starting_within_m=50.0)]
    assert len(from_start) == 2
    longest, cut_short = from_start
    assert longest.busy_share > 0.0
    assert cut_short.busy_share == 0.0 and 1200.0 < cut_short.length_m < 1500.0


def test_an_incline_plan_rides_a_descending_spoke_uphill():
    plan = IntervalSpec("x", 4, 4.0, "incline")
    spoke = Spoke(leg(road(60, ele_fn=lambda k: 500.0 - k * 4.9)), controls=[])
    offered = list(spoke.stretches_worth_trying(plan, starting_within_m=500.0))
    assert offered
    for s in offered:
        assert s.mean_grade_pct > 3.5
        assert s.points[0][2] < s.points[-1][2]
        assert s.starts_at_m > 1000.0      # the climb starts at its far, low end


def test_an_incline_plan_sizes_a_descending_spoke_by_its_uphill_lap():
    plan = IntervalSpec("x", 4, 4.0, "incline", watts=285, total_kg=84)
    spoke = Spoke(leg(road(60, ele_fn=lambda k: 500.0 - k * 4.9)), controls=[])  # ~4% down
    offered = list(spoke.stretches_worth_trying(plan, starting_within_m=500.0))
    assert offered
    for s in offered:
        assert s.mean_grade_pct > 3.5           # ridden uphill...
        assert s.fits_rep(plan)                 # ...and its uphill Lap fits the Rep
        assert s.lap_seconds(285, 84) > 0.9 * 4 * 60   # the longest that does


@pytest.mark.parametrize("stretch_m,rep_m,laps", [
    (10000.0, 6700.0, 1),      # holds a whole Rep
    (6700.0, 6700.0, 1),       # exactly one Rep
    (6600.0, 6700.0, 1),       # 1.5% short: Rep pace is an assumption, not
                               # a measurement, so this is still one Lap
    (6400.0, 6700.0, 1),       # 4.5% short: inside the 5% grace
    (6200.0, 6700.0, 2),       # 7.5% short: outside it, so say so
    (5500.0, 6700.0, 2),       # D18: 5.5 mi stretch, 6.7 mi Rep
    (2000.0, 6700.0, 4),
    (0.0, 6700.0, 1),          # a degenerate Stretch must not divide by zero
])
def test_laps_per_rep_by_distance_allow_a_little_grace(stretch_m, rep_m, laps):
    assert straight(stretch_m).laps_per_rep(no_power_plan(rep_m)) == laps


def test_with_power_laps_per_rep_go_by_lap_time_and_the_faster_lap_decides():
    climb = straight(1.13 * METERS_PER_MILE, 2.5)          # ~3:55 up, ~2:12 down at 285 W
    assert climb.laps_per_rep(IntervalSpec("x", 4, 4.0, "incline", watts=285)) == 1
    assert climb.laps_per_rep(IntervalSpec("x", 4, 4.0, "any", watts=285)) == 2
    # the grace is on time too: a Lap 4% short of the Rep is still one Lap
    lap = climb.lap_seconds(285)
    assert climb.laps_per_rep(IntervalSpec("x", 4, lap / 0.96 / 60, "incline", watts=285)) == 1
    assert climb.laps_per_rep(IntervalSpec("x", 4, lap / 0.94 / 60, "incline", watts=285)) == 2


def test_a_steeper_stretch_takes_longer_to_lap():
    assert straight(1200.0, 1.0).lap_seconds(285) < straight(1200.0, 5.0).lap_seconds(285)


def test_a_gentler_road_fits_a_longer_stretch_in_one_rep():
    plan = IntervalSpec("x", 4, 4.0, "incline", watts=285)
    d = plan.rep_distance_m                      # sized assuming 4%
    assert straight(d * 1.15, 3.0).fits_rep(plan)     # 3%: faster, so longer fits
    assert not straight(d * 1.15, 4.0).fits_rep(plan)
    assert not straight(d, 6.0).fits_rep(plan)        # steeper: even the sized length is too long
    guess = IntervalSpec("x", 4, 4.0, "incline")      # no watts: distance only
    assert straight(guess.rep_distance_m - 0.5, 8.0).fits_rep(guess)
    assert not straight(guess.rep_distance_m + 1, 0.0).fits_rep(guess)


def test_an_either_direction_stretch_grows_until_the_faster_lap_fills_the_rep():
    plan = IntervalSpec("x", 4, 4.0, "any", watts=285)
    L = plan.rep_distance_m                      # sized at 0%
    assert straight(L * 0.999, 0.0).fits_rep(plan)
    assert not straight(L * 1.2, 0.0).fits_rep(plan)
    # on a 2% road the descent is the faster Lap, so a LONGER Stretch still
    # fits one Rep
    assert straight(L * 1.2, 2.0).fits_rep(plan)
    assert straight(L * 1.2, -2.0).fits_rep(plan)     # which way it tilts doesn't matter
    two_pct = straight(L, 2.0)
    assert two_pct.lap_seconds(285, back=True) < two_pct.lap_seconds(285)


def test_a_road_crossing_on_a_trail_is_a_stop_but_a_crosswalk_on_the_ridden_road_is_not():
    """OSM tags both highway=crossing. Riding a trail, every road it
    crosses interrupts the Rep, signed or not; riding the road, a
    crosswalk across it is the pedestrians' stop, not the rider's."""
    from routes.interruptions import trail_crossing
    pts = road(60)                                         # ~7.2 km north
    trail = [(p[0], p[1]) for p in pts[:30]]               # the first ~3.6 km is a trail
    road_across_trail = trail_crossing(pts[12][0], pts[12][1])   # ~1.5 km along
    crosswalk = trail_crossing(pts[45][0], pts[45][1])           # ~5.5 km along, on the road
    spoke = Spoke(leg(pts, path=[trail]), controls=[road_across_trail, crosswalk])
    on_trail = spoke.stretch(500.0, 3000.0)
    assert on_trail.stops == 1 and on_trail.stop_weight == 1.0
    assert spoke.stretch(4500.0, 7000.0).stops == 0
    assert spoke.stretch(0.0, spoke.length_m).stops == 1
