"""Address -> coordinates via OSM Nominatim (free, no API key).

Nominatim usage policy: identify yourself with a User-Agent and stay
under 1 request/second. We make one request per compose run.
"""
import time
from typing import Any

import requests

from routes.spec import BBox
from routes.request_gate import wait_for_nominatim
from routes.execution import checkpoint

NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"
USER_AGENT: str = "cycling-agentic-flow-route-prototype/0.1"

class GeocodeError(Exception):
    """Base for the two ways a place lookup fails. They are different
    problems and deserve different words: the service being down is
    temporary and has nothing to do with the request, while a place that
    does not exist needs the user to rephrase. Callers used to see a raw
    requests.ConnectionError for the first and a bare ValueError for the
    second, and the service layer let both escape to the user as a
    traceback."""


class GeocodeUnavailable(GeocodeError, RuntimeError):
    """Nominatim could not be reached (after retries). Subclasses
    RuntimeError, which is what this path raised before it had a name."""


class GeocodeNotFound(GeocodeError, ValueError):
    """Nominatim answered, and has no such place. Subclasses ValueError so
    the existing fallback chains in geocode_flexible and edit_route, which
    catch ValueError to try a simpler query, keep working unchanged."""


RETRIES: int = 3
# Grows per attempt (1.5s, 3s); also keeps retries under Nominatim's
# 1 request/second usage policy.
RETRY_WAIT_S: float = 1.5
RETRYABLE_HTTP: tuple[int, ...] = (429, 502, 503, 504)


def _nominatim_get(params: dict[str, str | int]) -> list[dict[str, Any]]:
    """GET with retries — Nominatim occasionally 429s or times out, and
    one flaky lookup shouldn't kill a whole compose run."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(RETRY_WAIT_S * attempt)
        try:
            wait_for_nominatim()
            resp = requests.get(NOMINATIM_URL, params=params,
                                headers={"User-Agent": USER_AGENT},
                                timeout=30)
            if resp.status_code in RETRYABLE_HTTP:
                last = RuntimeError(
                    f"Nominatim HTTP {resp.status_code} (busy)")
                continue
            if resp.status_code >= 400:
                raise GeocodeUnavailable(f"Address lookup returned HTTP {resp.status_code}.")
            resp.raise_for_status()
            checkpoint()
            results = resp.json()
            if not isinstance(results, list):
                raise ValueError("Invalid geocoder response")
            return results
        except (requests.RequestException, ValueError) as e:
            last = e
    raise GeocodeUnavailable(
        f"could not reach the address lookup service: {last}") from last


def geocode(address: str) -> tuple[float, float, str]:
    """Return (lat, lon, display_name) for an address string."""
    results = _nominatim_get({"q": address, "format": "json", "limit": 1})
    if not results:
        raise GeocodeNotFound(f"Could not geocode address: {address!r}")
    hit = results[0]
    return float(hit["lat"]), float(hit["lon"]), hit["display_name"]


def _geocode_bounded(query: str, near: BBox) -> tuple[float, float, str]:
    """Geocode restricted to a (minlat, minlon, maxlat, maxlon) box — so
    'Whitney Way' on a Madison route finds Madison's, not one anywhere."""
    minlat, minlon, maxlat, maxlon = near
    results = _nominatim_get(
        {"q": query, "format": "json", "limit": 1,
         "viewbox": f"{minlon},{minlat},{maxlon},{maxlat}", "bounded": 1})
    if not results:
        raise GeocodeNotFound(f"Could not geocode near the route: {query!r}")
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
                     near: BBox | None = None) -> tuple[float, float, str]:
    """Geocode with fallbacks: an LLM (or user) may append the wrong city
    to a place name. With `near` (minlat, minlon, maxlat, maxlon), bounded
    lookups run first — and an unbounded hit far outside that box is
    REJECTED rather than returned (a Madison request once matched an
    Applebee's in Pittsburgh; a confidently wrong place is worse than a
    clear 'not found')."""
    queries = _query_variants(place)
    last_error: BaseException | None = None
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
                    raise GeocodeNotFound(
                        f"only found {name.split(',')[0]!r} far from the "
                        f"route — try a road name plus city, or a landmark")
            return lat, lon, name
        except ValueError as e:
            last_error = e
    raise last_error or GeocodeNotFound(f"Could not geocode: {place!r}")
