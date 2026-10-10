"""Route request spec and candidate result types.

The application builds specs from validated natural-language requests or
direct CLI arguments. Providers produce candidates; scoring verifies them.
"""
from dataclasses import dataclass, field
import math
from typing import Literal, NotRequired, Protocol, Sequence, TypedDict

from routes.policy import WAYPOINT_TOLERANCE_M

# ---- shared geometry types: signatures read as cycling, not tuple soup ----
LatLon = tuple[float, float]                # (lat, lon), degrees
Point = tuple[float, float, float | None]   # (lat, lon, elevation m or None)
Track = list[Point]                         # a polyline, in ride order
Coord = LatLon | Point                      # anything that starts lat, lon
NoGo = tuple[float, float, float]           # (lat, lon, radius m) avoid circle
BBox = tuple[float, float, float, float]    # (minlat, minlon, maxlat, maxlon)
# A resampled point that carries its own road distance; elevation is
# never None here (resampling drops points without it).
Sample = tuple[float, float, float, float]  # (lat, lon, ele m, cum m)
# How a request turned out: True | "partial" | False drives the UI's
# green / amber / red banner.
Outcome = bool | Literal["partial"]


class Leg(TypedDict):
    """One routed point-to-point leg, spurs already trimmed."""
    points: Track
    distance_m: float
    ascent_m: float
    major_m: float      # meters on motorway/trunk/primary roads
    # unpaved stretches (gravel, compacted, dirt, ...) as polylines along
    # the leg; routers that can't tell surfaces simply leave it out
    unpaved: NotRequired[list[list[LatLon]]]
    # stretches on busy roads (secondary and bigger), same shape
    busy: NotRequired[list[list[LatLon]]]
    # stretches ridden on a path or trail (cycleway, path, footway,
    # bridleway), same shape: where a road crossing interrupts the rider
    path: NotRequired[list[list[LatLon]]]


class ClimbRow(TypedDict):
    """One sustained ascent found in an elevation profile."""
    start: LatLon
    end: LatLon
    gain_m: float
    length_m: float
    avg_grade_pct: float
    name: NotRequired[str]   # set when the climb tops out at a named peak


class Router(Protocol):
    """Anything that can route between waypoints. The editing operations
    depend on this, not on BRouter — tests pass a fake."""
    def route(self, waypoints: Sequence[LatLon],
              avoid: Sequence[NoGo] | None = None,
              protect: Sequence[LatLon] | None = None) -> Leg | None: ...


METERS_PER_MILE: float = 1609.344
METERS_PER_FOOT: float = 0.3048

# ---- geodesy constants (every module doing local geometry uses these) ----
# Meters per one degree of latitude — near-constant everywhere on Earth.
METERS_PER_DEG_LAT: float = 110540.0
# Meters per one degree of longitude AT THE EQUATOR; the local east-west
# value shrinks with latitude, so usages multiply this by cos(latitude).
METERS_PER_DEG_LON_EQ: float = 111320.0
# Mean Earth radius, for haversine great-circle distances.
EARTH_RADIUS_M: float = 6371000.0

# Below this many meters on major highways, result tables show '0' --
# crossing a highway at an intersection is not riding it.
MAJOR_DISPLAY_MIN_M: float = 50.0


@dataclass
class AvoidRoad:
    name: str
    ways: list[list[LatLon]]


@dataclass
class RouteSpec:
    address: str
    distance_m: float
    max_ascent_m: float | None = None   # hard ceiling on climbing
    maximize_ascent: bool = False       # rank by most climbing
    minimize_ascent: bool = False       # rank by least climbing
    distance_tolerance: float = 0.15    # accept candidates within ±15% of target
    shape: str = "loop"                 # "loop" or "outback"
    avoid: list[NoGo] = field(default_factory=list)   # no-go circles
    via: list[LatLon] = field(default_factory=list)   # places to pass through
    via_names: list[str] = field(default_factory=list)
    # per via: how close counts as passing through it (see policy); vias
    # without an entry are exact points
    via_tolerance_m: list[float] = field(default_factory=list)
    avoid_roads: list[AvoidRoad] = field(default_factory=list)
    avoid_areas: list[NoGo] | None = None  # None: legacy avoid circles are all areas
    use_strava: bool = False  # explicit personal CLI opt-in; never enabled by web

    def __post_init__(self) -> None:
        if not math.isfinite(self.distance_m) or self.distance_m <= 0:
            raise ValueError("Route distance must be finite and positive.")
        if not math.isfinite(self.distance_tolerance) or not 0 < self.distance_tolerance < 1:
            raise ValueError("Distance tolerance must be between zero and one.")
        if self.max_ascent_m is not None and (not math.isfinite(self.max_ascent_m) or self.max_ascent_m < 0):
            raise ValueError("Climb limit must be finite and nonnegative.")
        if self.maximize_ascent and self.minimize_ascent:
            raise ValueError("Choose either maximum or minimum climbing.")
        if self.shape not in {"loop", "outback", "both"}:
            raise ValueError("Unsupported route shape.")

    def via_tolerances(self) -> list[float]:
        """How close counts as passing through each via."""
        given = self.via_tolerance_m
        return [given[i] if i < len(given) else WAYPOINT_TOLERANCE_M
                for i in range(len(self.via))]

    @classmethod
    def from_imperial(cls, address: str, miles: float,
                      max_climb_ft: float | None = None,
                      maximize_climb: bool = False,
                      shape: str = "loop",
                      avoid: list[NoGo] | None = None,
                      minimize_climb: bool = False,
                      via: list[LatLon] | None = None,
                      via_names: list[str] | None = None) -> "RouteSpec":
        return cls(
            address=address,
            distance_m=miles * METERS_PER_MILE,
            max_ascent_m=(max_climb_ft * METERS_PER_FOOT
                          if max_climb_ft is not None else None),
            maximize_ascent=maximize_climb,
            minimize_ascent=minimize_climb,
            shape=shape,
            avoid=list(avoid or []),
            via=list(via or []),
            via_names=list(via_names or []),
        )


@dataclass
class RouteCandidate:
    provider: str
    seed: str                       # provider-specific identity (bearing, seed number...)
    distance_m: float
    ascent_m: float
    shape: str = "loop"
    overlap_frac: float = 0.0  # fraction riding the same road twice (loops)
    natural: bool = False      # passes through vias organically, not anchored
    major_m: float | None = 0.0  # None means this provider cannot measure it
    points: Track = field(repr=False, default_factory=list)
    gpx_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def distance_mi(self) -> float:
        return self.distance_m / METERS_PER_MILE

    @property
    def ascent_ft(self) -> float:
        return self.ascent_m / METERS_PER_FOOT
