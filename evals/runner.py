"""Run the Route Gen AI evaluation campaign.

  python -m evals.runner --run baseline --tier live
  python -m evals.runner --run baseline --tier offline
  python -m evals.runner --run baseline --ids D14,D15,H09      # re-run a few
  python -m evals.runner --run postfix --split heldout
  python -m evals.runner --run variance --repeats 3 --no-parse-cache --parse-only

Design notes:

* It drives the REAL product entry point, routes.service.handle_request --
  the same function the web app and the CLI call. Nothing is reimplemented,
  so a pass here is a pass for the shipped code path.
* RESUMABLE. Every case writes evals/results/<run>/cases/<id>.json the moment
  it finishes. Re-running skips finished cases unless --force.
* The parse (the only paid call) is CACHED by (model, text) in
  evals/cache/parses.json. A re-run after a routing-side fix therefore costs
  $0, and the interpretation being scored is byte-identical to the one the
  earlier run scored.
* Geocoding for the SCORER's own expectations is cached too, and is separate
  from whatever the app geocodes internally.
* Sessions: cases sharing a "session" key run in order in one workspace, so
  edit chains behave exactly as they do for a user in one browser tab.
* Provider limits: a small delay between Nominatim lookups, and a persistent
  failure is recorded once rather than retried forever.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
import traceback
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Never let a private home address reach an eval result. Every case names a
# public start; this makes a case that says "home" fail loudly instead.
os.environ["ROUTEGEN_HOME_ADDRESS"] = "Wisconsin State Capitol, Madison WI"

from evals import paths, scorers
from evals.scorers import Check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(ROOT, "evals")
CASES_FILE = os.path.join(EVAL_DIR, "cases_v1.json")
CACHE_DIR = os.path.join(EVAL_DIR, "cache")
RESULTS_DIR = os.path.join(EVAL_DIR, "results")

# claude-haiku-4-5 list price, 2026-09.
PRICE_IN_PER_MTOK = 1.00
PRICE_OUT_PER_MTOK = 5.00

NOMINATIM_DELAY_S = 1.1   # the public endpoint's stated policy is 1 req/s


# --------------------------------------------------------------------------
# caches
# --------------------------------------------------------------------------

class JsonCache:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                self.data: dict[str, Any] = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)


PARSE_CACHE = JsonCache(os.path.join(CACHE_DIR, "parses.json"))
GEO_CACHE = JsonCache(os.path.join(CACHE_DIR, "geocodes.json"))
ROAD_CACHE = JsonCache(os.path.join(CACHE_DIR, "roads.json"))


def _parse_key(text: str, model: str, context: dict[str, Any] | None = None) -> str:
    """Cache identity for one parse.

    The SYSTEM PROMPT is part of the key. Without it, editing the prompt
    and re-running would silently score the new build against the OLD
    parses -- the exact mistake that would hide whether a prompt fix
    worked. (It did hide it once, for one run, which is why this is
    spelled out.) A routing-side fix still re-runs for free, against a
    byte-identical interpretation.
    """
    import routes.nl
    prompt_id = hashlib.sha256(routes.nl.SYSTEM.encode()).hexdigest()[:12]
    payload = model + chr(0) + prompt_id + chr(0) + text + chr(0) + json.dumps(context or {}, sort_keys=True) + chr(0) + json.dumps(routes.nl.SCHEMA, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


class ParseMeter:
    """Wraps routes.nl.parse_request: caches, counts tokens, prices the call."""

    def __init__(self, use_cache: bool = True) -> None:
        self.use_cache = use_cache
        self.calls_live = 0
        self.calls_cached = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.last: dict[str, Any] | None = None

    @property
    def cost_usd(self) -> float:
        return round(self.input_tokens / 1e6 * PRICE_IN_PER_MTOK
                     + self.output_tokens / 1e6 * PRICE_OUT_PER_MTOK, 6)

    def install(self) -> Callable[[str], dict[str, Any]]:
        import routes.nl as nl
        real = nl.parse_request
        model = os.environ.get("ROUTEGEN_MODEL", nl.DEFAULT_MODEL)

        def wrapper(text: str, client: Any = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
            key = _parse_key(text, model, context)
            if self.use_cache:
                hit = PARSE_CACHE.get(key)
                if hit is not None:
                    self.calls_cached += 1
                    out = json.loads(json.dumps(hit))
                    out["_usage"] = dict(out.get("_usage") or {})
                    out["_usage"]["cached"] = True
                    self.last = json.loads(json.dumps(out))
                    return out
            parsed = real(text, client, context=context)
            self.calls_live += 1
            usage = parsed.get("_usage") or {}
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
            PARSE_CACHE.put(key, json.loads(json.dumps(parsed)))
            self.last = json.loads(json.dumps(parsed))
            return parsed

        nl.parse_request = wrapper       # type: ignore[assignment]
        return real                       # type: ignore[return-value]


# --------------------------------------------------------------------------
# geocoding for the SCORER's own expectations (independent of the app's)
# --------------------------------------------------------------------------

_last_geo_call = [0.0]


def eval_geocode(place: str) -> tuple[float, float] | None:
    hit = GEO_CACHE.get(place)
    if hit is not None:
        return None if hit == "MISS" else (hit[0], hit[1])
    gap = time.time() - _last_geo_call[0]
    if gap < NOMINATIM_DELAY_S:
        time.sleep(NOMINATIM_DELAY_S - gap)
    _last_geo_call[0] = time.time()
    try:
        from routes.geocode import geocode
        lat, lon, _name = geocode(place)
    except Exception:
        GEO_CACHE.put(place, "MISS")      # do not retry a persistent failure
        return None
    GEO_CACHE.put(place, [lat, lon])
    return lat, lon


def eval_road_ways(name: str, lat: float, lon: float,
                   radius_m: float = 12000.0) -> list[list[list[float]]]:
    """OSM centerlines for a named road, cached. Used to measure how many
    meters of a route were actually ridden on a road the user excluded."""
    key = f"{name}|{lat:.3f},{lon:.3f}|{radius_m:.0f}"
    hit = ROAD_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        from routes.road_avoid import fetch_road
        ways = [[[p[0], p[1]] for p in w]
                for w in fetch_road(name, lat, lon, radius_m)]
    except Exception:
        ways = []
    ROAD_CACHE.put(key, ways)
    return ways


def resolve_expectations(case: dict[str, Any]) -> dict[str, Any]:
    """Geocode everything the SCORER needs, independently of the app."""
    exp = case["expect"].get("route") or {}
    out: dict[str, Any] = {"via_latlon": {}, "avoid_ways": {}}
    start = eval_geocode(case["start"])
    if start:
        out["start_latlon"] = start
    for place in exp.get("via") or []:
        ll = eval_geocode(place)
        if ll:
            out["via_latlon"][place] = ll
    if exp.get("anchor"):
        ll = eval_geocode(exp["anchor"])
        if ll:
            out["start_latlon"] = ll
    if exp.get("end_at"):
        ll = eval_geocode(exp["end_at"])
        if ll:
            out["end_latlon"] = ll
    for place in exp.get("avoid") or []:
        anchor = out.get("start_latlon")
        ll = eval_geocode(place)
        centre = ll or anchor
        if centre:
            ways = eval_road_ways(place.split(",")[0], centre[0], centre[1])
            if ways:
                out["avoid_ways"][place] = [[(p[0], p[1]) for p in w]
                                            for w in ways]
    return out


# --------------------------------------------------------------------------
# running one case
# --------------------------------------------------------------------------

def _break_geocoder() -> Callable[[], None]:
    """Point Nominatim at a closed port, with one attempt, so the failure
    path runs fast. Returns the undo."""
    import routes.geocode as g
    old_url, old_retries = g.NOMINATIM_URL, g.RETRIES
    g.NOMINATIM_URL = "http://127.0.0.1:9/search"
    g.RETRIES = 1

    def undo() -> None:
        g.NOMINATIM_URL, g.RETRIES = old_url, old_retries
    return undo


def run_case(case: dict[str, Any], workdir: str, meter: ParseMeter,
             session_state: dict[str, Any]) -> dict[str, Any]:
    from routes.service import handle_request

    env_undo: list[tuple[str, str | None]] = []
    geo_undo: Callable[[], None] | None = None
    for k, v in (case.get("env") or {}).items():
        if k == "ROUTEGEN_EVAL_BREAK_GEOCODER":
            geo_undo = _break_geocoder()
            continue
        env_undo.append((k, os.environ.get(k)))
        os.environ[k] = v

    meter.last = None
    sink = io.StringIO()
    t0 = time.time()
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        result = dict(handle_request(case["text"], log_sink=sink,
                                     default_address=case["start"],
                                     workdir=workdir))
    except BaseException as e:                       # noqa: BLE001 - recorded
        error = f"{type(e).__name__}: {e}"
        trace = traceback.format_exc()
    else:
        trace = ""
    seconds = round(time.time() - t0, 2)
    for k, old in env_undo:
        if old is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = old
    if geo_undo:
        geo_undo()

    log = sink.getvalue()
    if result is not None:
        result["log"] = log
    parsed = meter.last

    # ---- what artifact came back, if any ----
    gpx_path: str | None = None
    app_label: str | None = None
    cands = (result or {}).get("candidates") or []
    if cands:
        gpx_path = cands[0].get("gpx")
        app_label = cands[0].get("label")
        if gpx_path and not os.path.exists(gpx_path):
            gpx_path = None

    resolved = resolve_expectations(case) if gpx_path else {}
    measured = scorers.measure_route(gpx_path, case, resolved, app_label)

    # edits that promise a length CHANGE need the previous step's length
    exp_route = dict(case["expect"].get("route") or {})
    if exp_route.get("distance_delta_mi") is not None:
        exp_route["_before_mi"] = session_state.get("last_distance_mi")
    case = {**case, "expect": {**case["expect"], "route": exp_route}}

    # A bug in a CHECK must not destroy the run. One did: a renamed dict key
    # raised KeyError mid-campaign and took 37 unfinished cases with it.
    # A scorer that crashes makes its case inconclusive, not the campaign.
    shown = {"summary": (result or {}).get("summary"),
             "candidate_labels": [c.get("label", "") for c in cands]}
    checks: list[Check] = []
    scorer_error: str | None = None
    for fn in (lambda: scorers.score_outcome(case, result, parsed, error),
               lambda: scorers.score_parse(case, parsed),
               lambda: scorers.score_route(case, measured, parsed),
               lambda: scorers.score_spot(case, measured, parsed, resolved,
                                          shown)):
        try:
            checks += fn()
        except Exception as e:                       # noqa: BLE001 - recorded
            scorer_error = f"{type(e).__name__}: {e}"
            print(f"  HARNESS BUG while scoring: {scorer_error}")
    verdict, reasons = scorers.verdict_for(checks)
    if scorer_error:
        verdict, reasons = "inconclusive", [f"scorer crashed: {scorer_error}"]

    # A live-tier case whose failure is an environment problem, not a product
    # problem, is INCONCLUSIVE -- it must not count as evidence either way.
    if case.get("tier") == "live" and verdict == "fail":
        blob = (log + " " + (error or "")).lower()
        if "routing server unreachable" in blob and not case.get("env"):
            verdict = "inconclusive"
            reasons = ["routing server was not reachable during this run"]

    if measured.distance_mi:
        session_state["last_distance_mi"] = measured.distance_mi

    usage = (parsed or {}).get("_usage") or {}
    return {
        "id": case["id"], "split": case["split"], "category": case["category"],
        "tier": case["tier"], "scope": case["scope"],
        "session": case.get("session"), "step": case.get("step"),
        "text": case["text"], "start": case["start"], "why": case["why"],
        "expect": case["expect"],
        "verdict": verdict, "fail_reasons": reasons,
        "parsed": {k: v for k, v in (parsed or {}).items() if k != "_usage"},
        "notes": (parsed or {}).get("notes", ""),
        "outcome_ok": (result or {}).get("ok"),
        "summary": (result or {}).get("summary"),
        "warnings": (result or {}).get("warnings", []),
        "kind": (result or {}).get("kind"),
        "n_candidates": len(cands),
        "candidate_labels": [c.get("label", "") for c in cands],
        "error": paths.scrub(error),
        "traceback": paths.scrub(trace[-2000:]) if error else "",
        "seconds": seconds,
        "scorer_error": scorer_error,
        "parse_cached": bool(usage.get("cached")),
        "parse_tokens": {"input": usage.get("input_tokens"),
                         "output": usage.get("output_tokens")},
        "measured": {**measured.as_dict(),
                     "gpx_path": paths.rel(measured.gpx_path)},
        "checks": [c.as_dict() for c in checks],
        "log_tail": paths.scrub(log[-4000:]),
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_cases() -> dict[str, Any]:
    with open(CASES_FILE, encoding="utf-8") as f:
        return json.load(f)


def select(cases: list[dict[str, Any]], args: argparse.Namespace
           ) -> list[dict[str, Any]]:
    out = cases
    if args.tier != "all":
        out = [c for c in out if c["tier"] == args.tier]
    if args.split != "all":
        out = [c for c in out if c["split"] == args.split]
    if args.category:
        want = set(args.category.split(","))
        out = [c for c in out if c["category"] in want]
    if args.ids:
        want = set(args.ids.split(","))
        out = [c for c in out if c["id"] in want]
    # sessions must run in step order, and a session's steps must stay together
    out.sort(key=lambda c: (c.get("session") or "", c.get("step") or 0,
                            c["id"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True,
                    help="run name, e.g. baseline / postfix / pilot")
    ap.add_argument("--tier", default="all",
                    choices=["all", "live", "offline"])
    ap.add_argument("--split", default="all",
                    choices=["all", "dev", "heldout"])
    ap.add_argument("--category", default=None, help="comma-separated")
    ap.add_argument("--ids", default=None, help="comma-separated case ids")
    ap.add_argument("--force", action="store_true",
                    help="re-run cases that already have a saved result")
    ap.add_argument("--no-parse-cache", action="store_true",
                    help="always call the model (for variability measurement)")
    ap.add_argument("--parse-only", action="store_true",
                    help="parse and score interpretation only; no routing")
    ap.add_argument("--repeats", type=int, default=1,
                    help="run each selected case N times (variability)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    spec = load_cases()
    cases = select(spec["cases"], args)
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        print("no cases selected")
        return 1

    run_dir = os.path.join(RESULTS_DIR, args.run)
    case_dir = os.path.join(run_dir, "cases")
    work_root = os.path.join(run_dir, "work")
    os.makedirs(case_dir, exist_ok=True)

    meter = ParseMeter(use_cache=not args.no_parse_cache)
    meter.install()

    if args.parse_only:
        return _parse_only(cases, run_dir, meter, args)

    # preflight: the live tier needs a reachable router
    if any(c["tier"] == "live" for c in cases):
        from routes.providers import BRouterProvider, brouter_reachable
        url = BRouterProvider().base_url
        if not brouter_reachable(url):
            print(f"PREFLIGHT FAIL: BRouter not reachable at {url}. "
                  f"Start it before running the live tier.")
            return 2
        print(f"preflight ok: BRouter reachable at {url}")

    session_states: dict[str, dict[str, Any]] = {}
    t_start = time.time()
    done = 0
    for rep in range(args.repeats):
        for case in cases:
            suffix = f".r{rep + 1}" if args.repeats > 1 else ""
            out_path = os.path.join(case_dir, f"{case['id']}{suffix}.json")
            session = case.get("session")
            if os.path.exists(out_path) and not args.force:
                try:
                    with open(out_path, encoding="utf-8") as f:
                        prior = json.load(f)
                    if session:
                        st = session_states.setdefault(session, {})
                        d = (prior.get("measured") or {}).get("distance_mi")
                        if d:
                            st["last_distance_mi"] = d
                    print(f"[skip] {case['id']} ({prior.get('verdict')})")
                    continue
                except (OSError, ValueError):
                    pass

            if session:
                workdir = os.path.join(work_root, f"{session}{suffix}")
                state = session_states.setdefault(session + suffix, {})
                if case.get("step") == 1 and os.path.isdir(workdir):
                    shutil.rmtree(workdir, ignore_errors=True)
                    state.clear()
            else:
                workdir = os.path.join(work_root, case["id"] + suffix)
                state = {}
                shutil.rmtree(workdir, ignore_errors=True)
            os.makedirs(workdir, exist_ok=True)

            print(f"[run ] {case['id']} {case['category']:<17} "
                  f"{case['text'][:62]!r}")
            rec = run_case(case, workdir, meter, state)
            rec["run"] = args.run
            rec["repeat"] = rep + 1
            rec["cases_version"] = spec["version"]
            rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=1)
            done += 1
            flag = {"pass": "PASS", "partial": "PART", "fail": "FAIL",
                    "inconclusive": "INCO"}[rec["verdict"]]
            print(f"       -> {flag} in {rec['seconds']}s"
                  + (f"  [{', '.join(rec['fail_reasons'][:3])}]"
                     if rec["fail_reasons"] else ""))

    meta = {
        "run": args.run, "cases_version": spec["version"],
        "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "selection": {"tier": args.tier, "split": args.split,
                      "category": args.category, "ids": args.ids,
                      "repeats": args.repeats},
        "cases_executed_this_invocation": done,
        "wall_seconds": round(time.time() - t_start, 1),
        "parse_calls_live": meter.calls_live,
        "parse_calls_cached": meter.calls_cached,
        "parse_input_tokens": meter.input_tokens,
        "parse_output_tokens": meter.output_tokens,
        "parse_cost_usd": meter.cost_usd,
        "model": os.environ.get("ROUTEGEN_MODEL", "claude-haiku-4-5"),
        "price_per_mtok": {"input": PRICE_IN_PER_MTOK,
                           "output": PRICE_OUT_PER_MTOK},
    }
    meta_path = os.path.join(run_dir, f"meta.{int(time.time())}.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print(f"\n{done} case(s) in {meta['wall_seconds']}s | "
          f"live parses {meter.calls_live} (cached {meter.calls_cached}) | "
          f"model spend ${meter.cost_usd:.4f}")
    print(f"meta -> {meta_path}")
    return 0


def _parse_only(cases: list[dict[str, Any]], run_dir: str,
                meter: ParseMeter, args: argparse.Namespace) -> int:
    """Interpretation layer only -- for variability measurement."""
    from routes.nl import parse_request
    out_path = os.path.join(run_dir, "parse_variance.json")
    os.makedirs(run_dir, exist_ok=True)
    records: list[dict[str, Any]] = []
    for rep in range(args.repeats):
        for case in cases:
            parsed = parse_request(case["text"])
            checks = scorers.score_parse(case, parsed)
            verdict, reasons = scorers.verdict_for(checks)
            records.append({
                "id": case["id"], "repeat": rep + 1, "verdict": verdict,
                "fail_reasons": reasons,
                "parsed": {k: v for k, v in parsed.items() if k != "_usage"},
                "checks": [c.as_dict() for c in checks],
            })
            print(f"[parse] {case['id']} r{rep + 1}: {verdict}")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"records": records, "repeats": args.repeats,
                   "parse_calls_live": meter.calls_live,
                   "parse_cost_usd": meter.cost_usd,
                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")},
                  f, indent=1)
    print(f"\n-> {out_path} | live parses {meter.calls_live} | "
          f"${meter.cost_usd:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
