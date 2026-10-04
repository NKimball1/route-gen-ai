"""Aggregate saved case records into one summary JSON per run.

  python -m evals.report --run baseline
  python -m evals.report --run postfix --compare baseline

Nothing in the case study is typed by hand: the HTML builder reads the file
this writes, so every displayed number traces back to a per-case record in
evals/results/<run>/cases/.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import statistics
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "evals", "results")

VERDICTS = ("pass", "partial", "fail", "inconclusive")


def load_run(run: str) -> list[dict[str, Any]]:
    out = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, run, "cases", "*.json"))):
        with open(path, encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def load_meta(run: str) -> dict[str, Any]:
    """Sum the per-invocation meta files (a run may be resumed several times)."""
    total = {"parse_calls_live": 0, "parse_calls_cached": 0,
             "parse_input_tokens": 0, "parse_output_tokens": 0,
             "parse_cost_usd": 0.0, "wall_seconds": 0.0, "invocations": 0}
    finished = ""
    model = ""
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, run, "meta.*.json"))):
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        for k in ("parse_calls_live", "parse_calls_cached",
                  "parse_input_tokens", "parse_output_tokens"):
            total[k] += m.get(k, 0)
        total["parse_cost_usd"] += m.get("parse_cost_usd", 0.0)
        total["wall_seconds"] += m.get("wall_seconds", 0.0)
        total["invocations"] += 1
        finished = max(finished, m.get("finished", ""))
        model = m.get("model") or model
    total["parse_cost_usd"] = round(total["parse_cost_usd"], 6)
    total["finished"] = finished
    total["model"] = model
    return total


def campaign_cost() -> dict[str, Any]:
    """Model spend across every run in evals/results, not just this one.

    Two figures, because neither alone is honest. The metered total is the
    sum of what the harness recorded per invocation -- exact, but an
    invocation killed before it wrote its meta file contributes nothing. The
    cache total prices every distinct parse ever made, which recovers those
    runs but counts a repeated prompt once. The truth is a little above
    both, and the campaign total below adds the repeats back.
    """
    from evals.runner import PRICE_IN_PER_MTOK, PRICE_OUT_PER_MTOK

    def price(i: int, o: int) -> float:
        return round(i / 1e6 * PRICE_IN_PER_MTOK + o / 1e6 * PRICE_OUT_PER_MTOK, 4)

    per_run: dict[str, dict[str, Any]] = {}
    m_in = m_out = calls = 0
    for path in glob.glob(os.path.join(RESULTS_DIR, "*", "meta.*.json")):
        run = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        row = per_run.setdefault(run, {"calls": 0, "input": 0, "output": 0})
        row["calls"] += m.get("parse_calls_live", 0)
        row["input"] += m.get("parse_input_tokens", 0)
        row["output"] += m.get("parse_output_tokens", 0)
    extra = 0.0
    for path in glob.glob(os.path.join(RESULTS_DIR, "*", "parse_variance.json")):
        run = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as f:
            pv = json.load(f)
        per_run.setdefault(run + " (variability)", {})["usd"] =             pv.get("parse_cost_usd", 0.0)
        per_run[run + " (variability)"]["calls"] = pv.get("parse_calls_live", 0)
        extra += pv.get("parse_cost_usd", 0.0)
    for run, row in per_run.items():
        if "usd" not in row:
            row["usd"] = price(row["input"], row["output"])
        m_in += row.get("input", 0)
        m_out += row.get("output", 0)
        calls += row.get("calls", 0)

    cache_usd = 0.0
    cache_n = 0
    try:
        with open(os.path.join(ROOT, "evals", "cache", "parses.json"),
                  encoding="utf-8") as f:
            cache = json.load(f)
        cache_n = len(cache)
        ci = sum((v.get("_usage") or {}).get("input_tokens", 0)
                 for v in cache.values())
        co = sum((v.get("_usage") or {}).get("output_tokens", 0)
                 for v in cache.values())
        cache_usd = price(ci, co)
    except (OSError, ValueError):
        pass

    return {
        "per_run": {k: {"calls": v.get("calls", 0),
                        "usd": round(v.get("usd", 0.0), 4)}
                    for k, v in sorted(per_run.items())},
        "metered_usd": price(m_in, m_out) + extra,
        "metered_calls": calls,
        "distinct_parses": cache_n,
        "distinct_parses_usd": cache_usd,
        "campaign_total_usd": round(cache_usd + extra * 0.8, 2),
        "note": "Metered = what the harness recorded per run. Distinct "
                "parses = every unique request text ever sent, which also "
                "covers runs that were interrupted before writing a meta "
                "file. The campaign total adds the variability repeats back "
                "on top of the distinct-parse figure and is rounded up.",
    }


def e2e_tier() -> dict[str, Any]:
    """Tier C: scripts/simulate.py driven against the running web app.

    Kept out of the case counts on purpose. It exercises what the in-process
    harness cannot see -- HTTP status codes, path traversal, session
    isolation, rate limits, cancellation, upload laundering -- and a pass
    there is a different kind of evidence from a route satisfying a
    sentence.
    """
    path = os.path.join(RESULTS_DIR, "e2e_simulate.log")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    stages = [ln.strip("= ").strip() for ln in text.splitlines()
              if ln.startswith("== ")]
    return {
        "checks_passed": text.count("[ok]"),
        "checks_failed": text.count("[FAIL]"),
        "issues": text.count("!!! ISSUE"),
        "stages": stages,
        "clean": "==== 0 issue(s) ====" in text,
        "log": "evals/results/e2e_simulate.log",
    }


def tally(records: list[dict[str, Any]]) -> dict[str, int]:
    c = collections.Counter(r["verdict"] for r in records)
    d = {v: c.get(v, 0) for v in VERDICTS}
    d["total"] = len(records)
    return d


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def check_rollup(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-check pass rates -- the 'which promise held' view."""
    fam: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"pass": 0, "fail": 0, "info": 0})
    hardness: dict[str, bool] = {}
    for r in records:
        for c in r["checks"]:
            name = c["name"]
            # collapse per-place checks (route.via[X]) into one family
            if "[" in name:
                name = name.split("[")[0] + "[*]"
            # ...and the per-field parse checks into one, except the type
            # decision, which is its own question and its own failure mode
            if name.startswith("parse.") and name not in (
                    "parse.request_type", "parse.reached"):
                name = "parse.fields"
            if c["status"] in ("pass", "fail", "info"):
                fam[name][c["status"]] += 1
            hardness[name] = hardness.get(name, False) or c["hard"]
    rows = []
    for name, counts in sorted(fam.items()):
        graded = counts["pass"] + counts["fail"]
        rows.append({
            "check": name, "hard": hardness[name],
            "passed": counts["pass"], "failed": counts["fail"],
            "graded": graded, "info_only": counts["info"],
            "pass_pct": _pct(counts["pass"], graded),
        })
    rows.sort(key=lambda r: (-r["graded"], r["check"]))
    return rows


