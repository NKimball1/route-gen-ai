# Development Log — Route Gen AI

Every iteration, field test, hurdle, and fix, in order. The pattern
throughout: **generate → validate deterministically → field-test on real
roads → turn each complaint into a permanent, unit-tested fix.** The LLM
only parses intent; every quality decision is computed.

---

## Phase 0 — Viability research (2026-09-02)

**Question:** can an LLM tool compose safe cycling routes? Assumed needs:
Strava heatmap data, bike-safety road data, GPX output.

**Findings that shaped everything:**
- Strava's global heatmap has **no API** (Metro licensing only; tile scraping
  violates ToS). Substitute: the segment-explore endpoint returns popular
  segments — a legal popularity proxy.
- Bike-safety road data is a solved problem: OpenStreetMap tags bike
  infrastructure, and open routers (BRouter, OpenRouteService) already
  weight it. **Don't build road data; rent a router.**
- Key architecture decision: **the LLM never touches geometry.** It can't
  reliably invent coordinates or know which roads connect. LLM = intent
  parsing at the edges; deterministic search + validation in the core.
  (Mirrors ADR 0004 from the companion coaching-agent project:
  computable criteria get computed, not judged by a model.)

## Phase 1 — Prototype (2026-09-03, `0817608`)

Loop synthesis trick: routers only answer point-to-point, so place
via-points on a circle through the start and route around them. The circle
radius adapts (roads wander ~1.35x the geometric circle). Any point-to-point
bike router becomes a swappable backend — BRouter (public server, no key)
and OpenRouteService both implemented.

**First live results** (Madison + Boulder test prompts): 6/6 valid loops
under a climb cap; a 49 mi / 5,112 ft max-climb loop that found Boulder's
canyons by blind bearing search. Distance error tuned from +14% to ±5% by
measuring actual road-winding vs. the geometric circle.

## Phase 2 — Field test round 1: spurs and fast roads (2026-09-04, `f8775ff`)

First rides reviewed by an actual cyclist produced two complaints:

1. **Out-and-back spur artifacts** — the router detours to touch a
   geometric via-point and retraces. Hoped-for fix (BRouter's
   `correctMisplacedViaPoints` over HTTP) turned out to be an
   **unimplemented feature request** — dead end, documented.
   Built instead: `despur.py`, palindrome detection on the track. The
   measurement was the shock: one "1,375 ft of climbing" route contained
   **11.9 miles of retraced road**, and most of its climbing was the same
   hill counted out-and-back. Consequence: despurring must run **inside**
   candidate generation, before distance/climb accounting — otherwise every
   number lies.
2. **55+ mph roads** — switched profile to BRouter's `fastbike-lowtraffic`.

Also added `--shape outback|both`: out-and-backs mirror a one-way leg
(despurring applies to the one-way leg only — a finished out-and-back IS one
giant palindrome), and return-leg climbing = outbound descent.

## Phase 3 — Interval-spot finder (2026-09-04, `67929e0` → `032b1d2`)

New request type: "find me a spot for 2x20 threshold intervals — flat, few
interruptions." Search: route spokes outward, slide a window along each
spoke's geometry, score on length / gradient character / turn density.

**Field test round 2:** the top VO2 spot had a stop sign and a busy light.
Turn density can't see a stop sign on a straight road. Fix (`437ea79`): one
OSM Overpass query fetches every tagged traffic control in the search area
(~3,200 around Madison); windows are scored against real stop signs and
signals.

**Tuning insight:** per-km penalties weren't enough — a 6.6 mi stretch with
8 stops still beat a clean 2.4 mi stretch. What matters is **interruptions
per rep**: lapping a short stretch re-encounters its controls, and a
20-minute effort broken every 2.5 minutes is worthless. Made that term
dominant; a unit test pins the exact complaint case.

**Field test round 3** caught the counting itself lying (`032b1d2`): a major
signalized intersection counted as zero because (a) OSM maps one
intersection as four corner signal-nodes → clustering added, (b) control
positions used chord distances while window bounds used road distances —
hundreds of meters of drift at 10 km out, (c) a light exactly at the window
boundary was excluded even though you'd turn around at it every lap →
±150 m boundary pad. New verification habit born here: independently
re-fetch controls around the winning stretch's own midpoint and re-count.

**Napkin-math detour:** rider asked if a 0.9 mi @ 2.8% climb lasts a 4-min
rep at 300 W. Physics model says no — ~3:10 (17 mph at that grade). Exposed
that rep sizing should be power-based, not a flat speed assumption. Logged
as future work.

