"""Which road is this? Reverse geocoding for result descriptions.

A spot is only useful once the rider knows where it is: "Hope Road
(Femrite Drive -> Nora Road)" beats a coordinate and a GPX file name.
Nominatim's reverse endpoint, one request per second per its usage
policy, failures degrade to None rather than breaking a search.
"""
from typing import Callable

import requests

from routes.geocode import USER_AGENT
from routes.request_gate import wait_for_nominatim
from routes.spec import Coord

REVERSE_URL: str = "https://nominatim.openstreetmap.org/reverse"

Lookup = Callable[[float, float], "tuple[str, str] | None"]


def road_at(lat: float, lon: float) -> tuple[str, str] | None:
    """(road name, town) at a point, or None if unknown/unreachable."""
    params: dict[str, str | float] = {"lat": lat, "lon": lon,
                                      "format": "jsonv2", "zoom": 17}
    try:
        wait_for_nominatim()
        resp = requests.get(REVERSE_URL, params=params,
                            headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        a = resp.json().get("address", {})
    except (requests.RequestException, ValueError):
        return None
    road = a.get("road") or a.get("cycleway") or a.get("path") or a.get("footway")
    if not road:
        return None
    town = (a.get("village") or a.get("town") or a.get("city")
            or a.get("hamlet") or a.get("municipality") or "")
    return road, town


def describe_stretch(points: list[Coord], lookup: Lookup = road_at) -> str:
    """'Hope Road, Madison (Femrite Drive -> Nora Road)' for a stretch that
    changes roads; just 'Badger State Trail, Fitchburg' when it doesn't."""
    if not points:
        return "?"
    mid = lookup(points[len(points) // 2][0], points[len(points) // 2][1])
    if mid is None:
        return "?"
    name = f"{mid[0]}, {mid[1]}" if mid[1] else mid[0]
    ends = [lookup(points[0][0], points[0][1]), lookup(points[-1][0], points[-1][1])]
    end_names = [e[0] if e else "?" for e in ends]
    if all(e == mid[0] for e in end_names):
        return name
    return f"{name} ({end_names[0]} -> {end_names[1]})"