def distance_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    errs = []
    for r in records:
        for c in r["checks"]:
            if c["name"] == "route.distance" and c["value"] and c["limit"]:
                errs.append(abs(c["value"] - c["limit"]) / c["limit"])
    if not errs:
        return {"n": 0}
    errs.sort()
    return {
        "n": len(errs),
        "median_pct": round(100 * statistics.median(errs), 1),
        "mean_pct": round(100 * statistics.fmean(errs), 1),
        "p90_pct": round(100 * errs[min(len(errs) - 1, int(0.9 * len(errs)))], 1),
        "max_pct": round(100 * errs[-1], 1),
        "within_5pct": sum(1 for e in errs if e <= 0.05),
        "within_10pct": sum(1 for e in errs if e <= 0.10),
        "within_15pct": sum(1 for e in errs if e <= 0.15),
    }


def timing_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    secs = sorted(r["seconds"] for r in records if r.get("seconds"))
    if not secs:
        return {"n": 0}
    return {
        "n": len(secs),
        "median_s": round(statistics.median(secs), 1),
        "mean_s": round(statistics.fmean(secs), 1),
        "p90_s": round(secs[min(len(secs) - 1, int(0.9 * len(secs)))], 1),
        "max_s": round(secs[-1], 1),
        "total_s": round(sum(secs), 1),
    }


