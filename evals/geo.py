"""Measurement primitives for the evaluation harness.

Deliberately INDEPENDENT of routes/*: the point of an eval is to check the
product's claims with a second implementation. Distances are haversine (the
app's editing code uses a faster equirectangular approximation), and GPX is
parsed with an independent strict XML reader.
"""
import math
import xml.etree.ElementTree as ET
from typing import Iterable, Sequence, Iterator, TypeVar

EARTH_RADIUS_M: float = 6371008.8      # WGS-84 mean radius
METERS_PER_MILE: float = 1609.344
METERS_PER_FOOT: float = 0.3048

LatLon = tuple[float, float]
EleP = tuple[float, float, float | None]
T = TypeVar("T")


def haversine_m(a: Sequence[float], b: Sequence[float]) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dphi = phi2 - phi1
    dlam = math.radians(b[1] - a[1])
    h = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def track_length_m(points: Sequence[Sequence[float]]) -> float:
    return sum(haversine_m(points[i - 1], points[i])
               for i in range(1, len(points)))


def point_segment_m(p: Sequence[float], a: Sequence[float],
                    b: Sequence[float]) -> float:
    """Distance from p to segment ab, in a local flat projection."""
    lat0 = math.radians(p[0])
    kx = 111320.0 * math.cos(lat0)
    ky = 110540.0
    px, py = p[1] * kx, p[0] * ky
    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    t = 0.0 if den == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def min_dist_to_track_m(target: Sequence[float],
                        points: Sequence[Sequence[float]]) -> float:
    """Closest approach of a polyline to a point, measured against SEGMENTS
    (not just vertices) so a long straight leg cannot fake a miss."""
    if not points:
        return float("inf")
    if len(points) == 1:
        return haversine_m(target, points[0])
    return min(point_segment_m(target, points[i - 1], points[i])
               for i in range(1, len(points)))


def max_point_gap_m(points: Sequence[Sequence[float]]) -> float:
    """Largest jump between consecutive track points. A routed polyline is
    continuous; a big gap means spliced or disconnected geometry."""
    if len(points) < 2:
        return 0.0
    return max(haversine_m(points[i - 1], points[i])
               for i in range(1, len(points)))


def bearing_deg(a: Sequence[float], b: Sequence[float]) -> float:
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dlam = math.radians(b[1] - a[1])
    y = math.sin(dlam) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(dlam))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _turn(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def suspicious_gaps(points: Sequence[Sequence[float]],
                    min_gap_m: float = 800.0,
                    turn_deg: float = 55.0) -> list[dict[str, float]]:
    """Long segments that are discontinuities rather than straight road.

    Two earlier versions of this check were wrong, which is why it is spelled
    out here:

    1. A plain gap threshold (600 m) flagged three perfectly good routes.
       Wisconsin's section-line grid gives dead-straight half-mile and
       mile-long roads, and BRouter emits those as a single segment with no
       intermediate vertex.
    2. Requiring a sharp bearing change on EITHER end still flagged grid
       corners: you turn 90 degrees onto a section road and then run straight
       for half a mile, so the long segment is entered with a turn and left
       without one. That is a corner, not a break.

    A spliced or disconnected track instead arrives at the long segment from
    one direction and leaves it in another -- the connector is off-axis at
    BOTH ends. Requiring that keeps the check able to catch a real break
    without punishing a road grid.
    """
    out: list[dict[str, float]] = []
    for i in range(1, len(points)):
        gap = haversine_m(points[i - 1], points[i])
        if gap < min_gap_m or i < 2 or i + 1 >= len(points):
            continue
        here = bearing_deg(points[i - 1], points[i])
        enter = _turn(bearing_deg(points[i - 2], points[i - 1]), here)
        leave = _turn(here, bearing_deg(points[i], points[i + 1]))
        if enter >= turn_deg and leave >= turn_deg:
            out.append({"index": i, "gap_m": round(gap, 1),
                        "turn_in_deg": round(enter, 1),
                        "turn_out_deg": round(leave, 1)})
    return out


def point_at_distance(points: Sequence[Sequence[float]],
                      cum: Sequence[float], s: float) -> tuple[float, float]:
    """Interpolated position `s` meters along the track."""
    import bisect
    if s <= 0:
        return points[0][0], points[0][1]
    if s >= cum[-1]:
        return points[-1][0], points[-1][1]
    k = bisect.bisect_right(cum, s)
    a, b = points[k - 1], points[k]
    seg = cum[k] - cum[k - 1]
    f = 0.0 if seg <= 0 else (s - cum[k - 1]) / seg
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f


def mirror_fraction(points: Sequence[Sequence[float]],
                    tol_m: float = 40.0, samples: int = 200) -> float:
    """How much of the track is its own reverse -- the out-and-back signature.

    Compares the position at arc-length s against the position at
    (total - s). Arc-length interpolation rather than index arithmetic: an
    earlier version resampled at a fixed step and compared by index, which
    drifted out of phase and scored an exact palindrome at 0.6%.

    A pure out-and-back scores near 1.0; a true loop scores near 0. This is
    what lets the scorer tell 'rode the same road twice by design' from
    'rode the same road twice by accident'.
    """
    if len(points) < 4:
        return 0.0
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + haversine_m(points[i - 1], points[i]))
    total = cum[-1]
    if total <= 0:
        return 0.0
    hits = 0
    for k in range(samples):
        s = total * (k + 0.5) / (2 * samples)      # first half only
        a = point_at_distance(points, cum, s)
        b = point_at_distance(points, cum, total - s)
        if haversine_m(a, b) <= tol_m:
            hits += 1
    return hits / samples


