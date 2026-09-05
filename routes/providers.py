"""Routing backends.

Two interchangeable providers so the composer is not tied to one API:

- BRouterProvider: public brouter.de server, no API key. BRouter only routes
  point-to-point, so we synthesize loops ourselves: place via-points on a
  circle through the start and route around them. Loop generation stays in
  our code, which means ANY point-to-point bike router (Valhalla, GraphHopper,
  a self-hosted ORS) can back it later.
- ORSProvider: openrouteservice's native round_trip generator. Needs a free
  API key in the ORS_API_KEY env var.

Both return RouteCandidate lists for the same RouteSpec.
"""
import math
import os

import requests

from routes.despur import corridor_despur, despur
from routes.elevation import track_ascent
from routes.spec import RouteCandidate, RouteSpec

EARTH_RADIUS_M = 6371000.0


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial great-circle bearing from a to b, degrees."""
    phi1, phi2 = math.radians(a[0]), math.radians(b[0])
    dlam = math.radians(b[1] - a[1])
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _destination(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """Point reached from (lat, lon) after dist_m along bearing_deg (great circle)."""
    delta = dist_m / EARTH_RADIUS_M
    theta = math.radians(bearing_deg)
    phi1, lam1 = math.radians(lat), math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(delta)
                     + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2 = lam1 + math.atan2(math.sin(theta) * math.sin(delta) * math.cos(phi1),
                             math.cos(delta) - math.sin(phi1) * math.sin(phi2))
    return math.degrees(phi2), math.degrees(lam2)


class BRouterProvider:
    name = "brouter"
    # Real roads wander, so a routed loop runs longer than the geometric circle
    # its waypoints sit on; shrink the circle by this factor to compensate.
    WINDING_FACTOR = 1.35
    VIA_POINTS = 3
    # Real roads run longer than the straight line to an outback turnaround.
    DETOUR_FACTOR = 1.3
    MAX_RESCALES = 2

    def __init__(self, profile: str | None = None):
        # Self-hosted instance when BROUTER_URL is set (e.g.
        # http://localhost:17777/brouter); public server otherwise.
        # Self-hosting removes rate limits and enables custom profiles.
        self.base_url = os.environ.get("BROUTER_URL",
                                       "https://brouter.de/brouter")
        # Default profile: our custom "fastbike-quiet" (county-highway-class
        # roads heavily penalized) on the self-hosted server; the stock
        # "fastbike-lowtraffic" on the public server, which lacks it.
        if profile is None:
            profile = ("fastbike-quiet" if "localhost" in self.base_url
                       else "fastbike-lowtraffic")
        self.profile = profile

    def route(self, waypoints: list[tuple[float, float]],
              avoid: list[tuple[float, float, float]] | None = None,
              protect=None) -> dict | None:
        """Point-to-point request. Returns {points, distance_m, ascent_m,
        net_gain_m} with spurs already trimmed, or None on failure."""
        params = {
            "lonlats": "|".join(f"{p[1]:.6f},{p[0]:.6f}" for p in waypoints),
            "profile": self.profile,
            "alternativeidx": 0,
            "format": "geojson",
        }
        if avoid:
            params["nogos"] = "|".join(f"{a[1]:.6f},{a[0]:.6f},{a[2]:.0f}"
                                       for a in avoid)
        try:
            resp = requests.get(self.base_url, params=params, timeout=120)
            resp.raise_for_status()
            feature = resp.json()["features"][0]
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            print(f"  brouter: request failed ({e})")
            return None
        props = feature["properties"]
        points = [(c[1], c[0], c[2] if len(c) > 2 else None)
                  for c in feature["geometry"]["coordinates"]]
        # Road-class accounting from BRouter's per-segment messages: distance
        # ridden on major highways (motorway/trunk/primary, links included).
        # Counted pre-trim, so a trimmed spur on a highway still counts —
        # conservative in the right direction.
        major_m = 0.0
        for row in props.get("messages", [])[1:]:
            if len(row) > 9 and any(f"highway={h}" in row[9]
                                    for h in ("motorway", "trunk", "primary")):
                major_m += float(row[3])
        # Cut out-and-back spur artifacts BEFORE distance/climb accounting, so
        # rescaling and ranking see the route as it would be ridden. The naive
        # spur-ascent estimate can overshoot the provider's filtered figure,
        # hence the clamp.
        points, spur_dist, spur_ascent = despur(points, protect=protect)
        # Second pass: corridor tendrils (out and back on not-quite-identical
        # geometry — parallel path, offset lanes) that exact matching misses.
        points, c_dist, c_ascent = corridor_despur(points, protect=protect)
        spur_dist += c_dist
        spur_ascent += c_ascent
        if spur_dist > 400:
            print(f"  brouter: trimmed {spur_dist / 1609.344:.1f} mi of "
                  f"out-and-back spurs")
        return {
            "points": points,
            "distance_m": float(props["track-length"]) - spur_dist,
            # Ascent from the final trimmed geometry, device-calibrated —
            # not BRouter's smoothed figure (reads ~40% low vs. RideWithGPS)
            # and immune to spur-subtraction artifacts.
            "ascent_m": track_ascent(points),
            "major_m": major_m,
        }

    def candidates(self, spec: RouteSpec, lat: float, lon: float,
                   n: int = 6) -> list[RouteCandidate]:
        if spec.via:
            return self._via_candidates(spec, lat, lon, n)
        out = []
        for i in range(n):
            bearing = 360.0 * i / n
            if spec.shape == "outback":
                build = lambda s=1.0: self._outback(spec, lat, lon, bearing, s)
            else:
                clockwise = i % 2 == 0
                build = lambda s=1.0: self._loop(spec, lat, lon, bearing, clockwise, s)
            cand, scale = build(), 1.0
            # Adaptive rescales while the routed length misses the target by
            # more than half the acceptance tolerance.
            for _ in range(self.MAX_RESCALES):
                if cand is None:
                    break
                error = abs(cand.distance_m - spec.distance_m) / spec.distance_m
                if error <= spec.distance_tolerance / 2:
                    break
                scale *= spec.distance_m / cand.distance_m
                retry = build(scale)
                if retry is None:
                    break
                cand = retry
            if cand is not None:
                out.append(cand)
        return out

    # A route "goes through" a via if it passes within this distance of it —
    # towns are areas, and forcing the exact geocoded centroid creates
    # touch-and-retreat tendrils.
    VIA_NEAR_M = 2000.0

    @staticmethod
    def _passes_near(points, via, radius_m: float) -> bool:
        for p in points[::4]:
            dy = (p[0] - via[0]) * 110540.0
            dx = (p[1] - via[1]) * 111320.0 * math.cos(math.radians(via[0]))
            if dx * dx + dy * dy <= radius_m * radius_m:
                return True
        return False

    def _via_candidates(self, spec: RouteSpec, lat: float, lon: float,
                        n: int) -> list[RouteCandidate]:
        """Loops through user-required places, two ways:

        1. Plain bearing loops swept toward the vias — the most natural
           shapes; kept only when they pass within VIA_NEAR_M of every via.
        2. Anchored cycles (start -> vias -> start, both via orders) with one
           leg bowed outward to reach the target distance — guaranteed to
           hit the vias, used when the natural loops don't.
        """
        from routes.overlap import repeated_fraction

        vias = [tuple(v) for v in spec.via]
        out = []

        # 1: bearing loops aimed at the via centroid
        centroid = (sum(v[0] for v in vias) / len(vias),
                    sum(v[1] for v in vias) / len(vias))
        toward = _bearing((lat, lon), centroid)
        for offset in (-50, -15, 15, 50):
            cand = self._loop(spec, lat, lon, (toward + offset) % 360.0,
                              clockwise=offset > 0)
            if cand is None:
                continue
            error = abs(cand.distance_m - spec.distance_m) / spec.distance_m
            if error > spec.distance_tolerance / 2:
                retry = self._loop(spec, lat, lon, (toward + offset) % 360.0,
                                   clockwise=offset > 0,
                                   scale=spec.distance_m / cand.distance_m)
                if retry is not None:
                    cand = retry
            if all(self._passes_near(cand.points, v, self.VIA_NEAR_M)
                   for v in vias):
                cand.seed = f"sweep {cand.seed}"
                cand.overlap_frac = repeated_fraction(cand.points)
                cand.natural = True
                out.append(cand)
        if out:
            print(f"  {len(out)} natural loop(s) pass through all via places")

        # 2: anchored cycles with an adaptive extension bow
        orders = [vias] + ([list(reversed(vias))] if len(vias) > 1 else [])
        budget = max(2, n - len(out))
        for order in orders:
            anchors = [(lat, lon)] + order
            base = self.route(anchors + [(lat, lon)], spec.avoid, protect=vias)
            if base is None:
                continue
            base_dist = base["distance_m"]
            needed = spec.distance_m - base_dist
            if needed <= spec.distance_m * spec.distance_tolerance:
                base_cand = RouteCandidate(
                    provider=self.name, seed="via direct",
                    distance_m=base_dist, ascent_m=base["ascent_m"],
                    points=base["points"],
                    overlap_frac=repeated_fraction(base["points"]),
                    major_m=base["major_m"])
                out.append(base_cand)
                continue
            combos = [(li, side) for li in range(len(anchors))
                      for side in (1, -1)]
            for li, side in combos[:budget // len(orders) + 1]:
                a = anchors[li]
                b = anchors[(li + 1) % len(anchors)]
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                leg_bearing = _bearing(a, b)
                r = needed / 2 / 1.2
                cand = None
                for _ in range(3):
                    ext = _destination(mid[0], mid[1],
                                       leg_bearing + 90 * side, r)
                    wps = anchors[:li + 1] + [ext] + anchors[li + 1:] + [(lat, lon)]
                    leg = self.route(wps, spec.avoid, protect=vias)
                    if leg is None:
                        break
                    cand = RouteCandidate(
                        provider=self.name,
                        seed=f"via leg={li} side={'+' if side > 0 else '-'} "
                             f"r={r / 1609.344:.1f}mi",
                        distance_m=leg["distance_m"], ascent_m=leg["ascent_m"],
                        points=leg["points"],
                        overlap_frac=repeated_fraction(leg["points"]),
                        major_m=leg["major_m"])
                    error = abs(cand.distance_m - spec.distance_m) / spec.distance_m
                    added = cand.distance_m - base_dist
                    if error <= spec.distance_tolerance / 2 or added <= 0:
                        break
                    r *= max(0.25, min(4.0, needed / added))
                if cand is not None:
                    out.append(cand)
        return out

    def _loop(self, spec: RouteSpec, lat: float, lon: float, bearing: float,
              clockwise: bool, scale: float = 1.0) -> RouteCandidate | None:
        radius = scale * spec.distance_m / self.WINDING_FACTOR / (2 * math.pi)
        center = _destination(lat, lon, bearing, radius)
        start_angle = (bearing + 180.0) % 360.0  # bearing from center back to start
        step = 360.0 / (self.VIA_POINTS + 1) * (1 if clockwise else -1)
        vias = [_destination(center[0], center[1], start_angle + step * (k + 1), radius)
                for k in range(self.VIA_POINTS)]
        leg = self.route([(lat, lon)] + vias + [(lat, lon)], spec.avoid)
        if leg is None:
            return None
        from routes.overlap import repeated_fraction
        return RouteCandidate(
            provider=self.name,
            seed=f"bearing={bearing:.0f} {'cw' if clockwise else 'ccw'} scale={scale:.2f}",
            distance_m=leg["distance_m"],
            ascent_m=leg["ascent_m"],
            points=leg["points"],
            overlap_frac=repeated_fraction(leg["points"]),
            major_m=leg["major_m"],
        )

    def _outback(self, spec: RouteSpec, lat: float, lon: float, bearing: float,
                 scale: float = 1.0) -> RouteCandidate | None:
        crow = scale * (spec.distance_m / 2) / self.DETOUR_FACTOR
        dest = _destination(lat, lon, bearing, crow)
        leg = self.route([(lat, lon), dest], spec.avoid)
        if leg is None:
            return None
        full_track = leg["points"] + leg["points"][-2::-1]
        return RouteCandidate(
            provider=self.name,
            seed=f"outback bearing={bearing:.0f} scale={scale:.2f}",
            distance_m=leg["distance_m"] * 2,
            ascent_m=track_ascent(full_track),
            points=full_track,
            shape="outback",
            major_m=leg["major_m"] * 2,
        )


class ORSProvider:
    name = "ors"
    BASE_URL = "https://api.openrouteservice.org/v2/directions/cycling-regular/geojson"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ORS_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def candidates(self, spec: RouteSpec, lat: float, lon: float,
                   n: int = 6) -> list[RouteCandidate]:
        if spec.shape == "outback":
            print("  (ors: round_trip only generates loops; skipping outback)")
            return []
        if spec.avoid:
            print("  (ors: avoid zones not wired up for round_trip; skipping)")
            return []
        if spec.via:
            print("  (ors: round_trip cannot honor via places; skipping)")
            return []
        out = []
        for seed in range(n):
            try:
                resp = requests.post(
                    self.BASE_URL,
                    json={
                        "coordinates": [[lon, lat]],
                        "options": {"round_trip": {
                            "length": spec.distance_m, "points": 4, "seed": seed}},
                        "elevation": True,
                    },
                    headers={"Authorization": self.api_key},
                    timeout=120,
                )
                resp.raise_for_status()
                feature = resp.json()["features"][0]
            except (requests.RequestException, KeyError, IndexError, ValueError) as e:
                print(f"  ors seed {seed}: failed ({e})")
                continue
            summary = feature["properties"]["summary"]
            points = [(c[1], c[0], c[2] if len(c) > 2 else None)
                      for c in feature["geometry"]["coordinates"]]
            points, spur_dist, spur_ascent = despur(points)
            if spur_dist > 400:
                print(f"  ors seed {seed}: trimmed "
                      f"{spur_dist / 1609.344:.1f} mi of out-and-back spurs")
            out.append(RouteCandidate(
                provider=self.name,
                seed=f"seed={seed}",
                distance_m=float(summary["distance"]) - spur_dist,
                ascent_m=track_ascent(points),
                points=points,
            ))
        return out