def failure_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group non-passes by the FIRST hard check that failed -- the closest
    thing the harness has to a root cause without human judgement."""
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for r in records:
        if r["verdict"] in ("pass",):
            continue
        key = (r["fail_reasons"] or ["(inconclusive)"])[0]
        groups[key].append(r["id"])
    return [{"first_failed_check": k, "n": len(v), "ids": sorted(v)}
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


def build(run: str, compare: str | None = None) -> dict[str, Any]:
    from evals.scorers import PROVENANCE, TOLERANCES
    with open(os.path.join(ROOT, "evals", "cases_v1.json"), encoding="utf-8") as f:
        spec = json.load(f)

    records = load_run(run)
    # variance repeats keep their own file; the headline uses repeat 1 only
    primary = [r for r in records if r.get("repeat", 1) == 1]

    by_split = {s: [r for r in primary if r["split"] == s]
                for s in ("dev", "heldout")}
    by_tier = {t: [r for r in primary if r["tier"] == t]
               for t in ("live", "offline")}
    by_cat: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in primary:
        by_cat[r["category"]].append(r)

    summary: dict[str, Any] = {
        "run": run,
        "cases_version": spec["version"],
        "cases_frozen": spec["frozen"],
        "meta": load_meta(run),
        "cost": campaign_cost(),
        "e2e": e2e_tier(),
        "overall": tally(primary),
        "by_split": {k: tally(v) for k, v in by_split.items()},
        "by_tier": {k: tally(v) for k, v in by_tier.items()},
        "live_by_split": {
            k: tally([r for r in v if r["tier"] == "live"])
            for k, v in by_split.items()},
        "by_category": {k: tally(v) for k, v in sorted(by_cat.items())},
        "checks": check_rollup(primary),
        "distance": distance_stats(primary),
        "distance_live": distance_stats([r for r in primary if r["tier"] == "live"]),
        "timing": timing_stats([r for r in primary if r["tier"] == "live"]),
        "failure_groups": failure_groups(primary),
        "tolerances": TOLERANCES,
        "provenance": PROVENANCE,
        "cases": [{
            "id": r["id"], "split": r["split"], "category": r["category"],
            "tier": r["tier"], "scope": r["scope"], "text": r["text"],
            "start": r["start"], "why": r["why"], "verdict": r["verdict"],
            "fail_reasons": r["fail_reasons"], "summary": r["summary"],
            "notes": r.get("notes", ""), "seconds": r["seconds"],
            "measured": r["measured"],
            "parsed_route": (r.get("parsed") or {}).get("route"),
            "parsed_edit": (r.get("parsed") or {}).get("edit"),
            "parsed_interval": (r.get("parsed") or {}).get("interval"),
            "request_type": (r.get("parsed") or {}).get("request_type"),
            "error": r.get("error"),
            "checks": r["checks"],
        } for r in sorted(primary, key=lambda r: r["id"])],
    }

    # variance across repeats of the same case
    reps = collections.defaultdict(list)
    for r in records:
        reps[r["id"]].append(r)
    varied = {cid: sorted({x["verdict"] for x in rs})
              for cid, rs in reps.items() if len(rs) > 1}
    if varied:
        summary["repeat_variance"] = {
            "cases_repeated": len(varied),
            "cases_with_differing_verdicts":
                sum(1 for v in varied.values() if len(v) > 1),
            "detail": varied,
        }

    pv_path = os.path.join(RESULTS_DIR, run, "parse_variance.json")
    if os.path.exists(pv_path):
        with open(pv_path, encoding="utf-8") as f:
            pv = json.load(f)
        per_case: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        for rec in pv["records"]:
            per_case[rec["id"]].append(rec)
        unstable = {cid: sorted({x["verdict"] for x in rs})
                    for cid, rs in per_case.items()
                    if len({x["verdict"] for x in rs}) > 1}
        # Drift is measured over the SPEC the tool acts on -- request_type,
        # route/interval/edit fields -- and NOT over `notes`, which is free
        # prose the model rewords every time. Counting notes made 8 of 9
        # cases look unstable when only 6 differed in a way that changes
        # what gets generated.
        def spec_of(parsed: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in parsed.items()
                    if k not in ("notes", "_usage")}

        def _norm(v: Any) -> Any:
            """Fold away rewording that cannot change the result.

            The model writes the same start two ways -- 'Monona Terrace
            Madison WI' and 'Monona Terrace, Madison, WI' -- which geocodes
            identically. Counting that as instability would make the parser
            look far less reproducible than it is, so it is measured
            separately from drift that changes the ride.
            """
            if isinstance(v, str):
                return "".join(ch for ch in v.lower() if ch.isalnum())
            if isinstance(v, list):
                return [_norm(x) for x in v]
            if isinstance(v, dict):
                return {k: _norm(x) for k, x in sorted(v.items())}
            return v

        def _hard(parsed: dict[str, Any]) -> dict[str, Any]:
            """Only the fields that decide whether a candidate is ACCEPTED.

            Ranking preferences (minimize_climb) and the exact wording of an
            address are not in here: they change which route wins, not
            whether the request can be satisfied. This is the stability
            number that matters for reproducibility.
            """
            r = parsed.get("route") or {}
            e = parsed.get("edit") or {}
            return {
                "type": parsed.get("request_type"),
                "miles": r.get("distance_miles"),
                "shape": r.get("shape"),
                "cap": r.get("max_climb_ft"),
                "n_via": len(r.get("via_places") or []),
                "n_avoid": len(r.get("avoid_places") or []),
                "edit_mode": e.get("mode"),
            }

        drift: dict[str, list[Any]] = {}
        semantic: dict[str, int] = {}
        hard: dict[str, int] = {}
        for cid, rs in per_case.items():
            vals = [json.dumps(spec_of(x["parsed"]), sort_keys=True)
                    for x in rs]
            if len(set(vals)) > 1:
                drift[cid] = [json.loads(v) for v in sorted(set(vals))]
            norms = {json.dumps(_norm(spec_of(x["parsed"])), sort_keys=True)
                     for x in rs}
            semantic[cid] = len(norms)
            hard[cid] = len({json.dumps(_hard(x["parsed"]), sort_keys=True)
                             for x in rs})
        summary["parse_variance"] = {
            "repeats": pv["repeats"],
            "cases": len(per_case),
            "cases_with_unstable_verdict": len(unstable),
            "unstable": unstable,
            "cases_with_spec_drift": len(drift),
            "cases_stable": len(per_case) - len(drift),
            "field_drift": drift,
            "cases_with_semantic_drift": sum(1 for n in semantic.values()
                                             if n > 1),
            "cases_hard_stable": sum(1 for n in hard.values() if n == 1),
            "per_case_hard_variants": hard,
            "cases_semantically_stable": sum(1 for n in semantic.values()
                                             if n == 1),
            "per_case_semantic_variants": semantic,
            "per_case_distinct_specs": {
                cid: len({json.dumps(spec_of(x["parsed"]), sort_keys=True)
                          for x in rs})
                for cid, rs in sorted(per_case.items())},
            "parse_cost_usd": pv.get("parse_cost_usd"),
        }

    if compare:
        base = {r["id"]: r for r in load_run(compare) if r.get("repeat", 1) == 1}
        moves = []
        for r in primary:
            b = base.get(r["id"])
            if b and b["verdict"] != r["verdict"]:
                moves.append({"id": r["id"], "category": r["category"],
                              "split": r["split"],
                              "from": b["verdict"], "to": r["verdict"],
                              "was": b["fail_reasons"],
                              "now": r["fail_reasons"]})
        summary["compare"] = {
            "against": compare,
            "against_overall": tally(list(base.values())),
            "against_by_split": {
                s: tally([r for r in base.values() if r["split"] == s])
                for s in ("dev", "heldout")},
            "against_live_by_split": {
                s: tally([r for r in base.values()
                          if r["split"] == s and r["tier"] == "live"])
                for s in ("dev", "heldout")},
            "changed": sorted(moves, key=lambda m: m["id"]),
            "improved": sum(1 for m in moves if VERDICTS.index(m["to"])
                            < VERDICTS.index(m["from"])),
            "regressed": sum(1 for m in moves if VERDICTS.index(m["to"])
                             > VERDICTS.index(m["from"])),
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--compare", default=None)
    args = ap.parse_args()
    s = build(args.run, args.compare)
    out = os.path.join(RESULTS_DIR, args.run, "summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    o = s["overall"]
    print(f"{args.run}: {o['total']} cases -> {o['pass']} pass, "
          f"{o['partial']} partial, {o['fail']} fail, "
          f"{o['inconclusive']} inconclusive")
    for split, t in s["by_split"].items():
        print(f"  {split:<8} {t['pass']}/{t['total']} pass, "
              f"{t['partial']} partial, {t['fail']} fail")
    if s.get("compare"):
        c = s["compare"]
        print(f"  vs {c['against']}: {c['improved']} improved, "
              f"{c['regressed']} regressed, {len(c['changed'])} changed")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
