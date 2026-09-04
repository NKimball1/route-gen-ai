# Route Gen AI

Describe a ride in plain English, get a Garmin-ready GPX:

```
python ask.py "give me a 30ish mile loop from home, less than 1000 ft of climbing"
python ask.py "50 miles, as much climbing as you can, out and back or loop is fine"
python ask.py "find me a flat spot within 30 min of my house for 2x20 threshold intervals"
python ask.py "a spot close to home for 4x5 VO2 intervals against a slight incline"
```

## Architecture

The LLM only translates intent; it never touches geometry. Deterministic code
owns the search and the judgment, and routing engines are swappable backends.

1. **Parse** (`routes/nl.py`): one small Claude call (claude-haiku-4-5 by
   default, ~$0.002/request — structured outputs, so responses are
   schema-valid with no retry loop) turns the request into a typed spec:
   either a route request or an interval-spot request.
2. **Generate**:
   - Routes (`routes/providers.py` + `routes/pipeline.py`): via-points on a
     circle through the start make loops out of point-to-point routing;
     turnaround points make out-and-backs. Candidates across many compass
     bearings, adaptive rescaling toward the target distance, spur artifacts
     excised by `routes/despur.py`.
   - Interval spots (`routes/intervals.py`): spokes radiate from the start,
     a window slides along each spoke's geometry, and windows are scored on
     length, gradient character (flat and steady vs. a consistent ~4%
     climb), and turn density.
3. **Validate & rank** (`routes/scoring.py`): distance tolerance, climb caps,
   climb maximization — all computed, never judged by the LLM.
4. **Output**: GPX tracks (`output/routes/`, `output/spots/`) importable to
   Garmin Connect as courses, plus a Leaflet map preview (`preview.html`).

Routing backends: **BRouter** — self-hosted at `C:\Users\me\brouter`
(start with `start_brouter.cmd`, port 17777; `BROUTER_URL` in `.env` points
there, comment it out to fall back to the public brouter.de server, which
rate-limits) — and **OpenRouteService** (activates when `ORS_API_KEY` is
set). Geocoding is OSM Nominatim (free). Avoid-zones ("not Verona Rd") ride
along as BRouter `nogos`.

Profiles: `fastbike-lowtraffic` (default) avoids busy roads;
**`fastbike-quiet`** (custom, self-hosted only) additionally penalizes
primary/secondary/tertiary — i.e. county-highway-class — roads 2-3x, keeping
rides on quiet rural/residential roads at the cost of longer detours. Pass
`--profile fastbike-quiet`. Routing data covers the two 5° tiles around
southern Wisconsin (W90_N40, W95_N40); grab more `.rd5` tiles from
brouter.de/brouter/segments4/ into `segments4\` for other regions.

## Direct CLIs (no LLM, no API key needed)

```
python compose_route.py --address "..." --miles 30 --max-climb-ft 1000
python compose_route.py --address "..." --miles 50 --maximize-climb --shape both
python compose_route.py --address "..." --miles 30 --avoid "Verona Rd, Madison WI:1500"
python find_spot.py --address "..." --reps 2 --rep-minutes 20 --kind flat --max-travel-minutes 30
python find_spot.py --address "..." --reps 4 --rep-minutes 5 --kind incline
python -m routes.preview output/routes/*.gpx   # rebuild a map preview
```

The public BRouter server takes a few seconds per leg; a full run is 1–3 min.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # or edit .env: API key, home address
```

`ask.py` needs `ANTHROPIC_API_KEY`; everything else runs keyless.
Dev: `pip install -r requirements-dev.txt` then `python -m pytest tests/`.

## Known limits / next steps

- Interval "traffic interruptions" are proxied by turn density; stop signs
  and traffic lights would need an OSM Overpass query (upgrade path).
- Strava segment-explore integration (popularity scoring; routing to real
  categorized climbs for max-climb requests) is designed but not built.
- ORS provider skips out-and-backs and avoid-zones (BRouter covers both).
