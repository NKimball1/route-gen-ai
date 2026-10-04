"""Re-apply the scorers to saved case records, without re-running anything.

  python -m evals.rescore --run baseline

The runner saves each case's parse, its user-facing outcome, and the GPX it
produced (under evals/results/<run>/work/). Scoring is a pure function of
those, so calibrating a tolerance or fixing a check costs no API call and no
route generation -- and every run in the campaign can be re-scored under
identical rules, which is what makes before/after comparisons meaningful.

Each rescore writes the previous verdict into `verdict_history`, so the
record shows that a number moved and why.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
from typing import Any

from evals import paths, scorers
from evals.runner import resolve_expectations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "evals", "results")


def rescore_record(rec: dict[str, Any]) -> dict[str, Any]:
    case = {"id": rec["id"], "split": rec["split"], "category": rec["category"],
            "tier": rec["tier"], "scope": rec["scope"], "text": rec["text"],
            "start": rec["start"], "why": rec["why"], "expect": rec["expect"],
            "session": rec.get("session"), "step": rec.get("step"),
            "env": rec.get("env")}
    parsed = rec.get("parsed") or None
    if parsed is not None and not parsed:
        parsed = None
    result: dict[str, Any] | None = None
    if rec.get("error") is None:
        result = {"ok": rec.get("outcome_ok"), "summary": rec.get("summary"),
                  "warnings": rec.get("warnings", []),
                  "kind": rec.get("kind"), "log": rec.get("log_tail", ""),
                  "candidates": [{}] * (rec.get("n_candidates") or 0)}

    gpx = paths.resolve((rec.get("measured") or {}).get("gpx_path"))
    if gpx and not os.path.exists(gpx):
        gpx = None
    resolved = resolve_expectations(case) if gpx else {}
    measured = scorers.measure_route(gpx, case, resolved, None)
    if rec.get("measured", {}).get("app_reported_mi") is not None:
        measured.app_reported_mi = rec["measured"]["app_reported_mi"]

    exp_route = dict(case["expect"].get("route") or {})
    if exp_route.get("distance_delta_mi") is not None:
        exp_route["_before_mi"] = (rec.get("expect", {}).get("route", {})
                                   .get("_before_mi"))
    case = {**case, "expect": {**case["expect"], "route": exp_route}}

    shown = {"summary": rec.get("summary"),
             "candidate_labels": rec.get("candidate_labels") or []}
    checks: list[Any] = []
    scorer_error: str | None = None
    for fn in (lambda: scorers.score_outcome(case, result, parsed,
                                             rec.get("error")),
               lambda: scorers.score_parse(case, parsed),
               lambda: scorers.score_route(case, measured, parsed),
               lambda: scorers.score_spot(case, measured, parsed, resolved,
                                          shown)):
        try:
            checks += fn()
        except Exception as e:                       # noqa: BLE001 - recorded
            scorer_error = f"{type(e).__name__}: {e}"
            print(f"  HARNESS BUG while scoring {rec['id']}: {scorer_error}")
    verdict, reasons = scorers.verdict_for(checks)
    if scorer_error:
        verdict, reasons = "inconclusive", [f"scorer crashed: {scorer_error}"]
    if case["tier"] == "live" and verdict == "fail" and not case.get("env"):
        if "routing server unreachable" in (rec.get("log_tail") or "").lower():
            verdict, reasons = "inconclusive", [
                "routing server was not reachable during this run"]

    history = list(rec.get("verdict_history") or [])
    if rec["verdict"] != verdict:
        history.append({"was": rec["verdict"], "now": verdict,
                        "was_reasons": rec.get("fail_reasons"),
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    out = dict(rec)
    out.update({"verdict": verdict, "fail_reasons": reasons,
                "measured": {**measured.as_dict(),
                             "gpx_path": paths.rel(measured.gpx_path)},
                "checks": [c.as_dict() for c in checks],
                "verdict_history": history,
                "scorer_error": scorer_error,
                "rescored_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--ids", default=None)
    args = ap.parse_args()
    want = set(args.ids.split(",")) if args.ids else None
    moved = 0
    total = 0
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, args.run,
                                              "cases", "*.json"))):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        if want and rec["id"] not in want:
            continue
        total += 1
        new = rescore_record(rec)
        if new["verdict"] != rec["verdict"]:
            moved += 1
            print(f"{rec['id']}: {rec['verdict']} -> {new['verdict']} "
                  f"({', '.join(new['fail_reasons']) or 'clean'})")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=1)
    print(f"rescored {total} case(s); {moved} verdict(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
