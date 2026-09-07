# Route Gen AI

Describe a ride in plain English, get a Garmin-ready GPX.

> **How it was built:** [docs/DEVLOG.md](docs/DEVLOG.md) — 17 phases of
> field-tested iteration, every real-ride complaint turned into a
> permanent, unit-tested fix.

**Web app:** run `start_app.cmd` (with `start_brouter.cmd` running) and open
http://localhost:8903 — one text box for routes, interval spots, and edits;
candidates draw on a map with GPX downloads; edits chain against the
current route. Same brain as the CLI below (routes/service.py).

CLI:

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

Routing backends: **BRouter** — self-hosted (see docs/DEVLOG.md phase 5 for setup)
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

## Editing (generated or uploaded routes)

Upload any GPX (button in the app) or use a generated route, then ask in
plain English — edits chain, with an undo button and a green/amber/red
outcome banner that verifies results instead of narrating them:

- "avoid Whitney Way" — avoids the ROAD as a line (real OSM way
  geometry; the result is verified by measuring on-road meters), with
  point-plus-radius fallback for parks/landmarks
- "go down the commuter path instead" / "add a stop at Colectivo" — via,
  including multi-waypoint chains ("through X, past Y, take Z")
- "make it ~8 miles longer" / "shorten it to 40 miles total"
- "start and end at my house" (anchor: loops rotate to their closest
  approach), "end at Olbrich Park", "route me from ADDR to the start
  and back at the end"
- corrective phrasing ("that wasn't what I meant — use Struck St")
  automatically reverts the bad change before applying the fix

## Known limits / next steps

- "Both out and back" edits change one pass of a corridor at a time.
- Uploaded GPX without elevation data reads low on climbing until edits
  splice in routed legs.
- Interval rep sizing assumes fixed speeds; power-based sizing (rider
  watts + weight) is designed but not built.
- Strava starred segments feed climb targeting; segment-explore requires
  Strava's Extended Access tier (application pending a public launch).
- ORS provider skips out-and-backs, avoid-zones, and via routing
  (BRouter covers all three).
