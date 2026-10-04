"""Read GPX XML without inventing connections between separate tracks."""
import math
import xml.etree.ElementTree as ET

from routes.despur import _hav_m
from routes.spec import Track

# Consecutive segments whose ends are this close are the same point
# written twice (rounding); the duplicate is dropped.
SEGMENT_DUPLICATE_M = 1.0
# Device recordings split a ride into segments at every pause or GPS
# dropout, leaving real gaps (tens of meters to a couple of km). Those are
# one ride. A bigger jump means separate rides in one file, which the rider
# must split rather than have us draw a line across the map.
SEGMENT_JOIN_MAX_GAP_M = 2000.0


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


def parse_gpx_text(text: str, *, strict: bool = False) -> Track:
    """Read namespaced tracks/routes and legacy point fragments.

    Segments of one recording are joined across pause/dropout gaps up to
    SEGMENT_JOIN_MAX_GAP_M; anything farther apart is refused as separate
    rides. When a file carries both a track and a route (some exporters
    write the same ride twice), the track wins. DTDs/entities are never
    accepted. Strict
    upload mode also refuses invalid points instead of bridging over them.
    """
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("GPX documents containing DTDs or entities are not supported.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        try:
            root = ET.fromstring("<gpx>" + text + "</gpx>")
        except ET.ParseError:
            return []
    containers = [node for node in root.iter() if _tag(node) == "trkseg"
                  and any(_tag(n) == "trkpt" for n in node.iter())]
    if not containers:
        containers = [node for node in root.iter() if _tag(node) == "rte"]
    if not containers:
        containers = [root]
    points: Track = []
    for container in containers:
        segment: Track = []
        for node in container.iter():
            if _tag(node) not in ("trkpt", "rtept"):
                continue
            lat, lon = _number(node.get("lat")), _number(node.get("lon"))
            if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
                if strict:
                    raise ValueError("The GPX contains an invalid coordinate; repair it before uploading.")
                continue
            elevation = next((_number(child.text) for child in node
                              if _tag(child) == "ele"), None)
            segment.append((lat, lon, elevation))
        if points and segment:
            gap = _hav_m(points[-1], segment[0])
            if gap > SEGMENT_JOIN_MAX_GAP_M:
                raise ValueError(
                    f"This GPX contains separate rides ({gap / 1000:.1f} km "
                    "apart). Export one continuous route before uploading.")
            if gap <= SEGMENT_DUPLICATE_M:
                segment = segment[1:]
        points.extend(segment)
    return points
