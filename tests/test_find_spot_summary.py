"""The 'Best:' line a rider plans a session around (find_spot.best_summary)."""
from find_spot import best_summary
from routes.intervals import IntervalSpec, Spot
from routes.spec import METERS_PER_MILE
from tests.test_spoke import straight


def spot(mi: float, grade: float, known: bool = True) -> Spot:
    return Spot(straight(mi * METERS_PER_MILE, grade, controls=[] if known else None),
                dist_from_start_m=6 * METERS_PER_MILE)


def test_either_way_reports_both_directions():
    line = best_summary(IntervalSpec("x", 4, 4.0, "any", watts=285),
                        spot(1.13, 2.5))
    assert " out / " in line and " back at 285 W" in line
    # the 2:12 descent can't fill a 4-min Rep: two Laps
    assert "~2 laps per rep" in line


def test_a_flat_stretch_that_fills_the_rep_needs_no_laps():
    line = best_summary(IntervalSpec("x", 4, 4.0, "any", watts=285),
                        spot(1.58, 0.0))
    assert "laps" not in line


def test_one_direction_kinds_report_one_time():
    line = best_summary(IntervalSpec("x", 4, 4.0, "incline", watts=285),
                        spot(1.13, 2.5))
    assert "back" not in line and "one pass takes 3:5" in line


def test_unknown_stop_counts_are_called_out():
    line = best_summary(IntervalSpec("x", 2, 20.0, "flat"), spot(6.0, 0.0, False))
    assert "UNKNOWN" in line
