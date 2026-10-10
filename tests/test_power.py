"""Rider physics (routes/power.py) and power-based interval sizing.
Lap time and fills-a-Rep are Stretch facts: tests/test_spoke.py."""
import math

from routes.intervals import IntervalSpec
from routes.power import mmss, seconds_for, speed_mps
from routes.spec import METERS_PER_MILE

MPH = METERS_PER_MILE / 3600.0   # m/s per mph


def test_flat_speed_at_threshold_power_is_a_real_riders_number():
    # ~285 W on the flat, hoods, 84 kg all-in: low-to-mid 20s mph
    v = speed_mps(285, 0.0, 84.0) / MPH
    assert 21.0 < v < 26.0, v


def test_grade_slows_you_down_monotonically():
    speeds = [speed_mps(285, g, 84.0) for g in (0, 2, 4, 6, 8)]
    assert speeds == sorted(speeds, reverse=True)
    assert 12.0 < speeds[2] / MPH < 16.0   # 4%: mid-teens mph


def test_more_watts_more_speed_and_heavier_is_slower_uphill():
    assert speed_mps(320, 4.0, 84.0) > speed_mps(285, 4.0, 84.0)
    assert speed_mps(285, 4.0, 75.0) > speed_mps(285, 4.0, 95.0)


def test_downhill_still_solves():
    v = speed_mps(150, -3.0, 84.0)
    assert 25.0 < v / MPH < 35.0   # ~coasting terminal speed on -3%, plus a little


def test_zero_power_is_stopped():
    assert speed_mps(0, 0.0) == 0.0
    assert math.isinf(seconds_for(1000, 0.0, 0))
    assert mmss(math.inf) == "--:--"


def test_the_phase_3_napkin_math_case():
    # devlog phase 3: 0.9 mi @ 2.8% at 300 W lasts ~3:10, not the 4-5
    # minutes the fixed 11 mph guess implied
    t = seconds_for(0.9 * METERS_PER_MILE, 2.8, 300, 84.0)
    assert 170 < t < 215, mmss(t)


def test_watts_size_the_rep_and_the_default_still_works():
    guess = IntervalSpec("x", 4, 4.0, "incline")
    powered = IntervalSpec("x", 4, 4.0, "incline", watts=285)
    assert guess.rep_distance_m == 4.0 * 11.0 / 60.0 * METERS_PER_MILE
    # 4 min at 285 W into 4%: about 0.9-1.1 mi, more than the 0.73 mi guess
    assert 0.85 * METERS_PER_MILE < powered.rep_distance_m < 1.1 * METERS_PER_MILE
    flat = IntervalSpec("x", 4, 4.0, "flat", watts=285)
    assert flat.rep_distance_m > powered.rep_distance_m


def test_lap_times_read_as_minutes_and_seconds():
    assert mmss(125) == "2:05"


def test_kind_any_prefers_a_stretch_that_works_both_ways():
    """Ranked on how evenly its two Laps (the Stretch's own Lap times,
    the ones the rider is shown) take the Rep."""
    from routes.intervals import _rank
    from routes.spec import EARTH_RADIUS_M
    from routes.stretch import Spoke

    def straight(length_m, grade_pct):
        north = (43.0 + math.degrees(length_m / EARTH_RADIUS_M), -89.5,
                 300.0 + length_m * grade_pct / 100.0)
        spoke = Spoke({"points": [(43.0, -89.5, 300.0), north], "distance_m": 0.0,
                       "ascent_m": 0.0, "major_m": 0.0}, controls=[])
        return spoke.stretch(0.0, spoke.length_m)

    spec = IntervalSpec("x", 4, 4.0, "any", watts=285)
    L = spec.rep_distance_m
    flat, rolling, hill = (_rank(spec, straight(L, g)) for g in (0.0, 1.5, 4.0))
    assert flat > rolling > hill
    # a 4% hill is the INCLINE ideal, but for "either way" it is a poor spot
    assert flat - hill > 0.08