## Phase 4 — Via places and route-shape honesty (2026-09-04, `544721f` → `fa5e9fa`)

"50 miles through Fitchburg and Verona" added `--via`. First implementation
anchored routes to each town's **exact geocoded center point** — field
screenshots showed routes "jutting out to touch" the towns and one route
riding the same road for miles with a loop in between.

Three fixes from one screenshot review:
- **Soft vias** (`2565e10`): "through a town" = within 2 km. Natural
  sweeping loops that happen to pass through both towns are generated first
  and **ranked above** anchored ones. The winning route went from
  touch-and-retreat tendrils to an organic town-to-town loop.
- **Lollipop-stick detection** (`overlap.py`): same road ridden twice with a
  big loop in between is invisible to palindrome despurring. Grid-hash
  repeated-road fraction; loops >25% repeated are rejected; every result
  reports a "repeat %".
- **Corridor despurring** (`fa5e9fa`): a tendril whose return leg uses
  slightly different geometry (parallel path, offset lanes) defeats exact
  10 m matching. Resample to uniform 25 m spacing and mirror-match within a
  32 m corridor. Removed the last observed jut.

Also learned: BRouter's smoothed ascent reads ~½–⅓ of viewer DEM sums
(810 vs 2,253 ft on one route). Relative ranking holds; absolute feet are
estimates. And `fastbike-verylowtraffic` proved **unusable** for waypoint
synthesis — too-strict profiles make the router reach waypoints by massive
detour-retraces (up to 65 mi trimmed). Penalty tuning is a Goldilocks
problem, verified empirically.

## Phase 5 — Self-hosting (2026-09-04, `35b27ec`)

The public brouter.de server returned **403 rate limits** after a day of
iteration — and a future public website could never lean on it anyway.
Self-hosted BRouter 1.7.10: portable Temurin JRE 21 (system Java 8 was too
old; portable = zero system changes), Wisconsin tiles, ~200 MB total.

**Result: full 6-candidate run in 10.7 s vs 1–3 min public.** Iteration
became interactive. And self-hosting unlocked custom cost profiles — the
lever the county-highway complaint needed.

## Phase 6 — The quiet profile and the highway auditor (2026-09-04, `78760a6`)

Custom `fastbike-quiet` profile: primary/secondary/tertiary (Wisconsin
county-highway classes) cost 3–5x, quiet rural roads stay cheap. Field test
round 4: one route **rode onto US 18/151 and U-turned on the divided
highway.** Two-layer fix:
- Profile: trunk cost 10 → 100 (never ride along expressway-class roads).
- **Validator:** BRouter's per-segment WayTags now feed a per-candidate
  count of meters ridden on motorway/trunk/primary; >800 m = rejected, and
  every result shows a "major" column. "How much of this ride is on roads
  I'd hate" is now measured, not hoped.

After the fix, the route the rider had independently rated "actually pretty
good" ranked #1 — with a verified 0 major-road miles. The U-turn route
self-eliminated.

## Phase 7 — LLM layer live (2026-09-04, `79cbaea`)

One structured-output call on claude-haiku-4-5 parses plain English into a
typed spec (route or interval-spot). ~1,200 in / 70 out tokens ≈
**$0.0016/request** — priced for a public website from day one. Schema-valid
JSON by construction, so no retry loop.

**First live parse found a bug in minutes:** the model returned the address
as the literal string `"home"`, which the geocoder resolved to *Home, Pierce
County, Washington* — and the Wisconsin-only tiles correctly refused to
route a loop 1,400 miles away. Prompt fixed + defensive handling. Both
original target prompts now run end-to-end, sentence → GPX, in ~15 s.

## Phase 8 — Strava, the rug-pull, and open-data climb targeting (2026-09-04)

