"""Every request to an OpenStreetMap service names this app, the same way.

The OSMF usage policies for Nominatim and Overpass ask clients to identify
the application with a User-Agent. Nominatim lookups said
"cycling-agentic-flow-route-prototype/0.1" -- another project's name --
and Overpass queries said "route-gen-ai/0.1": two names for one app, and
neither said where to find whoever runs it.
"""
import pytest
import requests

from routes import geocode, interruptions, places, street_list


class Answer:
    status_code = 200

    def __init__(self, url):
        self.url = url

    def raise_for_status(self):
        pass

    def json(self):
        if self.url.endswith("/reverse"):
            return {"address": {"road": "East Main Street", "city": "Madison"}}
        return [{"lat": "43.0747", "lon": "-89.3842", "display_name": "Capitol Square"}]


@pytest.fixture
def sent(monkeypatch):
    """User-Agent of every request, whichever verb sent it."""
    agents = []

    def capture(url, **kw):
        agents.append(kw.get("headers", {}).get("User-Agent"))
        return Answer(url)
    monkeypatch.setattr(requests, "get", capture)
    monkeypatch.setattr(requests, "post", capture)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(geocode, "wait_for_nominatim", lambda: None)
    monkeypatch.setattr(places, "wait_for_nominatim", lambda: None)
    return agents


def test_the_identity_names_the_app_and_where_to_find_it():
    assert geocode.USER_AGENT.startswith("route-gen-ai/")
    assert "https://github.com/NKimball1/route-gen-ai" in geocode.USER_AGENT
    assert "cycling-agentic-flow" not in geocode.USER_AGENT


@pytest.mark.parametrize("call", [
    lambda: geocode.geocode("Capitol Square, Madison WI"),
    lambda: places.road_at(43.0747, -89.3842),
    lambda: street_list.fetch_street_ways("Monroe Street", (43.06, -89.42)),
    lambda: interruptions._post_one(interruptions.OVERPASS_URLS[0], "[out:json];node(1);out;"),
], ids=["nominatim search", "nominatim reverse", "street ways", "overpass"])
def test_every_osm_request_carries_it(sent, call):
    call()
    assert sent == [geocode.USER_AGENT]
