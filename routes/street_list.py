"""Route along a named sequence of streets.

    "Capital City Trail -> Dempsey Rd -> Davies St -> Major Ave -> ..."

Some routes are defined by their roads, not their shape: an official
lake loop, a club's standard ride, the way a local actually goes. Waypoints
guessed from a map get those wrong in ways a rider spots instantly (a trail
you can't ride, the arterial the route exists to avoid). Street names are
exact, so this module takes them literally:

1. Fetch each street's real geometry from OpenStreetMap, restricted to the
   area around the ride so same-named roads elsewhere never match.
2. Find where each street meets the next (the closest approach of the two
   geometries; streets usually intersect, sometimes via a short unnamed
   connector).
3. Put waypoints ON each street -- entry, middle, exit -- so the router has
   to ride along it, never through a park or down a side street to reach
   an off-road point. No waypoint is protected from spur trimming.
4. Route through them, then MEASURE how much of each named street the
   result actually rides. A street the route skipped is reported, not
   papered over.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import requests

from routes.editing import _cum, _dist_m
from routes.geocode import NOMINATIM_URL, USER_AGENT
from routes.road_avoid import Way, on_road_meters
from routes.spec import (METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ, Coord,
                         LatLon, Router, Track)

# Streets must meet within this to be consecutive in a ride; beyond it the
# list has a gap (a typo, a missing street, or two roads that never touch).
MAX_JUNCTION_GAP_M: float = 250.0
# Streets are searched close to where the ride already is, widening only
# when nothing turns up: "Main St" resolves to the one you mean, not the
# one two towns over -- and a long trail made of dozens of OSM ways isn't
# cut off by the geocoder's result cap before the segment you need.
SEARCH_RADII_M: tuple[float, ...] = (3000.0, 8000.0, 25000.0)
SEARCH_RADIUS_M: float = SEARCH_RADII_M[-1]
# A street counts as ridden once the route runs along it this far, or half
# the entry-to-exit span for very short streets.
MIN_RIDDEN_M: float = 60.0
# Waypoints closer than this collapse into one (junction == street end).
SAME_POINT_M: float = 25.0

Fetcher = Callable[[str, LatLon, float], list[Way]]


@dataclass
class StreetLeg:
    """One named street in the sequence, and how the route used it."""
    name: str
    ways: list[Way] = field(repr=False, default_factory=list)
    entry: LatLon | None = None
    exit: LatLon | None = None
    ridden_m: float = 0.0

    @property
    def span_m(self) -> float:
        """Straight-line distance between where the ride joins and leaves."""
        if self.entry is None or self.exit is None:
            return 0.0
        return _dist_m(self.entry, self.exit)

    @property
    def ridden(self) -> bool:
        return self.ridden_m >= min(MIN_RIDDEN_M, 0.5 * self.span_m)


@dataclass
class StreetRoute:
    points: Track
    streets: list[StreetLeg]
    problems: list[str]

    @property
    def distance_m(self) -> float:
        return _cum(self.points)[-1] if len(self.points) > 1 else 0.0

    @property
    def ok(self) -> bool:
        return not self.problems


def parse_street_list(text: str) -> list[str]:
    """'A -> B -> C', 'A; B; C', 'A, then B, then C' -> ['A', 'B', 'C']."""
    for sep in ("->", "→", ";", "\n"):
        text = text.replace(sep, "|")
    text = text.replace(", then ", "|").replace(" then ", "|")
    return [s.strip(" ,.") for s in text.split("|") if s.strip(" ,.")]


# ---- geometry ----

def _local_xy(p: Coord, origin: Coord) -> tuple[float, float]:
    kx = METERS_PER_DEG_LON_EQ * math.cos(math.radians(origin[0]))
    return (p[1] - origin[1]) * kx, (p[0] - origin[0]) * METERS_PER_DEG_LAT


def nearest_on_ways(p: Coord, ways: Sequence[Way]) -> tuple[LatLon, float]:
    """The closest point to p on any segment of `ways`, and its distance."""
    best: LatLon = ways[0][0]
    best_d = math.inf
    for way in ways:
        for a, b in zip(way, way[1:]):
            ax, ay = _local_xy(a, p)
            bx, by = _local_xy(b, p)
            vx, vy = bx - ax, by - ay
            seg2 = vx * vx + vy * vy
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / seg2))
            d = math.hypot(ax + t * vx, ay + t * vy)
            if d < best_d:
                best_d = d
                best = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        if len(way) == 1:
            d = _dist_m(p, way[0])
            if d < best_d:
                best, best_d = way[0], d
    return best, best_d


def junction(a: Sequence[Way], b: Sequence[Way]) -> tuple[LatLon, float]:
    """Where street a meets street b: the point on b closest to a (and the
    gap between them -- ~0 when they intersect)."""
    best: LatLon = b[0][0]
    best_d = math.inf
    for way in a:
        for p in way:
            q, d = nearest_on_ways(p, b)
            if d < best_d:
                best, best_d = q, d
    for way in b:
        for p in way:
            _, d = nearest_on_ways(p, a)
            if d < best_d:
                best, best_d = p, d
    return best, best_d


def farthest_node(ways: Sequence[Way], from_pt: LatLon) -> LatLon:
    """The end of a street far from where the ride is: ride all of it."""
    return max((p for w in ways for p in w), key=lambda p: _dist_m(p, from_pt))


# ---- fetching street geometry ----

def fetch_street_ways(name: str, near: LatLon,
                      radius_m: float = SEARCH_RADIUS_M) -> list[Way]:
    """Every OSM way named `name` within radius_m of `near` (roads and paths
    only). Nominatim returns one hit per way when dedupe is off."""
    dlat = radius_m / METERS_PER_DEG_LAT
    dlon = radius_m / (METERS_PER_DEG_LON_EQ * math.cos(math.radians(near[0])))
    params: dict[str, str | int] = {
        "q": name, "format": "jsonv2", "polygon_geojson": 1, "limit": 50,
        "dedupe": 0, "bounded": 1,
        "viewbox": f"{near[1] - dlon},{near[0] + dlat},{near[1] + dlon},{near[0] - dlat}",
    }
    hits: list[dict[str, Any]] = []
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            resp = requests.get(NOMINATIM_URL, params=params,
                                headers={"User-Agent": USER_AGENT}, timeout=30)
            if resp.status_code in (429, 502, 503, 504):
                continue
            resp.raise_for_status()
            hits = resp.json()
            break
        except (requests.ConnectionError, requests.Timeout):
            continue
    ways: list[Way] = []
    for h in hits:
        if h.get("category") != "highway":
            continue
        g = h.get("geojson") or {}
        if g.get("type") == "LineString":
            ways.append([(c[1], c[0]) for c in g["coordinates"]])
        elif g.get("type") == "MultiLineString":
            ways += [[(c[1], c[0]) for c in line] for line in g["coordinates"]]
    time.sleep(1.1)   # Nominatim usage policy: at most 1 request/second
    return ways


def _fetch_near(name: str, anchor: LatLon, fetch: Fetcher) -> list[Way]:
    for radius in SEARCH_RADII_M:
        ways = fetch(name, anchor, radius)
        if ways:
            return ways
    return []


def fetch_streets(names: Sequence[str], anchor: LatLon,
                  fetch: Fetcher = fetch_street_ways) -> list[StreetLeg]:
    """Geometry for each street, searched where the ride will actually be:
    each street near where the previous one was joined. Then any pair that
    still seems not to meet is re-fetched right next to each other -- the
    usual cause is a long street whose nearby segment didn't make the
    geocoder's result cap, not two roads that truly never touch."""
    streets: list[StreetLeg] = []
    here = anchor
    for name in names:
        ways = _fetch_near(name, here, fetch)
        streets.append(StreetLeg(name, ways))
        if ways:
            here = nearest_on_ways(here, ways)[0]
    for a, b in zip(streets, streets[1:]):
        if not a.ways or not b.ways or junction(a.ways, b.ways)[1] <= MAX_JUNCTION_GAP_M:
            continue
        a.ways = a.ways + fetch(a.name, junction(a.ways, b.ways)[0], SEARCH_RADII_M[0])
        b.ways = b.ways + fetch(b.name, junction(b.ways, a.ways)[0], SEARCH_RADII_M[0])
    return streets