Credentials arrived; token refresh worked on the first try (reusing the
coaching project's refresh token, no browser re-auth). Then the planned
centerpiece — the segment-explore endpoint — returned a bare 401 with valid
credentials and correct scopes. Diagnosis by elimination: `/athlete` worked,
`/segments/starred` worked, only explore failed. The answer was in Strava's
changelog: **explore was gated behind an "Extended Access Tier" on
2026-09-01 — three days before this integration.**

Lesson worth the tuition: don't build core features on a third party's
restricted endpoints. The redesign is better than the original plan:

- **Climb targeting from open data** (`climbs.py`): route spokes outward,
  extract sustained ascents from the elevation profiles (≥2.5% avg,
  ≥25 m gain, dips ≤12 m tolerated), dedupe by location, and add route
  candidates that pass THROUGH the top climbs via the existing soft-via
  machinery. Works for any user, no API dependency.
- **Starred Strava segments as the personal layer**: still served on the
  standard tier — starring a climb in the Strava app makes it a routing
  target, competing on gain with the elevation finds.

First live max-climb run with targeting: best 50-mi result yet
(45.4 mi / 1,490 ft, zero major-road miles, 1% repeat) — every top-4
candidate came through a targeted climb. Honest limits, logged: the
elevation search found 40–48 m ridge climbs but not the region's marquee
climb near the search radius edge (spoke reach + sampling), and badly-placed
extension bows still waste candidates (they self-reject; a placement
heuristic would save requests).

## Phase 9 — Riding-partner review round (2026-09-04)

The max-climb route got loaded into RideWithGPS, sent to a Garmin, and
rated "really good" — with two field findings:

1. **A grocery-store parking lot on the route.** OSM tags parking aisles as
   `highway=service` + `service=parking_aisle`, which the profile costed at
   1.2 — nearly free. BRouter's lookup table discriminates service types,
   so the quiet profile now costs parking aisles/driveways at 20x and
   general service roads at 2x. Reroute verified 134 m clear of the lot.
2. **Elevation figures read consistently short vs. RideWithGPS** (1,490 ft
   claimed, 2,483 on RWGPS). Root cause: BRouter's "filtered ascend"
   smooths aggressively; riders' devices don't. Fix: compute ascent from
   the final track's own elevation profile with a hysteresis threshold,
   **calibrated against RWGPS on the actual route** — 10 m hysteresis lands
   within ~2.5%. Bonus: computing on final geometry killed the
   ascent-arithmetic artifacts on heavily-despurred candidates ("92 ft over
   47 miles"). Reported numbers now speak the same language as the rider's
   head unit.

Also: GPX names cleaned up for route viewers (generation metadata moved to
the description field).

## Phase 10 — Peak-aware scouting: summiting Blue Mounds (2026-09-04)

The climb scout's structural blind spot: it reads elevation off routed
spokes, and routers only ride THROUGH-roads — but marquee climbs are often
dead-end spurs (the Blue Mounds park road tops out at the park and goes
nowhere). Three pieces closed it:

1. **OSM peaks as targets** (`peaks.py`): `natural=peak` nodes carry
   elevations; fetch them, dedupe twin summits, route toward the biggest
   ones deliberately, and harvest the climb that tops out nearest the peak.
2. **The averaging trap:** the first harvest returned *nothing* even though
   the routed leg demonstrably reached the summit (ended 62 m from the
   peak at 521 m). The climb extractor's maximal ascending run spanned
   miles of gentle valley approach plus the steep finish — average grade
   below threshold, whole 175 m climb rejected. Fix: take the earliest
   suffix of the run that meets both gain and grade — the climb proper,
   undiluted by its approach. Regression test encodes the exact shape.
3. **Protecting deliberate spurs:** the summit road is an out-and-back, and
   our own despurring would excise the very climb being targeted. Despur
   passes now accept protected points — a spur whose tip is near a
   requested via survives.

Result: "55 miles, maximize climbing" now produces a 54.0 mi / 2,986 ft
route that summits Blue Mounds (track max 525 m, 154 m from the peak node)
— a route the system could not construct at any distance the day before.
Runner-up context kept honest: a blind 60-mile loop out-climbs it slightly
on rollers without the summit; both are shown, the rider chooses.

## Testing & verification practices that emerged

- 24 unit tests: despurring (exact, corridor, palindrome semantics),
  interval scoring (including the exact field-complaint cases), overlap
  detection, ranking rules, control mapping.
- Every field complaint became a regression test before the fix shipped.
- Independent re-verification of winners (e.g., re-fetching traffic
  controls around a winning stretch's midpoint) caught a counting bug the
  primary path missed.
- Deterministic validators over LLM judgment, everywhere a criterion is
  computable: distance tolerance, climb caps, repeat %, major-road meters,
  interruptions per rep.

## Open items

- Strava segment integration (popularity scoring; routing to real
  categorized climbs) — designed, blocked on API credentials.
- Power-based interval rep sizing (rider watts + weight → stretch length).
- Uncontrolled-crossroads detection (road-crossing counting via Overpass).
- Distance spread after heavy trims; ascent accounting unreliable for
  heavily-trimmed candidates.
- Web frontend; multi-user Strava OAuth.
