# Route Gen AI — project overview

**One sentence:** describe a bike ride in plain English — *"give me a
40-mile loop from home with under 1,500 ft of climbing, and don't put me
on Verona Road"* — and get back ranked, Garmin-ready GPX routes drawn on
a map, which you can then edit conversationally (*"avoid Whitney Way"*,
*"make it 10 miles longer"*, *"add a stop at Colectivo"*).

Status: runs locally (web app + CLI); public deployment in progress.
Routing data currently covers southern Wisconsin — worldwide is a tile
download, not a redesign.

## What it does

- **Route generation** — target distance, loop or out-and-back, climb
  caps ("flat as possible") or climb maximization ("as much climbing as
  you can" — it scouts real summits and routes through them), routes
  through named places, avoid zones.
- **Interval-spot finding** — "find me a flat stretch for 2x20
  threshold" or "a 4x5 VO2 hill" returns scored stretches of road: right
  length, right gradient character, minimal stop signs and traffic
  lights (verified against OSM data, not guessed).
- **Conversational route editing** — upload a GPX of one ride or use a generated
  one, then chain edits in plain English: avoid a road, add waypoints,
  extend/shorten, move the start or end, anchor a round trip, connect
  from another address. Undo is a button; corrections ("that wasn't what
  I meant — use Struck St") build from the preceding version; failed corrections keep the current version;
  and a green/amber/red banner reports the *verified* outcome — the app
  measures whether the edit actually worked (e.g., meters remaining on
  the avoided road) instead of claiming success.

## Architecture — the decision that matters

**The LLM only translates intent; it never touches geometry.** One cheap
call per request (claude-haiku-4-5, ~$0.002, structured outputs so the
response is schema-valid JSON — enum-locked modes, no retry loop, no
tools, no agentic loop). Everything after the parse is deterministic
code: generation, validation, ranking, and editing are all computed.
The geometry and orchestration layers are testable offline with fixed inputs.
Live parses and external map data can vary. Runtime validation rejects invalid
request combinations and non-finite values; resource bounds are disclosed.
A selected operation prevents an edit from silently creating a new ride.

The pipeline is **generator → validator**: synthesize many candidates
(via-points on a circle through the start for loops, turnaround points
for out-and-backs, across many compass bearings with adaptive
rescaling), then filter and rank on hard computed criteria — distance
tolerance, climb caps, % of the route riding the same road twice, meters
on major highways, spur artifacts excised. Bad candidates are rejected
with stated reasons, not smoothed over.

## Tools and stack

| Layer | Tech |
|---|---|
| NL parsing | Claude API (claude-haiku-4-5, structured outputs) |
| Routing engine | **BRouter, self-hosted** (Java, local tiles) with a **custom "fastbike-quiet" profile** that penalizes county-highway-class roads 2–3x; OpenRouteService as an explicit experimental CLI backend |
| Geodata | OSM Nominatim (geocoding, with retry/backoff and route-bounded lookups), Overpass API (traffic controls, road geometry, peaks), Strava starred climbs only with an explicit personal CLI option |
| Backend | Python, FastAPI, background jobs with live log streaming, per-session workspaces, sliding-window rate limits, invite-code gate |
| Frontend | Zero-build vanilla JS + Leaflet — one text box, candidates on a map, GPX downloads |
| Calibration | Routes were ridden with a Garmin; barometric FIT data calibrated the elevation model until predictions sat within instrument spread |
| Testing | pytest — 251 offline tests (mocked HTTP, no API keys needed) plus 24 live parser tests, frontend behavior checks, and live HTTP routing checks |

## The process

The whole build is documented in [DEVLOG.md](DEVLOG.md) — a running log
of generate → field-test → fix. Every real-ride complaint became a
regression test before the fix shipped: the hidden 11.9-mile retrace
that led to despurring; "avoid Whitney Way" circling the wrong kilometer
of road (fix: treat a road as a *line* using real OSM way geometry, then
verify by measuring on-road meters); the false "Done" on a failed edit
(fix: outcome verification); a concurrency bug where one user's log
stole another's (fix: thread-routed stdout); an LLM parser that hedged
in its notes but acted anyway. Plus a structured code review against an
11-type bug taxonomy and a written prompt-injection threat model.

## Application boundaries

`api.py` and the CLI are adapters. `routes/service.py` interprets requests;
`edit_service.py`, `spot_service.py`, and `pipeline.py` own the application
workflows. Geometry, routing, and scoring modules do not depend on CLI scripts.
`policy.py` names product tolerances. `storage.py` owns immutable filenames,
atomic selection/history, and per-session transactions. `execution.py` joins
cancellation to the commit boundary. The frontend stays vanilla JavaScript in
`static/app.js`; no build system or new UI framework was introduced.

See [REVIEW_FIXES.md](REVIEW_FIXES.md) for the issue-to-test map and remaining
single-process limitations. Saved evaluation reports are historical evidence,
not automatically updated claims about this revision.
