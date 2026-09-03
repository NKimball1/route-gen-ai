# Route Gen AI

Turn a natural-language ride request into a Garmin-ready GPX cycling route.

Target prompts:

- "Give me a ~30ish mile ride from this address, less than 1000 feet of
  climbing, full loop back to the start."
- "Create a 50 mile ride with as much climbing as you can from this address."

## How it works

The composer owns loop generation and scoring; routing engines are swappable
backends (deliberately not tied to one API):

1. **Geocode** the start address (OSM Nominatim, free, no key).
2. **Generate candidates**: place via-points on a circle through the start at
   several compass bearings and ask a bike router for the legs
   (`routes/providers.py`). Backends:
   - **BRouter** — public brouter.de server, no API key, "trekking" profile
     (favors quiet roads and bike infrastructure). Default.
   - **OpenRouteService** — native round-trip generator; activates when
     `ORS_API_KEY` is set (free key: https://openrouteservice.org). Hosted
     round trips cap at 100 km.
3. **Filter and rank deterministically** (`routes/scoring.py`): distance
   within ±15% of target, climbing under the cap (or maximized), no LLM
   judgment in the loop.
4. **Output**: GPX tracks in `output/routes/` (import to Garmin Connect as a
   course) plus `output/routes/preview.html`, a Leaflet map grid of all
   candidates.

The LLM layer (parsing the natural-language request into a `RouteSpec`) is not
built yet — CLI flags stand in for it. Planned: Strava segment-explore scoring
(popularity + climb category) to prefer roads cyclists actually ride.

## Usage

```
python compose_route.py --address "Madison, Wisconsin" --miles 30 --max-climb-ft 1000
python compose_route.py --address "Boulder, Colorado" --miles 50 --maximize-climb
python -m routes.preview output/routes/*.gpx   # rebuild the map preview only
```

`--candidates N` (default 6) controls loops per provider; `--provider
brouter|ors|all` forces a backend. The public BRouter server takes a few
seconds per leg, so a full run is 1–3 minutes.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
