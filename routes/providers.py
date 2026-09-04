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

from routes.despur import despur
from routes.spec import RouteCandidate, RouteSpec

EARTH_RADIUS_M = 6371000.0


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
    BASE_URL = "https://brouter.de/brouter"
    # Real roads wander, so a routed loop runs longer than the geometric circle
    # its waypoints sit on; shrink the circle by this factor to compensate.
    WINDING_FACTOR = 1.35
    VIA_POINTS = 3
    # Real roads run longer than the straight line to an outback turnaround.
    DETOUR_FACTOR = 1.3
    MAX_RESCALES = 2

    def __init__(self, profile: str = "fastbike-lowtraffic"):
        # "fastbike-lowtraffic" is BRouter's road-bike profile that strongly
        # avoids busy/high-speed roads; "fastbike-verylowtraffic" avoids them
        # harder, "trekking" is the touring default.
        self.profile = profile

    def route(self, waypoints: list[tuple[float, float]],
              avoid: list[tuple[float, float, float]] | None = None) -> dict | None:
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
            resp = requests.get(self.BASE_URL, params=params, timeout=120)
            resp.raise_for_status()
            feature = resp.json()["features"][0]
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            print(f"  brouter: request failed ({e})")
            return None
        props = feature["properties"]
        points = [(c[1], c[0], c[2] if len(c) > 2 else None)
                  for c in feature["geometry"]["coordinates"]]
        # Cut out-and-back spur artifacts BEFORE distance/climb accounting, so
        # rescaling and ranking see the route as it would be ridden. The naive
        # spur-ascent estimate can overshoot the provider's filtered figure,
        # hence the clamp.
        points, spur_dist, spur_ascent = despur(points)
        if spur_dist > 400:
            print(f"  brouter: trimmed {spur_dist / 1609.344:.1f} mi of "
                  f"out-and-back spurs")
        return {
            "points": points,
            "distance_m": float(props["track-length"]) - spur_dist,
            "ascent_m": max(0.0, float(props["filtered ascend"]) - spur_ascent),
            "net_gain_m": float(props.get("plain-ascend", 0.0)),
        }

    def candidates(self, spec: RouteSpec, lat: float, lon: float,
                   n: int = 6) -> list[RouteCandidate]:
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
        return RouteCandidate(
            provider=self.name,
            seed=f"bearing={bearing:.0f} {'cw' if clockwise else 'ccw'} scale={scale:.2f}",
            distance_m=leg["distance_m"],
            ascent_m=leg["ascent_m"],
            points=leg["points"],
        )

    def _outback(self, spec: RouteSpec, lat: float, lon: float, bearing: float,
                 scale: float = 1.0) -> RouteCandidate | None:
        crow = scale * (spec.distance_m / 2) / self.DETOUR_FACTOR
        dest = _destination(lat, lon, bearing, crow)
        leg = self.route([(lat, lon), dest], spec.avoid)
        if leg is None:
            return None
        # Return-leg climbing is the outbound leg's descent (ascent minus net).
        total_ascent = max(0.0, leg["ascent_m"] + (leg["ascent_m"] - leg["net_gain_m"]))
        return RouteCandidate(
            provider=self.name,
            seed=f"outback bearing={bearing:.0f} scale={scale:.2f}",
            distance_m=leg["distance_m"] * 2,
            ascent_m=total_ascent,
            points=leg["points"] + leg["points"][-2::-1],
            shape="outback",
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
                ascent_m=max(0.0, float(feature["properties"].get("ascent", 0.0))
                             - spur_ascent),
                points=points,
            ))
        return out
