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

## Phase 1 — Prototype (2026-09-03)

Loop synthesis trick: routers only answer point-to-point, so place
via-points on a circle through the start and route around them. The circle
radius adapts (roads wander ~1.35x the geometric circle). Any point-to-point
bike router becomes a swappable backend — BRouter (public server, no key)
and OpenRouteService both implemented.

**First live results** (Madison + Boulder test prompts): 6/6 valid loops
under a climb cap; a 49 mi / 5,112 ft max-climb loop that found Boulder's
canyons by blind bearing search. Distance error tuned from +14% to ±5% by
measuring actual road-winding vs. the geometric circle.

## Phase 2 — Field test round 1: spurs and fast roads (2026-09-04)

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

## Phase 3 — Interval-spot finder (2026-09-04 →)

New request type: "find me a spot for 2x20 threshold intervals — flat, few
interruptions." Search: route spokes outward, slide a window along each
spoke's geometry, score on length / gradient character / turn density.

**Field test round 2:** the top VO2 spot had a stop sign and a busy light.
Turn density can't see a stop sign on a straight road. Fix: one
OSM Overpass query fetches every tagged traffic control in the search area
(~3,200 around Madison); windows are scored against real stop signs and
signals.

**Tuning insight:** per-km penalties weren't enough — a 6.6 mi stretch with
8 stops still beat a clean 2.4 mi stretch. What matters is **interruptions
per rep**: lapping a short stretch re-encounters its controls, and a
20-minute effort broken every 2.5 minutes is worthless. Made that term
dominant; a unit test pins the exact complaint case.

**Field test round 3** caught the counting itself lying: a major
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

## Phase 4 — Via places and route-shape honesty (2026-09-04 →)

"50 miles through Fitchburg and Verona" added `--via`. First implementation
anchored routes to each town's **exact geocoded center point** — field
screenshots showed routes "jutting out to touch" the towns and one route
riding the same road for miles with a loop in between.

Three fixes from one screenshot review:
- **Soft vias**: "through a town" = within 2 km. Natural
  sweeping loops that happen to pass through both towns are generated first
  and **ranked above** anchored ones. The winning route went from
  touch-and-retreat tendrils to an organic town-to-town loop.
- **Lollipop-stick detection** (`overlap.py`): same road ridden twice with a
  big loop in between is invisible to palindrome despurring. Grid-hash
  repeated-road fraction; loops >25% repeated are rejected; every result
  reports a "repeat %".
- **Corridor despurring**: a tendril whose return leg uses
  slightly different geometry (parallel path, offset lanes) defeats exact
  10 m matching. Resample to uniform 25 m spacing and mirror-match within a
  32 m corridor. Removed the last observed jut.

Also learned: BRouter's smoothed ascent reads ~½–⅓ of viewer DEM sums
(810 vs 2,253 ft on one route). Relative ranking holds; absolute feet are
estimates. And `fastbike-verylowtraffic` proved **unusable** for waypoint
synthesis — too-strict profiles make the router reach waypoints by massive
detour-retraces (up to 65 mi trimmed). Penalty tuning is a Goldilocks
problem, verified empirically.

## Phase 5 — Self-hosting (2026-09-04)

The public brouter.de server returned **403 rate limits** after a day of
iteration — and a future public website could never lean on it anyway.
Self-hosted BRouter 1.7.10: portable Temurin JRE 21 (system Java 8 was too
old; portable = zero system changes), Wisconsin tiles, ~200 MB total.

**Result: full 6-candidate run in 10.7 s vs 1–3 min public.** Iteration
became interactive. And self-hosting unlocked custom cost profiles — the
lever the county-highway complaint needed.

## Phase 6 — The quiet profile and the highway auditor (2026-09-04)

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

## Phase 7 — LLM layer live (2026-09-04)

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

## Phase 11 — The flat-route calibration miss (2026-09-05)

