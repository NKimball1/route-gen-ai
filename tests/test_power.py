"""Rider physics (routes/power.py) and power-based interval sizing."""
import math

from routes.intervals import IntervalSpec, IntervalSpot
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


def test_spot_times_itself_at_its_own_grade():
    spot = IntervalSpot(points=[], length_m=1200.0, mean_grade_pct=5.0)
    steep = spot.seconds_at(285)
    spot.mean_grade_pct = 1.0
    assert spot.seconds_at(285) < steep
    assert mmss(125) == "2:05"


def test_window_grows_by_time_on_a_gentler_road():
    spec = IntervalSpec("x", 4, 4.0, "incline", watts=285)
    d = spec.rep_distance_m                 # sized assuming 4%
    assert spec.rep_fits(d * 1.15, 3.0)     # 3%: faster, so a longer stretch fits
    assert not spec.rep_fits(d * 1.15, 4.0)
    assert not spec.rep_fits(d, 6.0)        # steeper: even the sized length is too long
    guess = IntervalSpec("x", 4, 4.0, "incline")
    assert guess.rep_fits(guess.rep_distance_m, 8.0)   # no watts: distance only
    assert not guess.rep_fits(guess.rep_distance_m + 1, 0.0)


def test_kind_any_prefers_a_stretch_that_works_both_ways():
    from routes.intervals import _score
    spec = IntervalSpec("x", 4, 4.0, "any", watts=285)
    L = spec.rep_distance_m
    flat = _score(spec, L, 0.0, 0.5, 0.0)
    rolling = _score(spec, L, 1.5, 0.5, 0.0)
    hill = _score(spec, L, 4.0, 0.5, 0.0)
    assert flat > rolling > hill
    # a 4% hill is the INCLINE ideal, but for "either way" it is a poor spot
    assert flat - hill > 0.08


def test_kind_any_window_fills_the_rep_in_the_faster_direction():
    spec = IntervalSpec("x", 4, 4.0, "any", watts=285)
    L = spec.rep_distance_m            # sized at 0%
    assert spec.rep_fits(L, 0.0)
    assert not spec.rep_fits(L * 1.2, 0.0)
    # on a 2% road the descent is the faster pass, so a LONGER stretch still
    # fits one rep -- the window keeps growing until the descent fills it
    assert spec.rep_fits(L * 1.2, 2.0)
    assert spec.rep_fits(L * 1.2, -2.0)   # sign of the grade doesn't matter
    spot = IntervalSpot(points=[], length_m=L, mean_grade_pct=2.0)
    assert spot.seconds_at(285, reverse=True) < spot.seconds_at(285)
