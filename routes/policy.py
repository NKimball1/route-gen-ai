"""Named product tolerances. Distances are metres unless stated otherwise."""

MIN_AVOID_RADIUS_M = 50.0
MAX_AVOID_RADIUS_M = 5000.0
EDIT_DISTANCE_TOLERANCE = 0.15
REP_FIT_TOLERANCE = 0.05
MAX_PLACES = 8

PARSE_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "route": {"distance_miles": (2.0, 150.0), "max_climb_ft": (0.0, 20000.0)},
    "interval": {"reps": (1, 20), "rep_minutes": (1.0, 60.0),
                 "max_travel_minutes": (5.0, 90.0), "watts": (1.0, 1500.0),
                 "total_kg": (30.0, 250.0), "max_stops": (0, 100)},
    "edit": {"radius_m": (MIN_AVOID_RADIUS_M, MAX_AVOID_RADIUS_M),
             "miles_delta": (0.0, 50.0), "target_miles": (2.0, 150.0)},
}
