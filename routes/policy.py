"""Named product tolerances. Distances are metres unless stated otherwise."""

WAYPOINT_TOLERANCE_M = 150.0  # an exact point: router snapping only
# A place a new route goes "through" is an area: passing within half its
# narrowest width of the geocoded center counts (a cafe stays at the 150 m
# floor; a loop sweeping through town need not touch the town hall). Capped
# so a sprawling county-sized match cannot excuse missing it entirely.
VIA_PLACE_MAX_TOLERANCE_M = 3000.0


def via_place_tolerance_m(extent_m: float | None) -> float:
    if extent_m is None:
        return WAYPOINT_TOLERANCE_M
    return max(WAYPOINT_TOLERANCE_M, min(extent_m / 2, VIA_PLACE_MAX_TOLERANCE_M))
AVOID_ROAD_TOLERANCE_M = 30.0  # crossing a road is allowed; riding it is not
# A stop sign, yield or rail crossing applies to the rider only when it sits
# ON the routed line: OSM maps it as a node of the road it controls, and the
# router's geometry passes through that node (40 of 40 sampled: within
# 0.5 m). Side-street signs sit a median 13 m off the main road.
CONTROL_ON_ROUTE_M = 3.0
# Signals stop every approach and are mapped at corners or per carriageway.
SIGNAL_REACH_M = 40.0
DEFAULT_AVOID_RADIUS_M = 800.0
MIN_AVOID_RADIUS_M = 50.0
MAX_AVOID_RADIUS_M = 5000.0
ROAD_LOOKUP_RADIUS_M = 12000.0
MAX_LOOP_REPEAT_FRACTION = 0.25
MAX_MAJOR_ROAD_M = 800.0
EDIT_DISTANCE_TOLERANCE = 0.15
REP_FIT_TOLERANCE = 0.05
MAX_PLACES = 8

# ---- interval Spots ----
# Controls just past a Stretch's ends still interrupt every Lap (you
# turn around there), and mapped positions carry a little noise.
CONTROL_PAD_M: float = 150.0
ON_WAY_M: float = 15.0   # a resampled point this close to a gravel/busy line is on it
TURN_DEG: float = 35.0  # a bearing change sharper than this between bins is a turn
# A Stretch is worth a Rep only when one Lap is at least this share of the
# Rep's distance and at least this long: shorter, and the Rep is mostly
# turnarounds.
MIN_STRETCH_REP_SHARE: float = 0.35
MIN_STRETCH_M: float = 400.0
# A road crossing a trail: OSM tags it highway=crossing on the node the
# path shares with the road, usually with no sign mapped. It weighs as a
# stop (1.0 in routes/interruptions.py WEIGHTS), not a yield (0.4): a
# yield on a road lets a rider merge rolling with traffic going their way,
# but crossing a road from a trail means looking both ways across every
# lane with no right of way, which at effort pace is a stop whenever
# anything is coming (the Military Ridge trail meets Cross Country Road
# near 43.0014,-89.5167 unsigned, and a rider still has to stop for a
# car). Where a crossing does have a stop sign for trail users, the two
# count as one control at the same weight.
TRAIL_CROSSING_WEIGHT: float = 1.0

PARSE_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "route": {"distance_miles": (2.0, 150.0), "max_climb_ft": (0.0, 20000.0)},
    "interval": {"reps": (1, 20), "rep_minutes": (1.0, 60.0),
                 "max_travel_minutes": (5.0, 90.0), "watts": (1.0, 1500.0),
                 "total_kg": (30.0, 250.0), "max_stops": (0, 100)},
    "edit": {"radius_m": (MIN_AVOID_RADIUS_M, MAX_AVOID_RADIUS_M),
             "miles_delta": (0.0, 50.0), "target_miles": (2.0, 150.0)},
}
