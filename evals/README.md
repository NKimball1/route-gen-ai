# Evaluation harness

Does a plain-English ride request actually produce a route that satisfies the
sentence? This directory answers that with computation rather than opinion.

It drives **`routes.service.handle_request`** — the same entry point the web
app and the CLI call — so a pass here is a pass for the shipped code path, not
for a test double.

```
evals/
  cases_v1.json        the versioned request set + frozen expectations
  runner.py            executes cases; resumable, cached, budget-aware
  scorers.py           the acceptance criteria, with the reason for each limit
  geo.py               measurement primitives, independent of routes/*
  roadcheck.py         asks the router whether a long straight segment is a road
  paths.py             repo-relative paths, so saved results stay portable
  rescore.py           re-grade saved runs without re-running anything
  report.py            per-case records -> one summary.json per run
  mapgen.py            self-contained SVG route maps + elevation profiles
  story.py             the case study's prose (never its numbers)
  build_case_study.py  summary.json -> the portfolio HTML page
  results/<run>/       cases/*.json, work/ (the GPX files), summary.json
  results/e2e_simulate.log   tier C: the HTTP end-to-end run
  BRIEF.md             what the campaign found, and interview material
  cache/               parses, geocodes, road and context geometry
```

## Running it

```bash
# Unit suite: no keys or routing server
python -m pytest tests/ -q

# Failure-injection campaign: still needs live parser key or a matching parse cache
python -m evals.runner --run myrun --tier offline

# live end-to-end: needs a self-hosted BRouter and ANTHROPIC_API_KEY
start_brouter.cmd
python -m evals.runner --run myrun --tier live
python -m evals.report --run myrun --compare baseline
python -m evals.build_case_study --run myrun --baseline baseline
```

Useful flags: `--split heldout`, `--category edit,via`, `--ids D14,D15`,
`--force` to re-run finished cases, `--repeats N` for variability,
`--parse-only --no-parse-cache` to measure parse stability on its own.

## Three questions, kept apart

Conflating these hides where a failure actually is, so each case is graded on
all three and the report keeps them separate.

1. **Did it understand?** The parsed spec is compared field by field against
   the frozen expectation.
2. **Did it produce something valid?** The GPX is re-parsed with a real XML
   parser independent of the application reader and checked for range-valid
   coordinates, finite elevations, continuity, and at least two points.
3. **Does it satisfy the sentence?** Distance by haversine over the geometry,
   loop closure, start proximity, closest approach to each waypoint, meters
   still ridden on an excluded road, climb against a stated ceiling.

Checks are **hard** (a promise the product makes; failing one turns the case
red) or **soft** (a preference it tries to honor; failing one makes it amber).
Verdicts are `pass` / `partial` / `fail` / `inconclusive` — the last for an
environment problem, such as the routing server dying mid-run, which is not
evidence either way.

**No model grades anything.** In particular the model that produced a route is
never asked whether the route is good.

## Three tiers, reported separately

| Tier | What runs | Why separate |
|---|---|---|
| offline | failure injection (dead router, dead geocoder); live/cached parsing | no successful live routing is exercised; use pytest for truly offline checks |
| live | real parse, real self-hosted BRouter, real Nominatim/Overpass | the only tier that is evidence about the product end to end |
| e2e server | `python scripts/simulate.py` against a running app | covers HTTP concerns the in-process harness cannot see: rate limits, session isolation, cancellation, path traversal |

## Dev and held-out

Roughly two thirds of cases are `dev` and one third `heldout`. Held-out
expectations were frozen in `cases_v1.json` before any fix was written, and no
held-out failure was used to shape a change. Where a fix was designed against a
dev case and a held-out case shares its root cause, the case study says so
explicitly rather than presenting the held-out pass as independent.

## Cost and provider limits

The only paid call is the parse — one small `claude-haiku-4-5` request per
case. Routing (self-hosted BRouter), geocoding (Nominatim) and road geometry
(Overpass) are free.

Parses are cached by `(model, system prompt, schema, route/start context, request text)`. **The system
prompt is part of the key on purpose**: without it, editing the prompt and
re-running would silently score the new build against the old parses — exactly
the mistake that would hide whether a prompt fix worked. A routing-side fix, by
contrast, re-runs at zero model cost and against a byte-identical
interpretation.

Geocodes, road centerlines and map context are cached on disk; a persistent
lookup failure is recorded once rather than retried forever; Nominatim calls
are spaced to its 1 request/second policy. Every case writes its record the
moment it finishes, so an interrupted run resumes where it stopped.

## Scoring is a pure function

`rescore.py` re-grades saved runs from their records and their GPX files, with
no API call and no route generation. That is what makes before/after
comparisons meaningful: every run in a campaign can be re-scored under
identical rules after a tolerance changes. Each rescore appends to the case's
`verdict_history`, so a number that moved shows that it moved.

The harness's own judgements are tested in `tests/test_evals_scoring.py`.
Three checks were wrong before they were right — all corrected *before* any
product change, and all recorded in the case study rather than quietly fixed:
a gap threshold that flagged straight rural miles, a turn test that flagged
grid corners, and an out-and-back detector that scored an exact palindrome at
0.6% because it compared by index instead of by arc length. A loosened check
is worthless if it can no longer catch what it exists for, so each is tested
from both sides — the legitimate geometry it must accept, and the broken
geometry it must still reject.

## What this cannot establish

Nothing here says a route is **safe or pleasant to ride**. Traffic volume,
shoulder width, pavement quality, sightlines and seasonal conditions are
outside what OSM tags and this harness can see. Elevation comes from the same
SRTM-derived data the router returns, so the climb checks verify a stated cap
against the model the product uses — they are not independent ground truth;
only a barometric ride is. Coverage is southern Wisconsin, the region whose
routing tiles are loaded.

## Privacy

Every start address in `cases_v1.json` is a public landmark. The runner
overrides `ROUTEGEN_HOME_ADDRESS` with a public default so a case saying
"home" fails loudly instead of leaking a private address, and the case-study
builder strips absolute paths out of anything it publishes.

## Review revision (2026-10-03)

Fresh records preserve result warnings. Unsupported requirements must appear in
the user-visible summary; a partial route must be marked partial with warnings.
Unverified hard geometry/waypoint/avoid checks are inconclusive, never a pass.
Named-road avoidance is a hard constraint. Defaults now reflect the product's
150 m waypoint and 30 m on-road limits; explicit versioned case overrides are
still honored. Historical results/site pages have not been silently regenerated.
For a comparable new campaign, run both revisions with the same scorer/cases and
record that scorer version; do not compare new verdicts to old scoring rules.
