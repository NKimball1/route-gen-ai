"""A real ride as a test case: five 4-minute VO2 reps up White Crossing Road.

The interval finder picked this climb (Town of Verona, Military Ridge Trail
crossing toward Dairy Ridge Road) for 4-minute efforts; the rider then rode
it 5x4 at ~300 W with a power meter and barometric altimeter
(tests/fixtures/white_crossing_5x4.json, trimmed to the reps). Every rep
is ground truth for the estimates the finder makes before anyone rides:

- the power model (routes/power.py): fed the rider's own second-by-second
  power over the barometric profile, it lands within 5% of the real time;
- the finder's Lap time (steady power along the Stretch's grade profile):
  within 6% of every clean rep (the old average-grade shortcut was 8-10%
  fast), so a Rep of the real time is one Lap;
- the router's elevation (live, opt-in): within 2.5 m of the altimeter's
  net climb on every rep.

Rep 1 was slowed by traffic near the Gust Road junction and is kept out of
the timing checks.

Live part, opt-in (needs the self-hosted BRouter on BROUTER_URL):
    RUN_ROUTER_TESTS=1 python -m pytest tests/test_ride_white_crossing.py -v
"""
import json
import math
import os

import pytest

from routes.intervals import IntervalSpec
from routes.power import seconds_for
from routes.stretch import Spoke

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "white_crossing_5x4.json")
with open(FIXTURE, encoding="utf-8") as fixture_file:
    RIDE = json.load(fixture_file)
COL = {name: i for i, name in enumerate(RIDE["columns"])}
KG = RIDE["total_kg"]
CLEAN = [rep for rep in RIDE["reps"] if "note" not in rep]


def col(rep, name):
    return [row[COL[name]] for row in rep["rows"]]


def ridden_seconds(rep):
    return len(rep["rows"]) - 1          # 1 Hz samples


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def test_the_fixture_is_the_ride_the_finder_proposed():
    assert len(RIDE["reps"]) == 5 and len(CLEAN) == 4
    for rep in RIDE["reps"]:
        start = (rep["rows"][0][COL["lat"]], rep["rows"][0][COL["lon"]])
        end = (rep["rows"][-1][COL["lat"]], rep["rows"][-1][COL["lon"]])
        assert hav(start, RIDE["trail_crossing"]) < 25          # started at the trail crossing
        assert 0 < hav(end, RIDE["dairy_ridge_junction"]) < 200   # 4:00 ran out before the junction
        assert 238 <= ridden_seconds(rep) <= 242
        assert 290 <= sum(col(rep, "power_w")) / len(rep["rows"]) <= 310


@pytest.mark.parametrize("rep", CLEAN, ids=lambda r: f"rep{r['rep']}")
def test_power_model_with_the_riders_own_power_matches_the_clock(rep):
    """Second by second, with measured power and barometric grade, the
    physics is within 5% (measured -1.6% to -4.5%). The remaining gap is
    road surface or riding position; one direction at one speed cannot
    separate them, so the defaults stay until a second ride can."""
    dist, alt, watts = col(rep, "dist_m"), col(rep, "baro_alt_m"), col(rep, "power_w")
    model = 0.0
    for i in range(len(dist) - 1):
        d = dist[i + 1] - dist[i]
        if d <= 0:
            continue
        grade = max(-10.0, min(10.0, (alt[i + 1] - alt[i]) / d * 100))
        model += seconds_for(d, grade, max(watts[i], 1), KG)
    error = (model - ridden_seconds(rep)) / ridden_seconds(rep)
    assert abs(error) < 0.05, f"{error:+.1%}"


LAP_TOLERANCE = 0.06   # a Lap time within 6% of the rider's real Rep time


@pytest.mark.parametrize("rep", CLEAN, ids=lambda r: f"rep{r['rep']}")
def test_the_finders_lap_time_matches_the_real_rep(rep):
    """Each rep measured as a Stretch, the way the finder measures one:
    a Spoke built from the ridden road (barometric elevation), the longest
    Stretch along it, and its Lap time at the power the rider held over
    that Stretch, against the seconds the rider took over the same road.
    (A Stretch ends on the Spoke's ~100 m measuring points, so it can stop
    up to ~90 m short of the rep's end; timing the same road keeps that
    out of the comparison.)

    A Lap time is steady power along the Stretch's grade profile (its
    ~100 m pieces). Steady power on the AVERAGE grade, the old shortcut,
    was 8.4-10.5% fast on every clean rep: White Crossing climbs at ~2.6%
    on average but through a 10% ramp, and the time lost on the steep
    parts is not won back on the gentle ones. Along the profile: measured
    -3.1% to -4.9%, the same few percent fast as the power model fed the
    rider's own second-by-second power (above), which is the physics
    defaults' share and out of reach here.

    Each rep starts from a rolling recovery (~16 km/h), but a start-up
    cost is not modeled: simulating the rep with momentum from 16 km/h
    lands within 0.6 points of the profile alone (the speed lost getting
    up to pace is about what momentum carries into the ramp, which the
    steady-state profile ignores)."""
    road = [(lat, lon, alt) for lat, lon, alt
            in zip(col(rep, "lat"), col(rep, "lon"), col(rep, "baro_alt_m"))]
    spoke = Spoke({"points": road, "distance_m": 0.0, "ascent_m": 0.0, "major_m": 0.0},
                  controls=[])
    stretch = spoke.stretch(0.0, spoke.length_m)
    assert stretch.points[0] == road[0]
    rows = len(stretch.points)                 # the rep's rows over the Stretch
    ridden = rows - 1                          # 1 Hz samples
    held = sum(col(rep, "power_w")[:rows]) / rows
    error = (stretch.lap_seconds(held, KG) - ridden) / ridden
    assert abs(error) < LAP_TOLERANCE, f"{error:+.1%}"
    # and a Rep of the time the rider took is filled by that Lap
    plan = IntervalSpec("White Crossing", 5, ridden / 60.0, "incline",
                        watts=held, total_kg=KG)
    assert stretch.laps_per_rep(plan) == 1


@pytest.mark.skipif(os.environ.get("RUN_ROUTER_TESTS") != "1",
                    reason="live router; set RUN_ROUTER_TESTS=1 with BRouter running")
@pytest.mark.parametrize("rep", RIDE["reps"], ids=lambda r: f"rep{r['rep']}")
def test_router_elevation_matches_the_altimeter(rep):
    """Measured 2026-10-09: within 1.6 m of net climb on every rep."""
    from dotenv import load_dotenv
    load_dotenv()
    from routes.providers import BRouterProvider

    rows = rep["rows"]
    waypoints = [(r[COL["lat"]], r[COL["lon"]]) for r in rows[::20]] + [(rows[-1][COL["lat"]], rows[-1][COL["lon"]])]
    leg = BRouterProvider().route(waypoints)
    assert leg is not None, "router did not answer"
    ele = [p[2] for p in leg["points"] if p[2] is not None]
    baro_net = rows[-1][COL["baro_alt_m"]] - rows[0][COL["baro_alt_m"]]
    assert abs((ele[-1] - ele[0]) - baro_net) <= 2.5