# ---- planning and building ----

def plan_waypoints(streets: list[StreetLeg], start: LatLon | None,
                   end: LatLon | None) -> tuple[list[LatLon], list[str]]:
    """Waypoints that force the route along each street in order, plus any
    problems found (a street with no geometry, two streets that never meet).
    Fills in each StreetLeg's entry and exit."""
    problems: list[str] = []
    usable = [s for s in streets if s.ways]
    for s in streets:
        if not s.ways:
            problems.append(f"couldn't find {s.name!r} near the ride")
    if not usable:
        return [], problems

    joins: list[LatLon] = []
    for a, b in zip(usable, usable[1:]):
        pt, gap = junction(a.ways, b.ways)
        if gap > MAX_JUNCTION_GAP_M:
            problems.append(f"{a.name!r} and {b.name!r} don't meet "
                            f"(closest {gap:.0f} m apart)")
        joins.append(pt)

    first, last = usable[0], usable[-1]
    first.entry = (nearest_on_ways(start, first.ways)[0] if start is not None
                   else farthest_node(first.ways, joins[0] if joins else first.ways[0][0]))
    for i, s in enumerate(usable):
        if i > 0:
            s.entry = joins[i - 1]
        if i < len(joins):
            s.exit = joins[i]
    anchor = last.entry if last.entry is not None else last.ways[0][0]
    last.exit = (nearest_on_ways(end, last.ways)[0] if end is not None
                 else farthest_node(last.ways, anchor))

    wps: list[LatLon] = [start] if start is not None else []
    for i, s in enumerate(usable):
        assert s.entry is not None and s.exit is not None
        mid = nearest_on_ways(((s.entry[0] + s.exit[0]) / 2,
                               (s.entry[1] + s.exit[1]) / 2), s.ways)[0]
        # With a start address, the first street's "entry" is just the spot
        # nearest a geocoded point -- often a park or building centroid the
        # router can't reach directly. Pinning it made the route join the
        # street elsewhere, ride back to the pin and turn around (0.7 mi of
        # spur from Olbrich Park). Mid and exit still force the street; the
        # router picks its own way onto it.
        pins = (mid, s.exit) if i == 0 and start is not None else (s.entry, mid, s.exit)
        for p in pins:
            if not wps or _dist_m(wps[-1], p) > SAME_POINT_M:
                wps.append(p)
    if end is not None and _dist_m(wps[-1], end) > SAME_POINT_M:
        wps.append(end)
    return wps, problems


def build_street_route(names: Sequence[str], router: Router,
                       start: LatLon | None = None, end: LatLon | None = None,
                       near: LatLon | None = None,
                       fetch: Fetcher = fetch_street_ways) -> StreetRoute:
    """Route along `names` in order. `start`/`end` optionally add a lead-in
    and a finish; `near` anchors the street search when neither is given."""
    anchor = start or end or near
    if anchor is None:
        raise ValueError("need a start, an end, or a 'near' point to find the streets")
    streets = fetch_streets(names, anchor, fetch)
    wps, problems = plan_waypoints(streets, start, end)
    if len(wps) < 2:
        return StreetRoute([], streets, problems or ["not enough of the list to route"])
    leg = router.route(wps)
    if leg is None:
        return StreetRoute([], streets, problems + ["the router couldn't connect those streets"])
    points = leg["points"]
    for s in streets:
        if s.ways:
            s.ridden_m = on_road_meters(points, s.ways)
            if not s.ridden:
                problems.append(f"the route doesn't actually ride {s.name!r} "
                                f"({s.ridden_m:.0f} m on it)")
    return StreetRoute(points, streets, problems)
