"""Peak-aware climb scouting.

The spoke-based elevation search only sees climbs on THROUGH-roads — but
marquee climbs are often dead-end spurs (a park road up a mound ends at the
top; no route to anywhere crosses it). OSM tags summits as natural=peak
nodes with elevations, so: fetch the peaks in reach, route toward the
biggest ones deliberately, and harvest the summit-road climb from each
leg's elevation profile.
"""
import math
import re

from routes.interruptions import bbox_around, query_overpass

PEAK_QUERY = """[out:json][timeout:60];
node["natural"="peak"]["ele"]({bbox});
out;"""


def _parse_ele(raw) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(raw).replace(",", ""))
    return float(m.group()) if m else None


def fetch_peaks(lat: float, lon: float, radius_m: float, top: int = 3) -> list[dict]:
    """Highest peaks within radius: {name, lat, lon, ele_m}. Deduped so twin
    summits of one hill (East/West Blue Mound style) don't both count."""
    data = query_overpass(PEAK_QUERY.format(bbox=bbox_around(lat, lon, radius_m)))
    if data is None:
        return []
    peaks = []
    for el in data.get("elements", []):
        ele = _parse_ele(el.get("tags", {}).get("ele"))
        if ele is None:
            continue
        peaks.append({"name": el["tags"].get("name", f"peak {ele:.0f}m"),
                      "lat": el["lat"], "lon": el["lon"], "ele_m": ele})
    # dedupe within ~2 km, keep the higher summit
    merged: list[dict] = []
    for p in sorted(peaks, key=lambda p: -p["ele_m"]):
        near = any(
            math.hypot((p["lat"] - q["lat"]) * 110540.0,
                       (p["lon"] - q["lon"]) * 111320.0
                       * math.cos(math.radians(p["lat"]))) < 2000
            for q in merged)
        if not near:
            merged.append(p)
    return merged[:top]


def climb_to_peak(start_lat: float, start_lon: float, peak: dict, provider):
    """Route toward a peak and extract the climb that ends nearest it.
    Returns a dict like climbs.extract_climbs rows plus the peak name, or
    None when no substantial climb tops out near the peak."""
    from routes.climbs import extract_climbs
    from routes.intervals import _resample

    leg = provider.route([(start_lat, start_lon), (peak["lat"], peak["lon"])])
    if leg is None:
        return None
    candidates = extract_climbs(_resample(leg["points"]))
    best, best_d = None, None
    for c in candidates:
        d = math.hypot((c["end"][0] - peak["lat"]) * 110540.0,
                       (c["end"][1] - peak["lon"]) * 111320.0
                       * math.cos(math.radians(peak["lat"])))
        if d <= 3000 and (best is None or c["gain_m"] > best["gain_m"]):
            best, best_d = c, d
    if best is None:
        return None
    best["name"] = f"{peak['name']} ({best['gain_m']:.0f}m @ {best['avg_grade_pct']:.1f}%)"
    return best
