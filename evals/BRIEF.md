# Morning brief — Route Gen AI evaluation campaign

> Historical snapshot from 2026-09-20, kept as written. Since then the
> per-run `work/` folders and `*.log` files stay local rather than in the
> repository, and the offline suite has grown (DEVLOG phases 37–38).

**Ran:** 2026-09-19 → 09-20. **Model spend:** $0.44 total, every run
included (budget was $2). **Request set:** `routegen-eval-v1`, 62 requests,
expectations frozen 2026-09-19 before any fix; 25 held out.

## The one-line answer

The concept works for **crisply specified requests in the region the routing
tiles cover**. It is not established for vague requests, and nothing here
establishes that a route is safe or pleasant to ride.

| | Baseline | After fixes |
|---|---|---|
| All 62 cases | 55 pass · 0 partial · 7 fail | **58 pass · 1 partial · 3 fail** |
| Live end-to-end (60) | 55 pass · 5 fail | **56 pass · 1 partial · 3 fail** |
| Held-out, live (25) | 22 pass · 3 fail | **23 pass · 2 fail** |
| Offline failure injection (2) | 0 pass · 2 fail | **2 pass** |

Per-promise, after the fixes: distance within ±15% **39/39**, loop closure
**19/19**, valid GPX **53/53**, stated climb ceiling **4/4**, waypoint reached
**10/10**, no break in the track **53/53**, starts where asked **39/39**,
request type understood **61/62**. Median distance error **1.8%** (worst
9.7%); median generation time **9.0 s** (interval-spot searches are the slow
ones, up to ~175 s, because they wait on Overpass).

## Where everything is

| Output | Path |
|---|---|
| **Case study (standalone)** | `evals/site/routegen-evals.html` |
| **Case study (in your site)** | `portfolio website 2026/public/routegen-evals.html`, linked from the Route Gen AI card; sitemap updated. Built, **not deployed**. |
| Methodology | `evals/README.md` |
| Request set + frozen expectations | `evals/cases_v1.json` |
| Harness | `evals/runner.py`, `scorers.py`, `geo.py`, `roadcheck.py`, `rescore.py`, `report.py` |
| Baseline results | `evals/results/baseline/` (per-case JSON, `summary.json`; GPX under `work/`, local only) |
| Post-fix results | `evals/results/postfix/` |
| Variability run | `evals/results/postfix/parse_variance.json` |
| HTTP end-to-end log | `evals/results/e2e_simulate.log` (50 checks, 0 issues; local only) |
| Featured route GPX | `evals/site/routegen/gpx/*.gpx` |
| Sanitized full results | `evals/site/routegen/results.json` |
| Write-up in the project log | `docs/DEVLOG.md` phase 36 |

Rerun: `python -m evals.runner --run myrun --tier live` then
`python -m evals.report --run myrun --compare baseline`.

## What was fixed

Four defects, all found on the development set, all with regression tests
(`tests/test_service_honesty.py`, `tests/test_evals_scoring.py`; 152 offline
tests pass, mypy clean).

1. **A new ride request answered as an edit** (`routes/nl.py`). *"I hate
   riding on Whitney Way — 22 mile loop from the Memorial Union without it"*
   parsed as an avoid-edit, the distance was dropped, and the rider got "No
   current route to edit". Fixed with one prompt rule: a stated length for
   the whole ride means a new route even alongside avoid/via language; no
   stated length means an edit, and never invent a distance.
2. **A dead router blamed the rider** (`service.py`, `pipeline.py`,
   `providers.py`). The log said the routing server was unreachable; the
   banner said "try a looser target or different distance". Plus a
   one-second preflight instead of a dozen two-minute timeouts.
3. **A geocoder failure escaped as a stack trace** (`geocode.py`,
   `service.py`). Now two named failure modes with different advice.
4. **An interval stretch too short for the rep, presented without the
   caveat** (`service.py`). The CLI had printed the lap count all along; the
   web label dropped it.

Three of the four are the same bug wearing different hats: the program had
already worked out the truth and handed the user a sentence that did not
contain it.

## What is NOT fixed — read before an interview

- **H23/H24 still fail.** The held-out mirror image of defect 1: *"route me
  through Vilas Park and then the Arboretum"* states no length, so it is an
  edit, but it still parses as a new route about 2 times in 5 — and when it
  does, the rider's current route is replaced and there is nothing to undo
  back to. The prompt rule turned a reliable failure into a coin flip, not a
  fix. **The durable answer is designed and deliberately not built**:
  recording a new route's parent so undo can recover the previous ride. The
  only evidence for it is in the held-out set, and spending that would cost
  the campaign its one clean measurement. Say this plainly if asked — it is
  a stronger answer than a green row.
- **D21 regressed** (`pass` → `fail`) and it is *not* a regression from the
  fixes. *"A short easy spin"* drew a 300 ft climb cap in both runs; the
  distance guess moved 8 → 10 mi between runs and at 10 mi nothing satisfied
  the cap. It is parse nondeterminism on a vague request, which the
  variability run then quantified.
- **Variability.** 9 requests × 5 parses with the cache off: 5 of 9 were
  identical on every constraint-driving field. All 5 crisp ones were stable;
  all 4 unstable ones underspecify the ride. *"I want to go for a bike ride"*
  gave four different distances, one of them zero miles (the clamp caught it).
- **Nothing was ridden.** Every claim is about geometry and stated
  constraints.
- **One region.** Southern Wisconsin tiles only.
- **The harness itself was wrong three times before it was right.** All three
  corrections predate any product change and each is pinned by a test that
  checks both directions. Disclosed on the page.

## Resume bullets — pick one

All three are defensible from the saved results. **Option A** is the safest;
**B** leads with the engineering; **C** is the shortest.

