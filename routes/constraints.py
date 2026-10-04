"""Resolve avoidance once; generation and editing use the same road data."""
from dataclasses import dataclass, field
from routes.geocode import geocode_flexible
from routes.policy import (DEFAULT_AVOID_RADIUS_M, MIN_AVOID_RADIUS_M,
                           MAX_AVOID_RADIUS_M, ROAD_LOOKUP_RADIUS_M)
from routes.road_avoid import fetch_road, looks_like_road, road_nogos
from routes.spec import AvoidRoad, BBox, LatLon, NoGo


class ConstraintUnavailable(ValueError):
    """A promised constraint cannot be verified with the available data."""


@dataclass
class Avoidance:
    routing_zones: list[NoGo] = field(default_factory=list)
    areas: list[NoGo] = field(default_factory=list)
    roads: list[AvoidRoad] = field(default_factory=list)


def resolve_avoid(items: list[str], near: BBox | None = None,
                  start: LatLon | None = None) -> Avoidance:
    resolved = Avoidance()
    for item in items:
        place, separator, radius = item.rpartition(":")
        if separator and place and radius.replace(".", "").isdigit():
            radius_m = max(MIN_AVOID_RADIUS_M, min(float(radius), MAX_AVOID_RADIUS_M))
        else:
            place, radius_m = item, DEFAULT_AVOID_RADIUS_M
        lat, lon, name = geocode_flexible(place, near=near)
        if looks_like_road(place):
            ways = fetch_road(place.split(",")[0], lat, lon, ROAD_LOOKUP_RADIUS_M)
            if not ways:
                raise ConstraintUnavailable(f"Cannot verify avoidance of {place!r}: road geometry is unavailable. Try again later or specify an area instead.")
            resolved.roads.append(AvoidRoad(place, ways))
            resolved.routing_zones.extend(road_nogos(ways, (lat, lon), ROAD_LOOKUP_RADIUS_M,
                                    keep_clear=[start] if start else []))
            print(f"Avoiding road: {name}")
        else:
            resolved.areas.append((lat, lon, radius_m))
            print(f"Avoiding area: {name} (r={radius_m:.0f} m)")
    resolved.routing_zones.extend(resolved.areas)
    return resolved
