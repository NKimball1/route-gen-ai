# Code and architecture review fixes — 2026-10-03

## Recovery point

Before edits, the entire working directory was archived to a private local
backup with a SHA-256 manifest (verified). It contains local configuration and
is deliberately not part of the repository. No Git reset or deployment was
performed.

## Architecture

The core remains natural language → validated intent → deterministic routing →
verified GPX. The work adds no agent framework, database, frontend framework, or
second execution system.

- `routes/service.py`: shared request boundary, context and explicit operation,
  runtime validation, visible limitations, dispatch to application services.
- `routes/edit_service.py`, `routes/spot_service.py`, `routes/pipeline.py`:
  reusable application workflows. CLI scripts are adapters.
- `routes/storage.py`: unique immutable artifact names, authoritative atomic
  `state.json`, parent history and session transactions. Existing `latest.txt`
  and lineage files migrate on use; `latest.txt` remains a compatibility mirror.
- `routes/execution.py`: cancellation and commit share one synchronization
  boundary. API reservations last until workers exit. Selection, upload and undo
  cannot race a running request.
- `routes/policy.py`, `routes/constraints.py`, `routes/geometry.py`: named product
  limits, resolved road/area constraints, segment-based geometric verification.
- `routes/gpx_in.py`: one namespace-aware reader; segments split at pauses or
  dropouts are joined, separate rides in one file are refused.
- `static/app.js`: browser controller separated from HTML, still vanilla JS.
- BRouter is the default supported workflow. ORS remains an explicit experimental
  CLI provider. Strava requires explicit personal CLI opt-in and cannot borrow
  another project's tokens or serve the host's account to web sessions.

## Findings addressed

| Finding | Result | Main regression evidence |
|---|---|---|
| Repeated generation/upload overwrites files and history | UUID artifacts, immutable parent records, atomic current/history commit | `test_review_storage.py`, `test_review_api.py`, live HTTP run |
| NL edits replace rides or invent missing context | Current length/home/operation supplied; clarify supported; explicit operation enforced | `test_review_service.py`, 24 live parser cases |
| Cancellation commits later or mutates alongside undo/upload/selection | Commit-aware token, session transaction and worker reservation | threaded storage/API tests, cancellation after preview |
| Interval export cuts corners | Export original routed vertices; scoring resampling stays internal | bend-heavy geometry regression, live GPX measurement |
| Partial edits reported as success | Missing return, off-target distance, skipped/unmet vias and bounded inputs disclosed as partial | service/geometry regressions |
| Failed correction shows a different route than current | Correction is staged from predecessor; failure preserves actual selection/display | failed-correction regression |
| Home cannot resolve in edits | Shared configured home resolution for single/multiple places | service regression |
| Vias unordered or accepted kilometers away | Preserve requested order; segment-based validation sized to the place (150 m for a point, up to 3 km for a town) | geometry/scoring regressions, live multi-via edit |
| Initial road avoidance is only a circle | Resolve real OSM ways and verify on-road meters; absent geometry refuses; areas verified separately | constraint/geometry tests |
| Outback becomes a loop through vias/climb scouting | Shape is a hard filter; explicit outback preserved | shape tests, live max-climb outback |
| Unknown stops shown as zero | Null metrics and visible unknown; hard stop caps need known controls | geometry/service regressions |
| GPX namespaces/segments flattened incorrectly | Namespace-aware XML; track preferred over a duplicate route; pause/dropout gaps up to 2 km join, separate rides and entities refused | reader/API tests |
| CLI-only interval controls and inconsistent summaries | Shared naming, watts/mass, any-direction, stop cap, lap and warning descriptions | service regression, live powered interval |
| Reversed climb travel measured at wrong end | Travel budget applied to the actual ridden start | reverse-incline regression |
| ORS metadata understated / misleading fallback | Compute overlap; major roads remain unknown; explicit optional provider, BRouter-only web policy | metadata regression and provider tests |
| Rejected requests drain global quota | Validate every quota before debiting any | API/quota tests |
| Unowned GPX/job reads and spoofable forwarded IP | Session ownership/invite required on reads and writes; proxy handling delegated to server configuration | API isolation/auth/traversal/IP tests |
| Polling never ends; start mandatory despite explicit address | Bounded polling and error exit; explicit-address requests work without saved start; reload restores geometry | frontend VM tests |
| Evaluations accept undisclosed/unknown results | Warnings persisted, user-visible honesty checked, unknown hard checks inconclusive; parse cache includes context/schema | evaluation regressions |

## Follow-up corrections (same day, before commit)

A second review found five regressions in the first pass; each now has a test
that fails on the first-pass code.

- **"Through a town" loops were rejected.** The 150 m via check applied to
  towns, so natural loops sweeping through Verona failed for missing its
  geocoded center. Via tolerance now comes from the place's Nominatim bounding
  box: half its narrowest width, between 150 m and 3 km. Edit waypoints keep 150 m.
- **Device recordings with pauses were refused.** Segments are joined across
  gaps up to 2 km; larger jumps are refused as separate rides.
- **CLIs ignored `.env` for import-time settings** (`ROUTEGEN_TOTAL_KG`, the
  Overpass cache directory). `load_dotenv()` runs before the route imports again.
- **Avoiding a road the route only crosses** reported a failed detour. A
  crossing (under 60 m on the road) is now reported as nothing to avoid.
- **A damaged `state.json` locked the session.** It is set aside as
  `state.json.damaged-*`; the current route is kept from the `latest.txt`
  mirror and undo history starts over.

## Verification

- Offline Python suite: 251 passed; 24 paid live parser tests skipped by default.
- Live parser suite, separately enabled: 24 passed.
- Frontend controller: 3 Node tests passed (minimal DOM/network adapter).
- Mypy: 43 source files clean; application pyflakes runs inside pytest.
- Real Claude + local BRouter through FastAPI TestClient: new loop, repeated
  generation with unchanged earlier bytes, undo, total-distance edit, multi-via
  edit, max-climb outback, powered interval search and GPX downloads passed.
  Evidence: `output/review-validation/20261003-141953/report.json` and its GPX files.
  The final pass (`output/review-validation/20261003-143727/report.json`)
  repeated those checks and independently measured the exported distances.
  Its additional named-road generation check could not produce a route:
  Overpass returned HTTP 504, then the fallback host timed out. The app
  explicitly refused the unverifiable constraint. That run records one
  failed live expectation; successful live road-avoidance remains unverified.
  Deterministic tests cover road geometry, mixed area/road exclusions, and
  refusal when geometry is missing.
- One external Starlette/AnyIO deprecation warning remains in the test harness.

This is not a claim of exhaustive route coverage. The live checks use public
Madison-area landmarks. No real-browser visual automation or new Garmin field
ride was performed. Saved evaluation portfolio reports were not regenerated;
they remain historical evidence under their original scoring rules.

## Remaining deliberate limits

Run one API worker. Jobs, rate counters and locks are process-local; independent
CLI processes must not share a mutable session concurrently. Browser session IDs
are bearer credentials, not user accounts. Restart loses active jobs but preserves
committed GPX/history. A cancelled request may leave an unselected unique artifact;
it cannot replace the committed current route. Disk cleanup/retention is manual.

OSM control/road data and elevations can be missing or inaccurate; unknowns are
shown and hard constraints that cannot be verified are refused. Routing does not
certify real-world access, weather, closures or safety. Broader accounts,
distributed jobs and more providers should be separate product decisions, not
prerequisites for the prompt-to-GPX workflow.