> **A.** Built a deterministic evaluation harness for an LLM-powered cycling
> route generator: 62 versioned natural-language requests with expectations
> frozen up front and a third held out, scored by recomputing distance, loop
> closure, waypoint proximity and elevation from the produced GPX rather than
> by asking a model. Found and fixed 4 defects; held-out pass rate 22/25 →
> 23/25, distance within ±15% on 39/39 routes, total model spend $0.44.

> **B.** Diagnosed and fixed four user-facing defects in an LLM route
> generator by grading the *message the user sees*, not just return values —
> including a request-classification bug that returned no route at all for a
> supported request, and a dead-dependency path that told users to change a
> request that was never the problem. Each fix shipped with a regression test
> that asserts both the fix and the behaviour it must not swallow.

> **C.** Measured an LLM route generator end to end against 62 frozen
> natural-language requests (a third held out), scored by deterministic
> geometry checks rather than model judgment; fixed 4 defects and quantified
> parse nondeterminism on underspecified requests. $0.44 in model spend.

**Needs your confirmation before use:** every bullet above says "I built" —
correct only if you're comfortable describing this session's work as your own
directed work, the way you describe AeroModel ("built by directing Claude
Code"). If you'd rather mirror that framing, say "directed" instead of
"built"; the numbers are unaffected either way.

## The 60–90 second version

> Route Gen AI turns a sentence like *"45 miles from Cross Plains, through
> Black Earth, under 2000 feet of climbing, and off Highway 14"* into a
> Garmin-ready GPX. The architecture decision that matters is that the
> language model only translates intent — one cheap structured-output call —
> and everything after it is deterministic code that generates candidate
> routes and throws away the ones that don't measure up.
>
> What I hadn't done was test that claim end to end, so I built an eval
> harness. 62 plain-English requests, expectations frozen before I ran
> anything, a third held out. The important design choice is that nothing is
> graded by a model: the harness re-parses the GPX with its own XML parser
> and recomputes distance by haversine, loop closure, closest approach to
> each waypoint, and how many meters you'd still ride on a road you asked to
> avoid. Tolerances come from the product's own thresholds, so I'm testing
> the promise rather than a band I picked to look good.
>
> It found four real defects. Three of them were the same defect: the program
> had already worked out the truth — the router was down, the lookup failed,
> the interval stretch needs two laps — and handed the user a sentence that
> didn't contain it. No assertion on a return value would have caught those.
>
> The most interesting part is what I didn't fix. The held-out set had a bug
> pointing the opposite way, and my prompt fix only moved it from a reliable
> failure to about three times in five. The real fix isn't a better prompt —
> the parser can't know whether the user already has a route — it's recording
> lineage so undo can recover. I designed it and left it unbuilt, because the
> only evidence for it is in the held-out set and spending that would have
> cost me the one clean measurement in the campaign.

## Five likely technical questions

**1. "Your eval scored your own system. How do you know the scorer isn't just
agreeing with the code?"**
Three answers. The measurements are re-implemented — haversine geodesy
against the app's flat-earth approximation, a real XML parser against the
app's regexes (the test suite has a malformed-GPX case the app's parser
accepts and the eval rejects). The scorer reads the GPX file on disk, not the
app's reported numbers, and the page shows both side by side. And where I
did borrow a threshold I borrowed it deliberately, from the product's own
rejection rules, so the eval tests the promise instead of a flattering band —
that's stated on the page. Where they genuinely can't be independent —
elevation comes from the same SRTM data the router returns — I say so instead
of claiming ground truth.

**2. "Three of your checks produced false failures. Why should I trust the
rest?"**
You should trust them more because of that, not less. All three corrections
happened before any product change, each is recorded on the page with what
was wrong, and each is pinned by a test that asserts *both* directions — the
legitimate geometry it must accept and the broken geometry it must still
reject, so loosening a check can't quietly turn it into a rubber stamp. The
last one is the interesting one: I couldn't distinguish a spliced track from
a Wisconsin grid jog geometrically, because they're the same shape. So the
check stopped guessing and asked the router whether a road connects the two
ends. It answered 805 m by road against 802 m straight — that's a
measurement, not a heuristic.

**3. "Why a held-out set for a 62-case eval? Isn't that overkill?"**
It's exactly the size where it matters, because with 62 cases you can
accidentally tune to all of them in an afternoon. It paid for itself twice.
It caught that my prompt fix generalized only partly — dev passed 5/5, the
held-out mirror case still fails 2 in 5 — which I'd have reported as a clean
fix otherwise. And it disciplined what I built: the right fix for the
remaining failure is evidenced only in held-out cases, so I designed it,
wrote down why, and left it unbuilt.

**4. "Your median distance error is 1.8%. Does that mean routes are good?"**
No, and the page says so. It means the geometry matches the number in the
sentence. It says nothing about whether the route is safe or pleasant —
traffic volume, shoulders, pavement, sightlines aren't in OSM tags at the
fidelity that would need, and no route in this campaign was ridden. The
project does have field evidence — 35 phases of riding routes and turning
complaints into regression tests, including an elevation model calibrated
against a barometric FIT file — but that's a different body of evidence and I
don't mix them.

**5. "What did you learn about evaluating LLM systems?"**
Two things. First, grade the artifact and the message, not the return value.
Three of four defects were cases where the program's internal state was
correct and the sentence shown to the user wasn't — invisible to any
assertion on a return value. Second, put the system prompt in the cache key.
I cached parses by model and text, changed the prompt, re-ran, and scored the
"fixed" build against stale parses — it silently reported the bug as still
present. That's the same class of mistake as evaluating against training
data, and it took a case that *shouldn't* have changed to expose it. The
harness now keys on the prompt, and scoring is a pure function of saved
records so every run can be re-graded under identical rules for free.
