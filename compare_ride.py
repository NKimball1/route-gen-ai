"""Compare a ridden .fit file against a generated route GPX.

  python compare_ride.py "path\\to\\ride.fit" output\\routes\\route_x.gpx

Finds the portion of the ride that followed the route (the rider may peel
off), then compares over the matched portion:
- the device's barometric climbing (the best ground truth there is)
- our model's predicted climbing over the same stretch of route geometry

Every ridden route is a free calibration point for routes/elevation.py.
"""
import math
import sys

from fitparse import FitFile

from routes.despur import RESAMPLE_STEP_M, _resample
from routes.elevation import track_ascent
from routes.preview import _parse_gpx
from routes.spec import METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ, METERS_PER_FOOT, METERS_PER_MILE

SEMI = 180.0 / 2 ** 31        # FIT semicircles -> degrees
ON_ROUTE_M = 60.0             # within this of the route = following it
OFF_RUN = 100                 # consecutive off-route samples = peeled off
MIN_CHUNK_M = 800.0           # ignore on-route touches shorter than ~0.5 mi
# How many consecutive resampled route indices span ~200 m. Used two ways
# below: a gap in covered indices larger than this splits the coverage into
# separate segments, and segments shorter than this carry no climbing
# signal worth comparing.
SEG_200M_IDX = int(200 / RESAMPLE_STEP_M)


def load_fit(path):
    """(lat, lon, ele) points + total device ascent from the FIT session."""
    fit = FitFile(path)
    pts = []
    for rec in fit.get_messages("record"):
        v = rec.get_values()
        lat, lon = v.get("position_lat"), v.get("position_long")
        ele = v.get("enhanced_altitude", v.get("altitude"))
        if lat is not None and lon is not None:
            pts.append((lat * SEMI, lon * SEMI, ele))
    totals = {}
    for msg in fit.get_messages("session"):
        totals = msg.get_values()
    return pts, totals


def device_ascent(points, min_step: float = 0.0):
    """Sum of positive barometric deltas — what head units report."""
    total, last = 0.0, None
    for _, _, e in points:
        if e is None:
            continue
        if last is not None and e - last > min_step:
            total += e - last
        last = e
    return total


class RouteIndex:
    def __init__(self, route_pts, cell_m: float = 100.0):
        self.kx = METERS_PER_DEG_LON_EQ * math.cos(math.radians(route_pts[0][0]))
        self.cell = cell_m
        self.grid = {}
        for i, p in enumerate(route_pts):
            self.grid.setdefault(self._key(p), []).append(i)
        self.pts = route_pts

    def _key(self, p):
        return (int(p[0] * METERS_PER_DEG_LAT / self.cell), int(p[1] * self.kx / self.cell))

    def nearest(self, p):
        kx, ky = self._key(p)
        best_d, best_i = None, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.grid.get((kx + dx, ky + dy), ()):
                    q = self.pts[i]
                    d = math.hypot((p[0] - q[0]) * METERS_PER_DEG_LAT,
                                   (p[1] - q[1]) * self.kx)
                    if best_d is None or d < best_d:
                        best_d, best_i = d, i
        return best_d, best_i


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    fit_path, gpx_path = sys.argv[1], sys.argv[2]

    ride, totals = load_fit(fit_path)
    route = _resample(_parse_gpx(gpx_path))
    print(f"ride: {len(ride)} points; route: {len(route)} resampled points")

    index = RouteIndex(route)

    def polyline_len(pts):
        return sum(
            math.hypot((pts[k + 1][0] - pts[k][0]) * METERS_PER_DEG_LAT,
                       (pts[k + 1][1] - pts[k][1]) * index.kx)
            for k in range(len(pts) - 1))

    # A rider can deviate briefly (missed turn, detour) and rejoin — a
    # single cut point undercounts badly. Instead: mark every ride sample
    # on/off route, then compare over contiguous on-route chunks and the
    # route indices they covered.
    on_flags, covered = [], set()
    for p in ride:
        d, ri = index.nearest(p)
        on = d is not None and d <= ON_ROUTE_M
        on_flags.append(on)
        if on:
            covered.add(ri)

    chunks, start = [], None
    for i, on in enumerate(on_flags + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            chunk = ride[start:i]
            if polyline_len(chunk) >= MIN_CHUNK_M:
                chunks.append(chunk)
            start = None
    if not chunks:
        print("The ride never followed the route.")
        return 1

    followed_dist = sum(polyline_len(c) for c in chunks)
    dev_ft = sum(device_ascent(c) for c in chunks) / METERS_PER_FOOT

    # model ascent over the covered contiguous route segments
    idxs = sorted(covered)
    segs, s = [], idxs[0]
    for a, b in zip(idxs, idxs[1:] + [None]):
        if b is None or b - a > SEG_200M_IDX:
            segs.append((s, a))
            s = b
    model_ft = sum(track_ascent(route[a:b + 1]) for a, b in segs
                   if b - a >= SEG_200M_IDX) / METERS_PER_FOOT
    covered_dist = sum(polyline_len(route[a:b + 1]) for a, b in segs)

    print(f"\nOn the route for {followed_dist / METERS_PER_MILE:.1f} mi across "
          f"{len(chunks)} stretch(es); route covered "
          f"{covered_dist / METERS_PER_MILE:.1f} of "
          f"{polyline_len(route) / METERS_PER_MILE:.1f} mi")
    print(f"device climbing on-route:            {dev_ft:7.0f} ft (barometric)")
    print(f"our model over the matched geometry: {model_ft:7.0f} ft "
          f"({(model_ft - dev_ft) / dev_ft:+.1%})")

    if totals:
        td = totals.get("total_distance")
        ta = totals.get("total_ascent")
        if td:
            print(f"\nwhole ride: {td / METERS_PER_MILE:.1f} mi"
                  + (f", {ta / METERS_PER_FOOT:.0f} ft device total ascent"
                     if ta else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