First real-world ride of an NL-generated route ("50 miles, flat as
possible") shipped promising 1,066 ft; RideWithGPS said 1,600. Yet the
hilly calibration route had matched within 3% the day before. The lesson:
**a single hysteresis threshold cannot represent both route types.** On a
hilly route the gain lives in big climbs and a 10 m threshold loses almost
nothing; on a flat route much of the gain is dozens of sub-10 m rollers —
all discarded. One calibration point hid the model's structural error;
the second exposed it.

Fix: two-point fit (hilly 2,483 / flat 1,600 ground truth) across a
smoothing×threshold grid selects a ~225 m rolling-mean smooth with a 0.5 m
threshold — smoothing absorbs DEM noise, the low threshold keeps rollers.
Error: +4.4% hilly, −6.3% flat. A regression test now encodes the roller
lesson directly. Every future device-measured ride is a free calibration
point.

## Phase 12 — The century ride: barometric ground truth (2026-09-05)

The rider took the flat 48-miler out — followed it ~46 miles, then peeled
off to complete a 100-mile century — and handed back the .fit file. That
file became infrastructure:

- **`compare_ride.py`**: parse the ridden .fit (barometric altitude),
  match it against the generated GPX, and compare device climbing vs the
  model over exactly the matched geometry. First version matched only
  4.3 mi because it treated the first sustained deviation as the end —
  real rides deviate and rejoin (7 separate on-route stretches here), so
  matching had to become chunk-based coverage.
- **The instruments disagree with each other.** Over the same matched
  roads: Garmin barometric 1,721 ft; RideWithGPS DEM ~1,600 route-wide;
  the model 1,263. Garmin runs 8–16% above RWGPS on identical ground —
  meaning ±10% is the attainable accuracy for ANY prediction, because
  "the right answer" depends on which instrument checks it.
- Refit centers the model in the instrument spread (125 m smooth, 1 m
  threshold): flat route 1,689 ft (RWGPS 1,600 / Garmin-derived 1,870),
  hilly 2,927 (RWGPS 2,483).

The pattern is now a loop: every device-measured ride is a free
calibration point, and the comparator turns a .fit file into one command.

## Phase 13 — Conversational editing (2026-09-05)

The idea arrived mid-century-ride: "I'd want to say *find a different
route around Pheasant Branch — those paths are gravel and walkers* and
have it keep the rest." Built the same day as a third request type:

- **Splice engine** (`editing.py`): find every contiguous stretch where
  the route enters the avoid zone, back off a 700 m buffer on each side,
  reroute each gap with a no-go circle over the zone, splice, recompute
  stats. The winner of any compose or edit becomes "the current route",
  so edits chain: generate → "around X" → "also avoid Y" → ride.
- Live demo on the actual example: replaced 2.9 mi of conservancy path
  with 7.7 mi of road; 95% of the route untouched.
- Two robustness lessons from one demo run: PowerShell writes a UTF-8 BOM
  that poisoned a path file (reader is now BOM-tolerant), and the parser
  appended the wrong city to the place name — the conservancy is in
  Middleton, not Madison — so geocoding now retries with progressively
  fewer comma-parts before failing.

This is the interaction model the future web frontend wraps: every
capability is already conversational at the CLI.

## Phase 14 — The web frontend (2026-09-05)

By the time the frontend was built, it was almost an anticlimax — the hard
part was thirteen phases of making the engine conversational. Two design
calls:

- **Zero build toolchain.** FastAPI + one vanilla HTML/JS page with
  Leaflet. No npm, no bundler; deploys anywhere Python runs with
  `git pull`. (Also pragmatic: this machine's npm is broken.)
- **One brain, two mouths.** `routes/service.py` extracts the CLI's
  dispatch into a shared handler returning structured candidates;
  `ask.py` and the API both call it. Requests run as background jobs with
  the pipeline's progress prints streamed live to the browser.

The page: one text box for everything (routes, interval spots, edits),
color-coded candidates drawn on the map, per-candidate GPX download and
"use" (which sets the current route that edits chain against). Verified
end-to-end in the browser: sentence in, three loops on the map, current
route auto-updated.

First UX review (same day) fixed three things: no more hardcoded Madison —
a first-run "where do your rides start?" field geocodes, centers the map,
persists per browser, and rides along with every request as the default
start (the server env address is only a fallback); "use" became "select"
with a visible ✓ on the current route and hint text explaining that the
selected route is what edits modify; and every candidate line gained a
dark casing + stronger colors so non-winners stay visible on the map.

## Phase 15 — "instead" is not "avoid" (2026-09-05)

A user asked to *use* a specific path ("can we go down the southwest
commuter path instead?") and got the opposite: the edit vocabulary only
had "avoid", so the parser shoehorned the request into it — while writing
its own doubts into the notes field ("if they actually want TO USE the
path, please clarify") and proceeding anyway. The mis-aimed edit then
no-oped, and the empty result cleared the map entirely. Three lessons,
three fixes:

1. **Vocabulary gaps become misinterpretations.** When the schema can only
   express "avoid", every request becomes avoidance. Edits now carry a
   mode — `avoid` routes around a place, `via` reroutes the nearest
   section THROUGH it (with the target protected from despurring, since
   riding out-and-back onto a path can be the point).
2. **A model that hedges in prose still acts.** The parser's uncertainty
   note was correct and useless — it doesn't gate anything. The durable
   fix was closing the vocabulary gap; a future one is treating
   low-confidence parses as questions back to the user.
3. **Failed operations must not destroy state.** A no-op edit now returns
   the unchanged route so the map keeps showing it, and the frontend
   ignores empty results outright.

Verified with the original sentence verbatim: parses as via, splices
5.0 mi through the path. Known limit: a long linear feature anchors at its
geocoded point — splicing along its full OSM geometry is future work.

## Phase 16 — Multi-user state (2026-09-06)

The prerequisite for anyone else using it. What was global: one
`latest.txt` current-route pointer, one shared output folder (concurrent
users would overwrite each other's GPX files), and — found only by
actually simulating two users — stdout capture itself.

- **Per-session workspaces**: each browser session carries a UUID
  (localStorage → X-Session-Id header) and owns `output/sessions/<id>/` —
  its GPX files and its current-route pointer. A session can only select
  its own files. Stale workspaces sweep after 7 days. The CLI keeps its
  single-user behavior.
- **One job per session** (429 otherwise); other sessions run freely in
  parallel.
- **The bug the test earned**: `contextlib.redirect_stdout` swaps stdout
  process-globally — with two concurrent jobs, one job's context exit
  stole or dropped the other's log (user B's log came back empty).
  Replaced with a thread-routed stdout installed once: each job thread
  writes to its own buffer, everything else falls through. Concurrency
  bugs don't announce themselves; the two-user test was the only reason
  this surfaced before real users hit it.

Verified live: two sessions with isolated current-route state and two
concurrent jobs with fully separate logs, zero cross-leak. Still open for
public deployment: rate limiting / auth (state isolation was this phase;
abuse control is its own).

## Phase 17 — Rate limits, usage log, invite gate (2026-09-06)

Abuse control, sized for a friendly beta. Session ids are client-generated
and trivially forgeable, so limits stack: per-session (12 asks/hour),
per-IP (20/hour), a global daily cap (400 — the LLM cost backstop at
~$0.002/request that keeps a hostile day under a dollar), a geocode
limit, and a global concurrent-jobs cap protecting the router. All
env-tunable. Live-verified: exactly 12 requests through, then 429s with
human-readable refusals.

Usage tracking without accounts: every ask / completion / error / refusal
appends a JSON line (session, IP, duration, result kind, and — unless
disabled — the request text, which is the product-improvement gold: what
do people actually ask a route tool for?). Auth stays deliberately light:
an optional shared invite code gates the expensive endpoint when set;
identity waits for "Sign in with Strava", which the roadmap wants anyway
for per-user segments.

## Phase 18 — Bring your own GPX (2026-09-06)

The user's insight: "I have a great route already, but one road I've found
I don't like riding." Editing an existing route is a smaller trust ask
than generating a whole one — and the edit system had unknowingly been
built for it all along, since it always operated on GPX files rather than
internal objects. Upload = hand it a file and declare it current.

Built against a written list of nine things a rider would actually ask:
avoid a road ✓ (existed), add a road / hit a specific cafe ✓ (via mode
existed), plus four new operations — **extend** (bow a section outward,
the loop-synthesis trick applied to an existing route), **shorten**
(bridge the best cuttable detour; an over-ask falls back to the best
partial cut with a note instead of refusing), **move_start / move_end**,
and **connect** (a leg from an address to the route's start, optionally
with the leg home at the end — the commute wrapper). Parse coverage: all
nine phrasings map to the right operation, verified against the live
parser before any routing code ran.

Two robustness details that came out of testing: uploaded files are
rewritten through the normalizer (which also strips timestamps and HR
data the original may carry — an accidental privacy win), and edit-place
geocoding is now bounded to the route's own bounding box, so "Whitney
Way" on a Madison route finds Madison's, not one anywhere on Earth.

Verified live, chained: upload a 19.8 mi loop → "make it ~5 miles longer"
→ 25.6 mi → "route me from 210 Langdon St to the start and back at the
end" → 37.6 mi commute-wrapped ride.

## Phase 19 — The backtracking start (2026-09-06)

First field test of the upload flow found it within minutes: "start from
this address" produced a ride that traveled to a point already on the
route, then back toward the old start — a doubled corridor. The user's
diagnosis ("routed to another point already on the ride, then back") was
exactly right: the join anchor was FIXED ~2 km along the old route, so
the new lead-in leg rode the route's own roads backward to reach it.

The fix is a semantics upgrade, not a patch: **join the route where it
passes nearest the new point.** Closed loops get the elegant version —
a loop has no privileged start, so ROTATE it until the closest-approach
point is the seam, then add one clean connecting leg. Open routes drop
the stretch before/after the join instead of re-riding it. Live check on
a real loop: start moved to a town the loop passes through → rotated
seam, 0.1 mi lead-in, 1% repeated road.

## Phase 20 — Anchor mode, and live-debugging a user session (2026-09-06)

The rider asked "start this ride from X **and end at X**, skip the tiny
original starting leg" — and got only half: the schema allows one mode
per edit, so the parser chose move_start, wrote the both-ends intent into
its notes (which gate nothing — the phase-15 lesson recurring), and left
the ride ending at the loop's old seam 3.6 mi from home.

The debugging itself is worth recording: because sessions are just
directories of GPX files plus a usage log, the whole failure was
reconstructable from disk — the original upload (a true 40.2 mi loop,
endpoints 82 m apart), edit1 (correct move_start: rotated, lead-in
added, but one-way), and edit3 (a 1.2-mi no-op because the start was
already right). No reproduction needed; the artifacts told the story.

Fix: mode **anchor** — a round trip from a place, as one operation.
Loops rotate to their closest approach and get a lead-in plus a
ride-home leg; open routes connect both ends and drop stubs; sub-150 m
connections are skipped. The exact reported sentence now parses to
anchor and turns the same uploaded loop into a 49.3 mi round trip
starting and ending 1 m from the requested address.

## Phase 21 — The silent failure (2026-09-06)

A rider proposed a neighborhood shortcut — "through Greentree, past Exact
Sciences, take the tunnel under the Beltline, both out and back" — and
the request quietly did nothing. Their sharpest complaint wasn't the
failure; it was that **nothing told them it failed**: the only evidence
was buried in a JSON log. Reconstructing from the session files found
three stacked defects:

1. **Vocabulary again** (third time — phases 15, 20): one place per edit,
   so the parser comma-joined three waypoints into a single garbage
   string. Now: a `places` list, chained into one spliced leg through
   every waypoint in ride order.
2. **Confidently wrong geocoding**: the garbage string's unbounded
   fallback matched an Applebee's on Greentree Road in **Pittsburgh,
   819 km away** (an 8 km sanity check saved the route, with a baffling
   message). Now: far-outside-the-route hits are rejected as "not
   found", and generic suffixes ("neighborhood") are stripped as query
   variants — OSM names the place, not the word.
3. **Found while fixing 1**: the first chain implementation matched the
   two waypoints to OPPOSITE passes of the out-and-back corridor and
   replaced 49 miles of ride with a 5-mile shortcut. Pass selection is
   now span-aware (smallest span wins; >45% of the ride refuses).

And the UX fix that motivated it all: every result now carries a
one-line outcome shown as a green/red banner — including partial truth:
*"Done — via Greentree neighborhood + Exact Sciences: now 52.2 mi
(+2.9 mi). Couldn't locate and skipped: tunnel under the beltline."*
Failures are loud, successes are specific, and nobody reads JSON to
learn what happened.

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
