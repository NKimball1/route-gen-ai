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


def _query_variants(place: str) -> list[str]:
    """Progressively simpler queries: drop trailing comma-parts, and also
    try without generic suffixes ('greentree neighborhood' -> 'greentree' —
    OSM names the neighborhood, not the word 'neighborhood')."""
    parts = [p.strip() for p in place.split(",")]
    queries = []
    for n in range(len(parts), 0, -1):
        q = ", ".join(parts[:n])
        queries.append(q)
        for suffix in (" neighborhood", " area", " district"):
            if q.lower().endswith(suffix):
                queries.append(q[: -len(suffix)])
    return queries


def geocode_flexible(place: str,
                     near=None) -> tuple[float, float, str]:
    """Geocode with fallbacks: an LLM (or user) may append the wrong city
    to a place name. With `near` (minlat, minlon, maxlat, maxlon), bounded
    lookups run first — and an unbounded hit far outside that box is
    REJECTED rather than returned (a Madison request once matched an
    Applebee's in Pittsburgh; a confidently wrong place is worse than a
    clear 'not found')."""
    queries = _query_variants(place)
    last_error = None
    if near is not None:
        for q in queries:
            try:
                return _geocode_bounded(q, near)
            except ValueError as e:
                last_error = e
    for q in queries:
        try:
            lat, lon, name = geocode(q)
            if near is not None:
                minlat, minlon, maxlat, maxlon = near
                pad_lat = (maxlat - minlat)
                pad_lon = (maxlon - minlon)
                if not (minlat - pad_lat <= lat <= maxlat + pad_lat
                        and minlon - pad_lon <= lon <= maxlon + pad_lon):
                    raise ValueError(
                        f"only found {name.split(',')[0]!r} far from the "
                        f"route — try a road name plus city, or a landmark")
            return lat, lon, name
        except ValueError as e:
            last_error = e
    raise last_error
