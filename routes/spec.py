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
    maximize_ascent: bool = False       # rank by climbing instead of distance fit
    distance_tolerance: float = 0.15    # accept candidates within ±15% of target
    shape: str = "loop"                 # "loop" or "outback"

    @classmethod
    def from_imperial(cls, address: str, miles: float,
                      max_climb_ft: float | None = None,
                      maximize_climb: bool = False,
                      shape: str = "loop") -> "RouteSpec":
        return cls(
            address=address,
            distance_m=miles * METERS_PER_MILE,
            max_ascent_m=(max_climb_ft * METERS_PER_FOOT
                          if max_climb_ft is not None else None),
            maximize_ascent=maximize_climb,
            shape=shape,
        )


@dataclass
class RouteCandidate:
    provider: str
    seed: str                       # provider-specific identity (bearing, seed number...)
    distance_m: float
    ascent_m: float
    shape: str = "loop"
    points: list[tuple[float, float, float | None]] = field(repr=False, default_factory=list)
    # points are (lat, lon, elevation_m or None)

    @property
    def distance_mi(self) -> float:
        return self.distance_m / METERS_PER_MILE

    @property
    def ascent_ft(self) -> float:
        return self.ascent_m / METERS_PER_FOOT
