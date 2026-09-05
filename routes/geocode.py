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


def geocode_flexible(place: str) -> tuple[float, float, str]:
    """Geocode with fallbacks: an LLM (or user) may append the wrong city
    to a place name. Try the full string, then progressively drop trailing
    comma-separated parts ("X, Madison, WI" -> "X, Madison" -> "X")."""
    parts = [p.strip() for p in place.split(",")]
    last_error = None
    for n in range(len(parts), 0, -1):
        try:
            return geocode(", ".join(parts[:n]))
        except ValueError as e:
            last_error = e
    raise last_error