def bbox(points: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return min(lats), min(lons), max(lats), max(lons)


class GpxProblem(Exception):
    pass


def parse_gpx_strict(path: str) -> list[EleP]:
    """Parse a GPX with a real XML parser and validate it the way a device
    would. Raises GpxProblem with a specific reason.

    Accepts <trkpt> and <rtept>; requires well-formed XML, a GPX root,
    at least two points, finite in-range coordinates, and no NaN elevation.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise GpxProblem(f"not well-formed XML: {e}") from e
    root = tree.getroot()
    tag = root.tag.split("}")[-1]
    if tag != "gpx":
        raise GpxProblem(f"root element is <{tag}>, not <gpx>")
    pts: list[EleP] = []
    for el in root.iter():
        name = el.tag.split("}")[-1]
        if name not in ("trkpt", "rtept"):
            continue
        try:
            lat = float(el.attrib["lat"])
            lon = float(el.attrib["lon"])
        except (KeyError, ValueError) as e:
            raise GpxProblem(f"point with missing/bad lat-lon: {e}") from e
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise GpxProblem("non-finite coordinate")
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise GpxProblem(f"coordinate out of range: {lat}, {lon}")
        ele: float | None = None
        for child in el:
            if child.tag.split("}")[-1] == "ele" and child.text:
                try:
                    ele = float(child.text)
                except ValueError as e:
                    raise GpxProblem(f"bad <ele>: {child.text!r}") from e
                if not math.isfinite(ele):
                    raise GpxProblem("non-finite <ele>")
        pts.append((lat, lon, ele))
    if len(pts) < 2:
        raise GpxProblem(f"only {len(pts)} track point(s)")
    return pts


def duplicate_point_runs(points: Sequence[Sequence[float]],
                         eps_m: float = 0.5) -> int:
    """Count consecutive points that sit on top of each other. A few are
    normal at via-point joins; a lot means a splice went wrong."""
    return sum(1 for i in range(1, len(points))
               if haversine_m(points[i - 1], points[i]) < eps_m)


def ascent_m_simple(points: Sequence[EleP], threshold_m: float = 3.0) -> float:
    """A blunt, independent climb figure: sum positive elevation changes that
    exceed `threshold_m`, no smoothing.

    This is NOT ground truth and NOT the app's model. It exists so the eval
    can say whether the app's calibrated figure is in a plausible band, and
    it reads on the same SRTM-derived elevations the router returns -- so it
    shares that data's limitations. Barometric field data is the only real
    check, and there is none in this campaign.
    """
    total = 0.0
    ref: float | None = None
    for p in points:
        e = p[2] if len(p) > 2 else None
        if e is None:
            continue
        if ref is None:
            ref = e
            continue
        if e - ref >= threshold_m:
            total += e - ref
            ref = e
        elif e < ref:
            ref = e
    return total


def resample_m(points: Sequence[Sequence[float]],
               step_m: float = 25.0) -> list[tuple[float, float]]:
    """Even spacing along the track, for coverage-style measurements."""
    if len(points) < 2:
        return [(p[0], p[1]) for p in points]
    out: list[tuple[float, float]] = [(points[0][0], points[0][1])]
    carry = 0.0
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        seg = haversine_m(a, b)
        if seg <= 0:
            continue
        pos = step_m - carry
        while pos <= seg:
            f = pos / seg
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            pos += step_m
        carry = (carry + seg) % step_m
    return out


def fmt_mi(meters: float) -> float:
    return round(meters / METERS_PER_MILE, 2)


def fmt_ft(meters: float) -> float:
    return round(meters / METERS_PER_FOOT, 0)


def chunks(seq: Iterable[T], n: int) -> Iterator[list[T]]:
    buf: list[T] = []
    for x in seq:
        buf.append(x)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf
