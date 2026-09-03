"""Address -> coordinates via OSM Nominatim (free, no API key).

Nominatim usage policy: identify yourself with a User-Agent and stay
under 1 request/second. We make one request per compose run.
"""
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "cycling-agentic-flow-route-prototype/0.1"


def geocode(address: str) -> tuple[float, float, str]:
    """Return (lat, lon, display_name) for an address string."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode address: {address!r}")
    hit = results[0]
    return float(hit["lat"]), float(hit["lon"]), hit["display_name"]
