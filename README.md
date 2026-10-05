# Route Gen AI

[![tests](https://github.com/NKimball1/route-gen-ai/actions/workflows/tests.yml/badge.svg)](https://github.com/NKimball1/route-gen-ai/actions/workflows/tests.yml)

Describe a ride in plain English, get a Garmin-ready GPX.

> **What it is:** [docs/OVERVIEW.md](docs/OVERVIEW.md) — the project,
> the stack, and the design decisions on one page.
> **How it was built:** [docs/DEVLOG.md](docs/DEVLOG.md) — 38 phases of
> field-tested iteration, every real-ride complaint turned into a
> permanent, unit-tested fix.
> **Does it work?** [evals/](evals/README.md) — a versioned set of 62
> plain-English requests, scored by deterministic checks recomputed from
> the GPX files rather than by asking a model. A third is held out.
> The written-up results are in
> [evals/site/routegen-evals.html](evals/site/routegen-evals.html).

**Web app:** run `start_app.cmd` (with `start_brouter.cmd` running) and open
http://localhost:8903 — one text box for routes, interval spots, and edits;
candidates draw on a map with GPX downloads; edits chain against the
current route. Same brain as the CLI below (routes/service.py).
Sessions are per-browser (concurrent users never share state), requests
are rate-limited (env-tunable `RATE_*`), and setting `ROUTEGEN_INVITE_CODE`
gates all session API reads and writes. Run one Uvicorn worker; jobs and
rate limits are process-local. Session IDs are bearer credentials, not accounts. Text pages like
[/about](static/pages/about.html) are drop-in files in `static/pages/`.

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
   a new route, edit, interval search, undo, or clarification. Current route
   length and configured home are context; runtime validation follows parsing.
2. **Generate**:
   - Routes (`routes/providers.py` + `routes/pipeline.py`): via-points on a
     circle through the start make loops out of point-to-point routing;
     turnaround points make out-and-backs. Candidates across many compass
     bearings, adaptive rescaling toward the target distance, spur artifacts
     excised by `routes/despur.py`.
   - Interval spots (`routes/intervals.py`): spokes radiate from the start,
     a window slides along each spoke's geometry, and windows are scored on
     length (sized from your watts when given), gradient character (flat
     means low climbing per km, not just a zero average), mapped stop signs
     and signals, and how much is unpaved or on busy roads.
3. **Validate & rank** (`routes/scoring.py`): distance tolerance, climb caps,
   requested shape, vias reached in order, excluded roads and areas, repeated
   road, major-highway meters — all computed, never judged by the LLM.
4. **Output**: GPX tracks (`output/routes/` and `output/spots/` from the CLI,
   `output/sessions/<id>/` from the web app) importable to Garmin Connect as
   courses, plus a Leaflet preview beside each GPX. Every file gets a unique
   name, so nothing is overwritten.

Code layout: `api.py` and the CLI scripts are thin adapters over
`routes/service.py` and its workflows (`pipeline.py` for new routes,
`edit_service.py`, `spot_service.py`). `routes/storage.py` owns session
history and undo, `routes/execution.py` cancellation, `routes/policy.py` the
named tolerances, and `routes/geometry.py`, `constraints.py` and `gpx_in.py`
the measurements, avoid rules and GPX reading. The browser code is
`static/app.js`. [docs/OVERVIEW.md](docs/OVERVIEW.md) has the boundaries.

Routing backends: **BRouter** — self-hosted (see docs/DEVLOG.md phase 5 for setup)
(start with `start_brouter.cmd`, port 17777; `BROUTER_URL` in `.env` points
there, comment it out to fall back to the public brouter.de server, which
rate-limits). **OpenRouteService** is an explicit experimental CLI option
(`--provider ors` or `all`, plus `ORS_API_KEY`), not an automatic fallback. Geocoding is OSM Nominatim (free). Named-road exclusions use OSM way geometry and are verified after routing;
area exclusions use circles. Missing road geometry causes a clear refusal.

Profiles: **`fastbike-quiet`** (custom, default when self-hosted)
penalizes primary/secondary/tertiary — i.e. county-highway-class — roads
2-3x on top of avoiding busy roads, keeping rides on quiet
rural/residential roads at the cost of longer detours;
`fastbike-lowtraffic` is the fallback default on the public server,
which lacks the custom profile. Override either with `--profile`. Routing data covers the two 5° tiles around
southern Wisconsin (W90_N40, W95_N40); grab more `.rd5` tiles from
brouter.de/brouter/segments4/ into `segments4\` for other regions.

## Direct CLIs (no LLM, no API key needed)

```
python compose_route.py --address "..." --miles 30 --max-climb-ft 1000
python compose_route.py --address "..." --miles 50 --maximize-climb --shape both
python compose_route.py --address "..." --miles 30 --avoid "Verona Rd, Madison WI:1500"
python find_spot.py --address "..." --reps 2 --rep-minutes 20 --kind flat --max-travel-minutes 30
python find_spot.py --address "..." --reps 4 --rep-minutes 5 --kind incline
python find_spot.py --address "..." --reps 4 --rep-minutes 4 --kind any --watts 285 --max-stops 0
python edit_route.py --avoid "Whitney Way, Madison WI"     # edits the current route
python street_route.py --start "Olbrich Park, Madison WI" --streets "Capital City State Trail -> Dempsey Road"
python -m routes.preview output/routes/*.gpx   # rebuild a map preview
```

The public BRouter server takes a few seconds per leg; a full run is 1–3 min.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # or edit .env: API key, home address
```

Natural-language requests in `ask.py` and the web app need `ANTHROPIC_API_KEY`.
Direct BRouter CLIs need no LLM key.
Dev: `pip install -r requirements-dev.txt` then `python -m pytest tests/`
and `python -m mypy`; frontend checks: `node --test tests/frontend.test.cjs` (the codebase is fully type-hinted; `mypy.ini` makes an
unannotated function an error).
Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips <proxy IP>`;
the app no longer reads `X-Forwarded-For` itself (a client could spoof it),
so without those flags every visitor shares the proxy's rate limit.
End-to-end smoke test against a running server (local or deployed):
`python scripts/simulate.py hostile route edits upload cancel spot`.
Evaluation campaign (`evals/README.md`):

```
python -m evals.runner --run myrun --tier offline     # failure injection; parser key/cache still needed
python -m evals.runner --run myrun --tier live        # needs BRouter + API key
python -m evals.report --run myrun --compare baseline
```

MIT licensed — see [LICENSE](LICENSE).

## Editing (generated or uploaded routes)

Upload a GPX track or route of one ride (button in the app) or use a generated route, then ask in
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
  builds the correction from the predecessor; failed corrections retain the
  current route

## Reliability and limits

The [review fixes and test record](docs/REVIEW_FIXES.md) explain the current
architecture and coverage. Use the operation selector to explicitly choose a
new ride, edit, or interval search when the wording is ambiguous.

- Unknown stop counts stay unknown. A hard stop cap requires available OSM
  control data. Data availability does not guarantee every real-world stop is mapped.
- Interval power sizing, rider+bike mass, either-direction searches, road names,
  and stop caps work through natural language as well as the direct CLI.
- Uploaded GPX may have missing elevation; results disclose that limitation.
  Segments split at pauses or dropouts (gaps up to 2 km) are joined; separate
  rides in one file are refused rather than bridged with a straight line.
- BRouter is the supported default. ORS lacks outbacks, avoid/via support and
  major-road metadata; unknown metadata is disclosed.
- Strava starred climbs require personal CLI `--use-strava` and project-local
  credentials. They are disabled in shared web requests.
- Job execution and rate limits are single-process. Restarting loses active
  jobs; committed route history survives. Independent CLI processes must not
  edit the same session concurrently.
- Map data, access restrictions, weather and elevation estimates still need
  rider judgment. The app verifies defined constraints against available data;
  it cannot certify a route's real-world safety.

Live verification (writes isolated test sessions using public landmarks):
`python scripts/verify_live.py`. It needs the parser key and a running BRouter.
