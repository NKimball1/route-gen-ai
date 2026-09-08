"""Strava segment data: token management + the segment-explore endpoint.

Strava is an OPTIONAL enhancement layer — everything routes without it.
What it adds: real climb locations (explore returns climb_category per
segment) and a where-locals-actually-ride popularity signal.

Auth: needs STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET in the environment and
a token file. The token file is seeded from the cycling-coach project's
authorization if not present here; access tokens auto-refresh. Multi-user
note: per Strava's API agreement one athlete's token must not serve other
people — a public deployment gives each user their own OAuth connection.
"""
import json
import math
import os
import time

import requests
from routes.spec import METERS_PER_DEG_LAT, METERS_PER_DEG_LON_EQ

TOKEN_FILE = ".strava_tokens.json"
SEED_TOKEN_FILE = os.path.join(
    os.path.expanduser("~"), "OneDrive", "Desktop", "cycling agentic flow",
    ".strava_tokens.json")
TOKEN_URL = "https://www.strava.com/oauth/token"
EXPLORE_URL = "https://www.strava.com/api/v3/segments/explore"
STARRED_URL = "https://www.strava.com/api/v3/segments/starred"


def available() -> bool:
    return bool(os.environ.get("STRAVA_CLIENT_ID")
                and os.environ.get("STRAVA_CLIENT_SECRET")
                and (os.path.exists(TOKEN_FILE)
                     or os.path.exists(SEED_TOKEN_FILE)))


def get_access_token() -> str:
    path = TOKEN_FILE if os.path.exists(TOKEN_FILE) else SEED_TOKEN_FILE
    tokens = json.load(open(path))
    if tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]
    resp = requests.post(TOKEN_URL, data={
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }, timeout=30)
    resp.raise_for_status()
    fresh = resp.json()
    if fresh.get("refresh_token") != tokens["refresh_token"]:
        print("  strava: NOTE — refresh token rotated; if the coach agent's "
              "deployment refreshes separately, update its token file")
    tokens.update(fresh)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f)
    return tokens["access_token"]


def starred_segments() -> list[dict]:
    """The athlete's starred segments — still available on the standard API
    tier (unlike explore, gated behind Extended Access since 2026-09-01).
    Starring a climb in the Strava app makes it a routing target here."""
    resp = requests.get(
        STARRED_URL, params={"per_page": 100},
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def explore_segments(lat: float, lon: float, radius_m: float) -> list[dict]:
    """Popular riding segments in a box around (lat, lon). Each dict has
    name, climb_category, avg_grade, elev_difference (m), distance (m),
    start_latlng, end_latlng, and decoded polyline points."""
    dlat = radius_m / METERS_PER_DEG_LAT
    dlon = radius_m / (METERS_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
    bounds = f"{lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon}"
    resp = requests.get(
        EXPLORE_URL,
        params={"bounds": bounds, "activity_type": "riding"},
        headers={"Authorization": f"Bearer {get_access_token()}"},
        timeout=30,
    )
    resp.raise_for_status()
    segments = resp.json().get("segments", [])
    for s in segments:
        s["points"] = decode_polyline(s.get("points", ""))
    return segments


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode a Google encoded polyline to (lat, lon) pairs."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        for coord in ("lat", "lon"):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if coord == "lat":
                lat += delta
            else:
                lon += delta
        points.append((lat / 1e5, lon / 1e5))
    return points
