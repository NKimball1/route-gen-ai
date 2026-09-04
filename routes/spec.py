"""Route request spec and candidate result types.

The spec is what the LLM will eventually produce from a natural-language
request ("give me a ~30 mile loop with less than 1000 ft of climbing").
For the prototype it is built directly from CLI flags.
"""
from dataclasses import dataclass, field

METERS_PER_MILE = 1609.344
METERS_PER_FOOT = 0.3048


@dataclass
class RouteSpec:
    address: str
    distance_m: float
    max_ascent_m: float | None = None   # hard ceiling on climbing
    maximize_ascent: bool = False       # rank by most climbing
    minimize_ascent: bool = False       # rank by least climbing
    distance_tolerance: float = 0.15    # accept candidates within ±15% of target
    shape: str = "loop"                 # "loop" or "outback"
    avoid: list = field(default_factory=list)  # (lat, lon, radius_m) no-go circles
    via: list = field(default_factory=list)    # (lat, lon) places to pass through
    via_names: list = field(default_factory=list)

    @classmethod
    def from_imperial(cls, address: str, miles: float,
                      max_climb_ft: float | None = None,
                      maximize_climb: bool = False,
                      shape: str = "loop",
                      avoid: list | None = None,
                      minimize_climb: bool = False,
                      via: list | None = None,
                      via_names: list | None = None) -> "RouteSpec":
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
    points: list[tuple[float, float, float | None]] = field(repr=False, default_factory=list)
    # points are (lat, lon, elevation_m or None)

    @property
    def distance_mi(self) -> float:
        return self.distance_m / METERS_PER_MILE

    @property
    def ascent_ft(self) -> float:
        return self.ascent_m / METERS_PER_FOOT
