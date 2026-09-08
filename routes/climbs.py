"""Find real climbs near a start point, to route max-climb requests AT them
instead of hoping blind bearing search stumbles onto hills.

Two sources, merged:
- The rider's starred Strava segments (personal, human-validated; the
  standard API tier still serves these — segment *explore* got gated behind
  Strava's Extended Access tier on 2026-09-01).
- Our own elevation search: route spokes outward and extract sustained
  ascents from the elevation profile. Open data, works for anyone.
"""
import math
from dataclasses import dataclass

from routes.intervals import _resample
from routes.spec import METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ, METERS_PER_MILE


@dataclass
class Climb:
    name: str
    start: tuple  # (lat, lon)
    end: tuple
    gain_m: float
    length_m: float
    avg_grade_pct: float
    source: str   # "starred" or "elevation"


def _dist_m(a, b) -> float:
    dy = (a[0] - b[0]) * METERS_PER_DEG_LAT
    dx = (a[1] - b[1]) * METERS_PER_DEG_LON_EQ * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def extract_climbs(rs, min_gain_m: float = 25.0, min_grade_pct: float = 2.5,
                   max_dip_m: float = 12.0) -> list[dict]:
    """Sustained ascents in a resampled profile ((lat, lon, ele, cum) rows):
    runs that keep gaining, tolerating dips up to max_dip_m."""
    climbs = []
    i, n = 0, len(rs)
    while i < n - 1:
        if rs[i][2] is None or rs[i + 1][2] is None or rs[i + 1][2] <= rs[i][2]:
            i += 1
            continue
        j, peak_ele, peak_j = i, rs[i][2], i
        while j + 1 < n and rs[j + 1][2] is not None:
            j += 1
            if rs[j][2] > peak_ele:
                peak_ele, peak_j = rs[j][2], j
            if peak_ele - rs[j][2] > max_dip_m:
                break
        # A maximal ascending run can be a shallow miles-long approach with
        # a steep summit finish — its AVERAGE grade fails the threshold and
        # a 175 m climb gets discarded (found the hard way on Blue Mounds).
        # Take the earliest start whose suffix-to-peak meets both gain and
        # grade: the climb proper, maximal gain, undiluted by the approach.
        for k in range(i, peak_j):
            gain = rs[peak_j][2] - rs[k][2]
            length = rs[peak_j][3] - rs[k][3]
            if (length > 0 and gain >= min_gain_m
                    and gain / length * 100 >= min_grade_pct):
                climbs.append({
                    "start": (rs[k][0], rs[k][1]),
                    "end": (rs[peak_j][0], rs[peak_j][1]),
                    "gain_m": gain, "length_m": length,
                    "avg_grade_pct": gain / length * 100,
                })
                break
        i = max(peak_j, i + 1)
    return climbs


def find_climbs(lat: float, lon: float, radius_m: float, provider,
                n_spokes: int = 10, top: int = 3) -> list[Climb]:
    """Best climbs within radius: starred Strava segments first, then our
    own elevation search along routed spokes. Deduped by location."""
    from routes.providers import _destination

    found: list[Climb] = []

    try:
        from routes import strava
        if strava.available():
            for s in strava.starred_segments():
                start = tuple(s.get("start_latlng") or ())
                if len(start) != 2 or _dist_m((lat, lon), start) > radius_m:
                    continue
                gain = float(s.get("elevation_high", 0)) - float(s.get("elevation_low", 0))
                if gain < 15:
                    continue
                found.append(Climb(
                    name=s.get("name", "starred segment"),
                    start=start, end=tuple(s.get("end_latlng") or start),
                    gain_m=gain, length_m=float(s.get("distance", 0)),
                    avg_grade_pct=float(s.get("average_grade", 0)),
                    source="starred"))
    except Exception as e:  # Strava is an enhancement; never fatal
        print(f"  strava starred segments unavailable ({e})")

    # OSM peaks: route toward the biggest summits deliberately — the spoke
    # search below only sees through-roads, and marquee climbs are often
    # dead-end spurs (a park road up a mound is on no route to anywhere).
    from routes.peaks import climb_to_peak, fetch_peaks
    for peak in fetch_peaks(lat, lon, radius_m):
        c = climb_to_peak(lat, lon, peak, provider)
        if c is not None:
            found.append(Climb(
                name=c["name"], start=c["start"], end=c["end"],
                gain_m=c["gain_m"], length_m=c["length_m"],
                avg_grade_pct=c["avg_grade_pct"], source="peak"))

    for i in range(n_spokes):
        bearing = 360.0 * i / n_spokes
        dest = _destination(lat, lon, bearing, radius_m)
        leg = provider.route([(lat, lon), dest])
        if leg is None:
            continue
        for c in extract_climbs(_resample(leg["points"])):
            found.append(Climb(
                name=f"climb {c['gain_m']:.0f}m @ {c['avg_grade_pct']:.1f}%",
                start=c["start"], end=c["end"], gain_m=c["gain_m"],
                length_m=c["length_m"], avg_grade_pct=c["avg_grade_pct"],
                source="elevation"))

    # Dedupe by start location (~600 m cells): starred > peak > elevation
    # for the same hill, then bigger gain wins.
    priority = {"starred": 2, "peak": 1, "elevation": 0}
    best: dict = {}
    for c in found:
        key = (round(c.start[0] * 180), round(c.start[1] * 180))
        cur = best.get(key)
        if cur is None or ((priority[c.source], c.gain_m)
                           > (priority[cur.source], cur.gain_m)):
            best[key] = c
    # Rank by gain — the objective is climbing; starring only wins dedupes.
    ranked = sorted(best.values(), key=lambda c: -c.gain_m)
    for c in ranked[:top]:
        print(f"  climb target [{c.source}]: {c.name} — "
              f"{c.gain_m:.0f} m gain over {c.length_m / METERS_PER_MILE:.1f} mi "
              f"({c.avg_grade_pct:.1f}%)")
    return ranked[:top]
