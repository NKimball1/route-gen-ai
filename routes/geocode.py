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


def _geocode_bounded(query: str, near) -> tuple[float, float, str]:
    """Geocode restricted to a (minlat, minlon, maxlat, maxlon) box — so
    'Whitney Way' on a Madison route finds Madison's, not one anywhere."""
    minlat, minlon, maxlat, maxlon = near
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1,
                "viewbox": f"{minlon},{minlat},{maxlon},{maxlat}",
                "bounded": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not geocode near the route: {query!r}")
    hit = results[0]
    return float(hit["lat"]), float(hit["lon"]), hit["display_name"]


def geocode_flexible(place: str,
                     near=None) -> tuple[float, float, str]:
    """Geocode with fallbacks: an LLM (or user) may append the wrong city
    to a place name. Try the full string, then progressively drop trailing
    comma-separated parts ("X, Madison, WI" -> "X, Madison" -> "X").
    With `near` (minlat, minlon, maxlat, maxlon), bounded lookups run
    first, unbounded only as a last resort."""
    parts = [p.strip() for p in place.split(",")]
    queries = [", ".join(parts[:n]) for n in range(len(parts), 0, -1)]
    last_error = None
    if near is not None:
        for q in queries:
            try:
                return _geocode_bounded(q, near)
            except ValueError as e:
                last_error = e
    for q in queries:
        try:
            return geocode(q)
        except ValueError as e:
            last_error = e
    raise last_error
