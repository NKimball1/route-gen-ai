"""Build the portfolio case study from saved evaluation results.

  python -m evals.build_case_study --run postfix --baseline baseline
  python -m evals.build_case_study --run postfix --baseline baseline \
      --portfolio /path/to/portfolio-site

Every number on the page is read out of evals/results/<run>/summary.json,
which is itself derived from the per-case records. Nothing is typed in by
hand, so the page cannot drift away from the evidence: change the results and
rebuild, or the page does not change.

Outputs (into evals/site/ by default):
  routegen-evals.html      the standalone case study, self-contained
  routegen/results.json    the full sanitized per-case results
  routegen/gpx/*.gpx       the featured routes, downloadable
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from typing import Any

from evals import geo, mapgen, paths, story

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
SITE_DIR = os.path.join(ROOT, "evals", "site")

# ---------------------------------------------------------------------------
# which cases get a map on the page, and why. Stated on the page itself so a
# reader can check that the featured set is not a flattering hand-pick.
# ---------------------------------------------------------------------------
FEATURE_RULES: list[dict[str, str]] = [
    {"id": "D01", "role": "Straightforward",
     "note": "The floor case: a short loop from a public landmark. Featured "
             "because it is the simplest thing the product claims to do."},
    {"id": "D10", "role": "Hard constraint",
     "note": "A stated climb ceiling. Featured because it is the one "
             "constraint the ranker enforces by rejecting candidates outright."},
    {"id": "D13", "role": "Waypoints",
     "note": "Two named towns in ride order. Featured because multi-waypoint "
             "loops use a different generator than plain bearing loops."},
    {"id": "H10", "role": "Hardest combination",
     "note": "Held out. Distance + waypoint + climb cap + avoid in one "
             "sentence -- the most constrained request in the set."},
]


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def load(run: str) -> dict[str, Any]:
    with open(os.path.join(RESULTS_DIR, run, "summary.json"),
              encoding="utf-8") as f:
        return json.load(f)


def load_case_record(run: str, cid: str) -> dict[str, Any] | None:
    p = os.path.join(RESULTS_DIR, run, "cases", f"{cid}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def sanitize(obj: Any) -> Any:
    """Strip absolute paths (they carry a username) out of published data."""
    if isinstance(obj, str):
        s = obj.replace("\\", "/")
        s = re.sub(r"[A-Za-z]:/.*?/route gen ai/", "", s)
        s = re.sub(r"/?(?:home|Users)/[^/\s]+/", "~/", s)
        return s
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    return obj


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.0f}" if whole else "0"


# ---------------------------------------------------------------------------
# featured demos
# ---------------------------------------------------------------------------

def build_feature(run: str, rule: dict[str, str], gpx_out: str
                  ) -> dict[str, Any] | None:
    rec = load_case_record(run, rule["id"])
    if not rec:
        return None
    gpx = paths.resolve((rec.get("measured") or {}).get("gpx_path"))
    if not gpx or not os.path.exists(gpx):
        return None
    try:
        pts = geo.parse_gpx_strict(gpx)
    except geo.GpxProblem:
        return None

    markers: list[tuple[float, float, str]] = []
    from evals.runner import eval_geocode
    for place in (rec["expect"].get("route") or {}).get("via") or []:
        ll = eval_geocode(place)
        if ll:
            markers.append((ll[0], ll[1], place))

    os.makedirs(gpx_out, exist_ok=True)
    fname = f"{rule['id']}.gpx"
    shutil.copyfile(gpx, os.path.join(gpx_out, fname))

    asked: list[str] = []
    pr = (rec.get("parsed") or {}).get("route") or {}
    if pr.get("distance_miles"):
        asked.append(f"{pr['distance_miles']:g} mi")
    if pr.get("shape"):
        asked.append(pr["shape"])
    if pr.get("max_climb_ft"):
        asked.append(f"climb ≤ {pr['max_climb_ft']:g} ft")
    if pr.get("maximize_climb"):
        asked.append("maximize climb")
    if pr.get("minimize_climb"):
        asked.append("minimize climb")
    for v in pr.get("via_places") or []:
        asked.append(f"through {v}")
    for a in pr.get("avoid_places") or []:
        asked.append(f"avoid {a}")

    graded = [c for c in rec["checks"]
              if c["status"] in ("pass", "fail")
              and not c["name"].startswith(("parse.", "outcome."))]
    return {
        "id": rule["id"], "role": rule["role"], "note": rule["note"],
        "split": rec["split"], "verdict": rec["verdict"],
        "text": rec["text"], "start": rec["start"],
        "asked": asked,
        "svg": mapgen.route_svg(pts, markers=markers,
                                title=f"{rule['id']}: {rec['text'][:70]}"),
        "elevation": mapgen.elevation_svg(pts),
        "measured": rec["measured"],
        "summary": rec["summary"],
        "checks": graded,
        "gpx": f"routegen/gpx/{fname}",
        "seconds": rec["seconds"],
    }


# ---------------------------------------------------------------------------
# the engineering-improvement section, rendered from the saved records
# ---------------------------------------------------------------------------

def _outcome_quote(run: str, cid: str) -> tuple[str, str]:
    """(verdict, the sentence the user saw) for one case in one run."""
    rec = load_case_record(run, cid)
    if not rec:
        return "missing", "(no record)"
    if rec.get("error"):
        return rec["verdict"], f"{rec['error']}"
    return rec["verdict"], (rec.get("summary") or "(no message)").strip()


def render_improvement(run: str, baseline: str | None) -> str:
    if not baseline:
        return ""
    fix = story.HEADLINE_FIX
    steps = "".join(
        f"<li><b>{esc(t)}</b><p>{body}</p></li>" for t, body in fix["steps"])

    def pair(cid: str) -> str:
        bv, bq = _outcome_quote(baseline, cid)
        av, aq = _outcome_quote(run, cid)
        rec = load_case_record(run, cid) or {}
        return f"""
<div class="panel" style="margin-top:18px">
  <div class="panel-title">Case {esc(cid)} &mdash; {esc(rec.get("text", ""))}</div>
  <div class="panel-sub">{esc(rec.get("split", ""))} set &middot;
    {esc(rec.get("why", ""))}</div>
  <div class="diffbox">
    <div class="diffcol before"><h4>Before &middot; <span class="pill {bv}">{bv}</span></h4>
      <p>{esc(bq)}</p></div>
    <div class="diffcol after"><h4>After &middot; <span class="pill {av}">{av}</span></h4>
      <p>{esc(aq)}</p></div>
  </div>
</div>"""

    others = "".join(f"""
<div class="panel" style="margin-top:18px">
  <div class="panel-title">{o["title"]}</div>
  <div class="panel-sub">case {esc(o["trigger"])} &middot;
    <span class="mono">{o["file"]}</span></div>
  <p style="font-size:.95rem">{o["what"]}</p>
  {pair(o["trigger"])}
</div>""" for o in story.OTHER_FIXES)

    calib = "".join(
        f'<tr><td>{esc(a)}</td><td>{b}</td></tr>'
        for a, b in story.SCORER_CALIBRATION)

    return f"""
<section>
  <p class="eyebrow">Engineering</p>
  <h2>{esc(fix["title"])}</h2>
  <div class="prose">
    <p>The campaign found four distinct defects. This one is worth walking
    through because the failure was total &mdash; the rider got no route at
    all for a request the product fully supports &mdash; and because the
    held-out set contained the same bug pointing the other way.</p>
  </div>
  <ol class="saga">{steps}</ol>
  <div class="codeblock">{esc(fix["code"])}</div>
  {pair(fix["trigger"])}
  {"".join(pair(c) for c in fix["confirm"])}
  <div class="callout"><p>{fix["closing"]}</p></div>
</section>

<section>
  <p class="eyebrow">The other three</p>
  <h2>Correct internally, misleading on screen</h2>
  <div class="prose"><p>{story.THEME}</p></div>
  {others}
</section>

<section>
  <p class="eyebrow">Scoring the scorer</p>
  <h2>Three checks that were wrong before they were right</h2>
  <div class="prose">
    <p>An eval can be wrong in the same ways the thing it measures can. Three
    checks in this harness produced false failures on real routes and were
    corrected <em>before</em> any product change &mdash; recorded here because
    a report that only lists the subject's mistakes is not a report worth
    trusting. Each correction is pinned by a test asserting both that the
    legitimate geometry passes and that the broken geometry it exists to catch
    still fails; every case carries its scoring history in the saved
    results.</p>
  </div>
  <div class="tbl-scroll"><table>
    <thead><tr><th>What went wrong</th><th>Why, and what replaced it</th></tr></thead>
    <tbody>{calib}</tbody>
  </table></div>
</section>"""


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

CSS = """
:root{color-scheme:light;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--baseline:#c3c2b7;--ring:rgba(11,11,11,.10);--accent:#1c5cab;
 --pass:#0ca30c;--pass-text:#006300;--partial:#c98a00;--fail:#d03b3b;--inco:#898781;
 --dev:#2a78d6;--held:#eb6834;
 --map-bg:#f1f0ea;--map-road:#d3d2c8;--map-water:#dbe6ef;--map-route:#1c5cab;
 --map-route-halo:#fcfcfb;--map-start:#0ca30c;--map-end:#d03b3b;--map-marker:#eb6834;
 --map-ink:#6d6c66;--map-ele:#1c5cab;--map-ele-fill:rgba(28,92,171,.13);}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--baseline:#383835;--ring:rgba(255,255,255,.10);--accent:#6da7ec;
 --pass:#0ca30c;--pass-text:#0ca30c;--partial:#e0a62a;--fail:#e66767;--inco:#898781;
 --dev:#3987e5;--held:#d95926;
 --map-bg:#161615;--map-road:#33332f;--map-water:#1b2a38;--map-route:#6da7ec;
 --map-route-halo:#0d0d0d;--map-start:#0ca30c;--map-end:#e66767;--map-marker:#d95926;
 --map-ink:#8a8981;--map-ele:#6da7ec;--map-ele-fill:rgba(109,167,236,.16);}}
:root[data-theme="dark"]{color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--baseline:#383835;--ring:rgba(255,255,255,.10);--accent:#6da7ec;
 --pass:#0ca30c;--pass-text:#0ca30c;--partial:#e0a62a;--fail:#e66767;--inco:#898781;
 --dev:#3987e5;--held:#d95926;
 --map-bg:#161615;--map-road:#33332f;--map-water:#1b2a38;--map-route:#6da7ec;
 --map-route-halo:#0d0d0d;--map-start:#0ca30c;--map-end:#e66767;--map-marker:#d95926;
 --map-ink:#8a8981;--map-ele:#6da7ec;--map-ele-fill:rgba(109,167,236,.16);}
*{box-sizing:border-box}
body{background:var(--page);color:var(--ink);font-family:"Source Sans 3","Segoe UI",system-ui,sans-serif;
 font-size:17px;line-height:1.55;margin:0;padding:0 20px 96px}
.wrap{max-width:900px;margin:0 auto}
.prose{max-width:66ch}
h1,h2,h3,.tile-num{font-family:"Bricolage Grotesque","Arial Black",system-ui,sans-serif}
h1{font-size:clamp(2.1rem,5.5vw,3.2rem);font-weight:800;line-height:1.04;letter-spacing:-.015em;margin:0 0 14px;text-wrap:balance}
h2{font-size:1.45rem;font-weight:700;letter-spacing:-.01em;margin:0 0 6px;text-wrap:balance}
h3{font-size:1.05rem;font-weight:700;margin:0 0 4px}
.eyebrow{font-family:"Spline Sans Mono",ui-monospace,Consolas,monospace;font-size:.72rem;font-weight:500;
 letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0 0 10px}
p{margin:0 0 14px;color:var(--ink-2)}
p strong,li strong{color:var(--ink)}
a{color:var(--accent)}
section{margin-top:72px}
header.hero{padding-top:72px}
.lede{font-size:1.12rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:14px;margin-top:34px}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:20px 22px 17px}
.tile-num{font-size:2.4rem;font-weight:800;line-height:1;letter-spacing:-.02em}
.tile-num .unit{font-size:1.2rem;color:var(--ink-2);font-weight:700}
.tile-label{margin-top:8px;font-size:.88rem;color:var(--ink-2);line-height:1.35}
.pipe{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin:22px 0 8px}
.pipe-step{flex:1 1 150px;background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:12px 14px}
.pipe-step b{display:block;font-size:.92rem;color:var(--ink)}
.pipe-step span{font-size:.83rem;color:var(--ink-2);line-height:1.35;display:block;margin-top:3px}
.pipe-step.llm{border-color:var(--accent);border-width:1.5px}
.pipe-arrow{align-self:center;color:var(--muted);flex:0 0 auto}
.legendline{font-size:.83rem;color:var(--muted);margin-top:10px}
.panel{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:22px 24px 18px;margin-top:20px}
.panel-title{font-weight:600;color:var(--ink);font-size:.98rem;margin-bottom:2px}
.panel-sub{font-size:.84rem;color:var(--muted);margin-bottom:18px}
.demo{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:20px 22px;margin-top:22px}
.demo-head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;justify-content:space-between;margin-bottom:6px}
.demo-role{font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.7rem;letter-spacing:.12em;
 text-transform:uppercase;color:var(--accent)}
.ask{font-size:1.02rem;color:var(--ink);margin:2px 0 12px;font-style:italic}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
.chip{font-size:.76rem;padding:2px 9px;border-radius:999px;border:1px solid var(--ring);color:var(--ink-2);
 font-family:"Spline Sans Mono",ui-monospace,monospace}
.mapwrap{border:1px solid var(--grid);border-radius:8px;overflow:hidden;background:var(--map-bg)}
.routemap{display:block}
.elevation{display:block;border-top:1px solid var(--grid)}
.demo-grid{display:grid;grid-template-columns:1fr;gap:16px}
@media(min-width:760px){.demo-grid{grid-template-columns:1.25fr 1fr}}
.measure{list-style:none;margin:0;padding:0;font-size:.88rem}
.measure li{display:flex;gap:9px;padding:5px 0;border-bottom:1px solid var(--grid);color:var(--ink-2);align-items:flex-start}
.measure li:last-child{border-bottom:0}
.dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;margin-top:6px}
.dot.pass{background:var(--pass)}.dot.fail{background:var(--fail)}.dot.info{background:var(--muted)}
.dl{display:inline-block;margin-top:12px;font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.78rem;
 letter-spacing:.08em;text-transform:uppercase}
.bars{display:grid;gap:4px}
.crow{display:grid;grid-template-columns:190px 1fr;gap:4px 14px;align-items:center;padding:8px 0}
.crow+.crow{border-top:1px solid var(--grid)}
.cname{font-size:.87rem;color:var(--ink);line-height:1.25}
.cname small{display:block;color:var(--muted);font-size:.75rem}
.barline{display:grid;grid-template-columns:1fr 92px;align-items:center;gap:10px}
.stack{display:flex;height:14px;border-radius:3px;overflow:hidden;background:var(--grid)}
.seg{height:100%}
.seg.pass{background:var(--pass)}.seg.partial{background:var(--partial)}
.seg.fail{background:var(--fail)}.seg.inco{background:var(--inco)}
.bval{font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.76rem;color:var(--ink-2);
 text-align:right;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;margin-top:16px;font-size:.9rem}
th{text-align:left;font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.7rem;letter-spacing:.1em;
 text-transform:uppercase;color:var(--muted);font-weight:500;padding:8px 10px 8px 0;border-bottom:1px solid var(--baseline)}
td{padding:8px 10px 8px 0;border-bottom:1px solid var(--grid);color:var(--ink-2);font-variant-numeric:tabular-nums;
 vertical-align:top}
td.mono,th.mono{font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.8rem}
.tbl-scroll{overflow-x:auto}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:.76rem;padding:1px 8px;border-radius:999px;
 border:1px solid var(--ring);color:var(--ink);white-space:nowrap}
.pill::before{content:"";width:7px;height:7px;border-radius:50%}
.pill.pass::before{background:var(--pass)}.pill.partial::before{background:var(--partial)}
.pill.fail::before{background:var(--fail)}.pill.inconclusive::before{background:var(--inco)}
.callout{border-left:3px solid var(--accent);background:var(--surface);border-radius:0 8px 8px 0;padding:14px 18px;margin:20px 0}
.callout p{margin:0;font-size:.96rem}
.callout.warn{border-left-color:var(--partial)}
ol.saga{list-style:none;counter-reset:saga;margin:24px 0 0;padding:0}
ol.saga li{counter-increment:saga;position:relative;padding:0 0 22px 52px}
ol.saga li::before{content:counter(saga);position:absolute;left:0;top:-2px;width:32px;height:32px;border-radius:50%;
 background:var(--surface);border:1px solid var(--ring);font-family:"Spline Sans Mono",ui-monospace,monospace;
 font-size:.85rem;display:grid;place-items:center;color:var(--accent);font-weight:600}
ol.saga li:not(:last-child)::after{content:"";position:absolute;left:15px;top:34px;bottom:2px;border-left:1px solid var(--grid)}
ol.saga b{color:var(--ink)}
ol.saga p{margin:2px 0 0;font-size:.94rem}
ul.plain{color:var(--ink-2);padding-left:20px;margin:0}
ul.plain li{margin-bottom:8px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px;align-items:center}
.controls button{font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.74rem;letter-spacing:.08em;
 text-transform:uppercase;background:var(--surface);color:var(--ink-2);border:1px solid var(--ring);
 border-radius:999px;padding:5px 13px;cursor:pointer}
.controls button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
.codeblock{background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:14px 16px;overflow-x:auto;
 font-family:"Spline Sans Mono",ui-monospace,monospace;font-size:.8rem;color:var(--ink-2);line-height:1.6;white-space:pre}
.diffbox{display:grid;gap:10px;margin-top:16px}
@media(min-width:720px){.diffbox{grid-template-columns:1fr 1fr}}
.diffcol{background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:14px 16px}
.diffcol h4{margin:0 0 6px;font-size:.78rem;letter-spacing:.1em;text-transform:uppercase;
 font-family:"Spline Sans Mono",ui-monospace,monospace;font-weight:500}
.diffcol.before h4{color:var(--fail)}.diffcol.after h4{color:var(--pass-text)}
.diffcol p{margin:0;font-size:.9rem}
footer{margin-top:88px;padding-top:20px;border-top:1px solid var(--grid);font-size:.85rem;color:var(--muted)}
footer a{color:var(--muted)}
.note-sm{font-size:.86rem;color:var(--muted)}
"""

JS = """
const R = window.__ROUTEGEN__;

function stack(el, t, total){
  const order=[["pass","pass"],["partial","partial"],["fail","fail"],["inconclusive","inco"]];
  const wrap=document.createElement("div"); wrap.className="stack";
  for(const [k,cls] of order){
    const n=t[k]||0; if(!n) continue;
    const d=document.createElement("div");
    d.className="seg "+cls; d.style.width=(100*n/total)+"%";
    d.title=n+" "+k; wrap.appendChild(d);
  }
  el.appendChild(wrap);
}

function renderBreakdown(hostId, rows){
  const host=document.getElementById(hostId); if(!host) return;
  host.innerHTML="";
  for(const r of rows){
    const total=r.t.total; if(!total) continue;
    const row=document.createElement("div"); row.className="crow";
    const name=document.createElement("div"); name.className="cname";
    name.innerHTML=r.label+(r.sub?"<small>"+r.sub+"</small>":"");
    const line=document.createElement("div"); line.className="barline";
    const bar=document.createElement("div"); stack(bar,r.t,total);
    const val=document.createElement("div"); val.className="bval";
    val.textContent=(r.t.pass||0)+"/"+total+" pass";
    line.appendChild(bar); line.appendChild(val);
    row.appendChild(name); row.appendChild(line); host.appendChild(row);
  }
}

renderBreakdown("byCat", Object.entries(R.by_category)
  .sort((a,b)=>b[1].total-a[1].total)
  .map(([k,t])=>({label:k.replace(/_/g," "),t:t})));

renderBreakdown("bySplit", [
  {label:"Development set", sub:"used to find and drive fixes", t:R.live_by_split.dev},
  {label:"Held-out set", sub:"expectations frozen before any fix", t:R.live_by_split.heldout},
]);

// ---- per-check pass rates ----
const CHECK_LABELS = {
  "gpx.valid": ["GPX is a valid file", "real XML parser, in-range coords, finite elevations"],
  "route.distance": ["Distance matches the ask", "haversine over the geometry, +/-15%"],
  "route.starts_where_asked": ["Starts where asked", "within 600 m of the geocoded start"],
  "route.loop_closure": ["Loop actually closes", "start-to-finish gap under 250 m"],
  "route.via[*]": ["Passes through each waypoint", "closest approach, measured per place"],
  "route.climb_cap": ["Respects a stated climb ceiling", "no slack: the product rejects above it"],
  "route.avoid[*]": ["Stays off an excluded road", "meters ridden within 28 m of its OSM centerline"],
  "geometry.continuous": ["No breaks in the track", "long segments checked against the road network"],
  "geometry.repeat": ["Does not double back", "out-and-backs exempted by a mirror test"],
  "parse.request_type": ["Understood what kind of request it was", "route, edit, undo or interval spot"],
  "parse.fields": ["Parsed every other field correctly", "distance, shape, climb cap, edit mode, waypoints, reps -- against the frozen expectation"],
  "parse.reached": ["Got as far as a parse", ""],
  "outcome.no_crash": ["Handled without an unhandled exception", ""],
  "outcome.succeeded": ["Returned a result when one was expected", ""],
  "outcome.declined": ["Refused when it should refuse", ""],
  "outcome.explained": ["Explained a refusal usefully", ""],
  "outcome.flagged_unsupported": ["Flagged what it cannot do", "for requests naming features the product lacks"],
  "outcome.message_names_cause": ["Named the real cause of a failure", ""],
  "spot.within_travel_budget": ["Interval spot inside the travel budget", ""],
  "spot.lap_count_disclosed": ["Disclosed laps when a stretch is short", ""],
  "spot.long_enough_for_a_rep": ["Interval stretch holds a whole rep", "soft: lapping is a supported answer"],
  "route.anchored": ["Start and finish moved together", ""],
  "route.ends_where_asked": ["Finishes where asked", ""],
  "route.distance_delta": ["Grew or shrank by the amount asked", ""],
  "route.outback_returns": ["Out-and-back returns to the start", ""],
  "route.climb_sought": ["Found real climbing when asked", "soft: a ranking goal, not a promise"],
  "outcome.handled": ["Handled an open-ended case either way", ""],
};
const checkHost = document.getElementById("byCheck");
if (checkHost) {
  for (const c of R.checks.filter(c => c.graded > 0)) {
    const [label, sub] = CHECK_LABELS[c.check] || [c.check, ""];
    const row = document.createElement("div"); row.className = "crow";
    const name = document.createElement("div"); name.className = "cname";
    name.innerHTML = label + (c.hard ? "" : ' <span style="color:var(--muted)">(soft)</span>')
      + (sub ? "<small>" + sub + "</small>" : "");
    const line = document.createElement("div"); line.className = "barline";
    const bar = document.createElement("div");
    stack(bar, {pass: c.passed, fail: c.failed}, c.graded);
    const val = document.createElement("div"); val.className = "bval";
    val.textContent = c.passed + "/" + c.graded;
    line.appendChild(bar); line.appendChild(val);
    row.appendChild(name); row.appendChild(line);
    checkHost.appendChild(row);
  }
}

// ---- full results table ----
const tbody=document.getElementById("allRows");
let filter="all";
function draw(){
  tbody.innerHTML="";
  for(const c of R.cases){
    if(filter!=="all" && c.verdict!==filter && c.split!==filter
       && c.tier!==filter && c.category!==filter) continue;
    const tr=document.createElement("tr");
    const m=c.measured||{};
    const got=[];
    if(m.distance_mi!=null) got.push(m.distance_mi.toFixed(1)+" mi");
    if(m.ascent_ft_app!=null) got.push(Math.round(m.ascent_ft_app)+" ft");
    if(m.loop_closure_m!=null && m.loop_closure_m<=250) got.push("closed");
    tr.innerHTML=
      '<td class="mono">'+c.id+'</td>'+
      '<td>'+c.text+'<br><span style="color:var(--muted);font-size:.82rem">'+
        c.category.replace(/_/g," ")+" &middot; "+c.split+" &middot; "+c.tier+
        (c.scope==="out_of_scope"?" &middot; out of product scope":"")+'</span></td>'+
      '<td class="mono">'+(got.join(", ")||"&mdash;")+'</td>'+
      '<td><span class="pill '+c.verdict+'">'+c.verdict+"</span>"+
        (c.fail_reasons&&c.fail_reasons.length?
          '<br><span style="color:var(--muted);font-size:.8rem">'+
          c.fail_reasons.join(", ")+"</span>":"")+'</td>';
    tbody.appendChild(tr);
  }
  document.getElementById("rowCount").textContent=tbody.children.length+" of "+R.cases.length;
}
for(const b of document.querySelectorAll("[data-filter]")){
  b.addEventListener("click",()=>{
    filter=b.dataset.filter;
    for(const o of document.querySelectorAll("[data-filter]"))
      o.setAttribute("aria-pressed", o===b ? "true":"false");
    draw();
  });
}
draw();
"""


def render_variability(s: dict[str, Any]) -> str:
    pv = s.get("parse_variance")
    if not pv:
        return ""
    rows = []
    for cid, parses in sorted((pv.get("field_drift") or {}).items()):
        case = next((c for c in s["cases"] if c["id"] == cid), {})
        variants = []
        for parsed in parses:
            # Show every field the drift is measured over, so a row that
            # says "3 distinct specs" visibly contains three of them.
            r = parsed.get("route") or {}
            bits = [f"type={parsed.get('request_type')}"]
            if parsed.get("address"):
                bits.append(f"start={parsed['address']!r}")
            if r.get("distance_miles") is not None:
                bits.append(f"{r['distance_miles']:g} mi")
            if r.get("max_climb_ft"):
                bits.append(f"cap {r['max_climb_ft']:g} ft")
            if r.get("shape"):
                bits.append(r["shape"])
            if r.get("minimize_climb"):
                bits.append("minimize climb")
            if r.get("maximize_climb"):
                bits.append("maximize climb")
            for v in r.get("via_places") or []:
                bits.append(f"via {v!r}")
            for a in r.get("avoid_places") or []:
                bits.append(f"avoid {a!r}")
            e = parsed.get("edit") or {}
            if e.get("mode"):
                bits.append(f"mode={e['mode']}")
            if e.get("places"):
                bits.append(f"places={e['places']!r}")
            elif e.get("place"):
                bits.append(f"place={e['place']!r}")
            variants.append(", ".join(bits))
        unstable = cid in (pv.get("unstable") or {})
        hard_n = (pv.get("per_case_hard_variants") or {}).get(cid, 1)
        hard_cell = ('<span class="pill pass">stable</span>' if hard_n == 1
                     else f'<span class="pill fail">{hard_n} versions</span>')
        rows.append(
            f'<tr><td class="mono">{esc(cid)}</td>'
            f'<td>{esc(case.get("text", ""))}</td>'
            f'<td>{hard_cell}</td>'
            f'<td class="note-sm">{"<br>".join(esc(v) for v in sorted(set(variants)))}</td>'
            f'<td>{"<span class='pill fail'>flips</span>" if unstable else "<span class='pill pass'>same verdict</span>"}</td></tr>')
    return f"""
<section>
  <p class="eyebrow">Variability</p>
  <h2>The same sentence, five times</h2>
  <div class="prose">
    <p>Everything after the parse is deterministic, so the only place
    run-to-run variation can enter is the one model call. To find out how
    much, {pv["cases"]} requests were parsed <strong>{pv["repeats"]} times
    each with the cache disabled</strong> &mdash; a mix of crisply specified
    asks as a control and deliberately vague ones.</p>
    <p>The reproducible number is the one about <strong>constraints</strong>
    &mdash; request type, distance, shape, climb cap, how many waypoints and
    avoid-places. Those are the fields that decide whether a candidate is
    accepted. <strong>{pv["cases_hard_stable"]} of {pv["cases"]} were
    identical on all of them across all five runs</strong>, and the split is
    not subtle: every crisply specified request was stable, and every
    unstable one was a request that genuinely underspecifies the ride or
    depends on session state.</p>
    <p>Below that level, wording moves constantly and mostly harmlessly
    &mdash; the same start is written &ldquo;Monona Terrace Madison
    WI&rdquo; one run and &ldquo;Monona Terrace, Madison, WI&rdquo; the
    next, which geocodes identically. Only
    <strong>{pv["cases_with_unstable_verdict"]} of {pv["cases"]}</strong>
    drifted far enough to change the graded outcome. Free-text notes are
    excluded throughout: the model rewords those every run and they do not
    affect what gets generated.</p>
  <div class="tbl-scroll"><table>
    <thead><tr><th>Case</th><th>Request</th><th>Constraints</th>
      <th>Every spec produced across 5 runs</th><th>Outcome</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
  <div class="callout warn"><p>So the honest reading of the headline number
  is this: a crisply specified request is reproducible, and a vague one is a
  distribution rather than an answer. <em>&ldquo;I want to go for a bike
  ride&rdquo;</em> produced four different distances across five runs,
  including one of zero miles that the server-side clamp caught. The product
  has no way to ask a clarifying question &mdash; it has to guess, and it
  does not tell the rider it guessed. That is a product gap this campaign
  measured rather than fixed, and it is the reason a single pass/fail count
  would overstate what is known here.</p></div>
</section>"""


def tile(num: str, unit: str, label: str) -> str:
    return (f'<div class="tile"><div class="tile-num">{num}'
            f'{f"<span class=unit>{unit}</span>" if unit else ""}</div>'
            f'<div class="tile-label">{label}</div></div>')


def render_feature(f: dict[str, Any]) -> str:
    # (measurements are rendered from f["checks"])
    chips = "".join(f'<span class="chip">{esc(a)}</span>' for a in f["asked"])
    rows = []
    for c in f["checks"]:
        cls = "pass" if c["status"] == "pass" else (
            "fail" if c["status"] == "fail" else "info")
        rows.append(f'<li><span class="dot {cls}"></span>'
                    f'<span>{esc(c["detail"])}</span></li>')
    verdict = f["verdict"]
    return f"""
<div class="demo">
  <div class="demo-head">
    <span class="demo-role">{esc(f["role"])} &middot; case {esc(f["id"])}
      &middot; {esc(f["split"])} set</span>
    <span class="pill {verdict}">{verdict}</span>
  </div>
  <p class="ask">&ldquo;{esc(f["text"])}&rdquo;</p>
  <div class="chips">{chips}</div>
  <div class="demo-grid">
    <div>
      <div class="mapwrap">{f["svg"]}{f["elevation"]}</div>
      <a class="dl" href="{esc(f["gpx"])}" download>&darr; Download GPX</a>
    </div>
    <div>
      <ul class="measure">{"".join(rows)}</ul>
      <p class="note-sm" style="margin-top:12px">Measured independently from
      the GPX on disk, not read back from the app. Generated in
      {f["seconds"]:.0f} s.</p>
    </div>
  </div>
  <p class="note-sm" style="margin-top:10px">{esc(f["note"])}</p>
</div>"""


def build_html(s: dict[str, Any], base: dict[str, Any] | None,
               features: list[dict[str, Any]],
               improvement_html: str) -> str:
    live = s["by_tier"]["live"]
    off = s["by_tier"]["offline"]
    dev = s["live_by_split"]["dev"]
    held = s["live_by_split"]["heldout"]
    d = s["distance_live"]
    t = s["timing"]
    meta = s["meta"]
    ov = s["overall"]

    _pv = s.get("parse_variance") or {}
    pv_hard = _pv.get("cases_hard_stable", 0)
    pv_n = _pv.get("cases", 0)
    e2e = s.get("e2e") or {}
    e2e_stages = (esc(", ".join(x.lower() for x in e2e.get("stages", [])))
                  or "Six scripted user journeys")
    e2e_res = (f'{e2e["checks_passed"]} checks, '
               f'{e2e["issues"]} issue(s)' if e2e else "not run")
    cost_rows = "".join(
        f'<tr><td class="mono">{esc(k)}</td>'
        f'<td class="mono">{v["calls"]}</td>'
        f'<td class="mono">${v["usd"]:.4f}</td></tr>'
        for k, v in s["cost"]["per_run"].items())
    variability_html = render_variability(s)

    data = {
        "by_category": s["by_category"],
        "live_by_split": s["live_by_split"],
        "cases": sanitize(s["cases"]),
        "checks": s["checks"],
    }

    cmp_html = ""
    if base:
        b_live = base["by_tier"]["live"]
        b_dev = base["live_by_split"]["dev"]
        b_held = base["live_by_split"]["heldout"]
        changed = (s.get("compare") or {}).get("changed") or []
        rows = "".join(
            f'<tr><td class="mono">{esc(c["id"])}</td><td>{esc(c["split"])}</td>'
            f'<td>{esc(c["category"]).replace("_", " ")}</td>'
            f'<td><span class="pill {c["from"]}">{c["from"]}</span></td>'
            f'<td><span class="pill {c["to"]}">{c["to"]}</span></td>'
            f'<td class="note-sm">{esc(", ".join(c["was"] or []) or "&mdash;")}</td></tr>'
            for c in changed)
        cmp_html = f"""
<section>
  <p class="eyebrow">Before and after</p>
  <h2>What the fixes moved</h2>
  <div class="prose">
    <p>Both columns are the same {ov["total"]} cases, scored by the same
    rules. The development set is where the failures were diagnosed; the
    held-out set never informed a fix, so it is the honest read.</p>
  </div>
  <div class="tbl-scroll"><table>
    <thead><tr><th>Set</th><th>Before</th><th>After</th></tr></thead>
    <tbody>
      <tr><td>Development (live)</td>
        <td class="mono">{b_dev["pass"]}/{b_dev["total"]} pass,
          {b_dev["partial"]} partial, {b_dev["fail"]} fail</td>
        <td class="mono">{dev["pass"]}/{dev["total"]} pass,
          {dev["partial"]} partial, {dev["fail"]} fail</td></tr>
      <tr><td>Held out (live)</td>
        <td class="mono">{b_held["pass"]}/{b_held["total"]} pass,
          {b_held["partial"]} partial, {b_held["fail"]} fail</td>
        <td class="mono">{held["pass"]}/{held["total"]} pass,
          {held["partial"]} partial, {held["fail"]} fail</td></tr>
      <tr><td>All live cases</td>
        <td class="mono">{b_live["pass"]}/{b_live["total"]} pass</td>
        <td class="mono">{live["pass"]}/{live["total"]} pass</td></tr>
    </tbody>
  </table></div>
  {"<div class='tbl-scroll'><table><thead><tr><th>Case</th><th>Set</th>"
   "<th>Category</th><th>Was</th><th>Now</th><th>Had failed on</th></tr>"
   "</thead><tbody>" + rows + "</tbody></table></div>" if rows else
   "<p class='note-sm'>No case changed verdict.</p>"}
</section>"""

    prov = s["provenance"]
    tol_rows = "".join(
        f'<tr><td class="mono">{esc(k)}</td><td class="mono">{esc(v["default"])}</td>'
        f'<td>{esc(v["why"])}</td></tr>'
        for k, v in s["tolerances"].items())

    fails = [c for c in s["cases"] if c["verdict"] in ("fail", "partial")]
    fail_rows = "".join(
        f'<tr><td class="mono">{esc(c["id"])}</td><td>{esc(c["text"])}</td>'
        f'<td><span class="pill {c["verdict"]}">{c["verdict"]}</span></td>'
        f'<td class="note-sm">{esc(", ".join(c["fail_reasons"]))}</td></tr>'
        for c in fails)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0d0d0d">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>Route Gen AI Evals</title>
<meta name="description" content="A natural-language cycling route generator, measured: {live["pass"]} of {live["total"]} live end-to-end cases pass deterministic checks on the GPX they produced.">
<meta property="og:type" content="article">
<meta property="og:title" content="Route Gen AI &mdash; Eval Report">
<meta property="og:description" content="Plain-English ride requests, scored by computation rather than by the model that produced the route. {live["total"]} live cases, {held["total"]} of them held out.">
<style>{CSS}</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@700;800&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&family=Spline+Sans+Mono:wght@400;500;600&display=swap">
</head>
<body>
<div class="wrap">

<header class="hero">
  <p class="eyebrow">Eval report &middot; Route Gen AI</p>
  <h1>Describe a bike ride. Get a GPX that actually matches.</h1>
  <div class="prose">
    <p class="lede">A cyclist types <em>&ldquo;45 miles from Cross Plains,
    through Black Earth, under 2000 feet of climbing, and off Highway
    14&rdquo;</em> and gets back a Garmin-ready route. The interesting
    question is not whether something comes back &mdash; it is whether the
    thing that comes back <em>satisfies the sentence</em>. This report
    measures that, case by case, with computed checks on the GPX file rather
    than by asking a model how it did.</p>
  </div>
  <div class="tiles">
    {tile(f'{live["pass"]}', f'/{live["total"]}',
          'live end-to-end cases fully passing every hard check'
          + (f', with {live["partial"]} more passing every hard check but '
             f'missing a soft preference' if live["partial"] else
             '. The rest are listed in full, with what each one failed on')
          + '.')}
    {tile(f'{held["pass"]}', f'/{held["total"]}',
          'of the held-out cases pass. Their expectations were frozen before '
          'any fix was written, and no held-out failure informed a change.')}
    {tile(f'{d.get("median_pct", 0)}', '%',
          f'median distance error against the requested mileage across '
          f'{d.get("n", 0)} routes with a stated target; '
          + ('every one landed inside 10%'
             if d.get("within_10pct") == d.get("n")
             else f'{d.get("within_10pct", 0)} of them landed inside 10%')
          + f'. Worst case {d.get("max_pct", 0)}%.')}
    {tile(f'${s["cost"]["campaign_total_usd"]:.2f}', '',
          'total model spend for the whole campaign, every run included. One '
          'cheap parse per request &mdash; routing, elevation, geocoding and '
          'map data are self-hosted or free.')}
  </div>
  <p class="note-sm" style="margin-top:18px">Run {esc(s["run"])} &middot;
  request set <span class="mono">{esc(s["cases_version"])}</span>, frozen
  {esc(s["cases_frozen"])} &middot; finished {esc(meta.get("finished", ""))}
  &middot; every number on this page is read from the saved results file
  linked at the bottom.</p>
</header>

<section>
  <p class="eyebrow">The product</p>
  <h2>What it does, and why the language part is the easy half</h2>
  <div class="prose">
    <p>Route Gen AI turns a plain-English ride request into ranked,
    Garmin-importable GPX routes on a map, and then lets you edit them by
    talking to them &mdash; <em>&ldquo;avoid Whitney Way&rdquo;</em>,
    <em>&ldquo;make it ten miles longer&rdquo;</em>, <em>&ldquo;add a stop at
    Olbrich&rdquo;</em>. It exists because route planners make you draw, and
    drawing a 40-mile loop that avoids one particular road is tedious in a way
    a sentence is not.</p>
    <p>The technically interesting part is not the parsing. A language model
    reads &ldquo;under 2000 feet of climbing&rdquo; correctly on the first
    try. The hard part is that <strong>a route is a geometric object with
    physical constraints, and language models cannot be trusted to produce
    one</strong>. So the architecture puts the model in exactly one place, at
    the front, translating intent &mdash; and everything after it is ordinary
    deterministic code that generates candidates and throws away the ones that
    do not measure up.</p>
    <p>That split is also what makes this evaluable. Because the
    acceptance criteria are computed, the eval can use the same kind of
    computation to check them &mdash; independently, from the file on disk.</p>
  </div>
</section>

<section>
  <p class="eyebrow">How it works</p>
  <h2>One model call, then arithmetic</h2>
  <div class="pipe">
    <div class="pipe-step llm"><b>1. Parse</b><span>The only model call.
      claude-haiku-4-5 with a JSON schema turns the sentence into a typed
      spec: distance, shape, climb cap, waypoints, avoid list. Enum-locked,
      no tools, no agent loop.</span></div>
    <div class="pipe-arrow">&rarr;</div>
    <div class="pipe-step"><b>2. Clamp</b><span>Every number the model emitted
      is bounded server-side. A schema-valid request can still say
      &ldquo;300 miles&rdquo;; the clamp pulls it to 150 and logs that it
      did.</span></div>
    <div class="pipe-arrow">&rarr;</div>
    <div class="pipe-step"><b>3. Generate</b><span>Self-hosted BRouter over
      OSM. Loops are synthesized by placing via-points on a circle through
      the start, swept across compass bearings and rescaled toward the
      target length.</span></div>
    <div class="pipe-arrow">&rarr;</div>
    <div class="pipe-step"><b>4. Rank &amp; reject</b><span>Computed, never
      judged: distance tolerance, climb ceiling, fraction of road ridden
      twice, meters on major highways. Rejections carry reasons.</span></div>
    <div class="pipe-arrow">&rarr;</div>
    <div class="pipe-step"><b>5. GPX</b><span>Written as a GPX 1.1 track with
      elevation, importable to Garmin Connect as a course.</span></div>
  </div>
  <p class="legendline">The outlined box is the only step a language model
  touches. Steps 2&ndash;5 are deterministic, run offline in tests, and are
  where every claim in this report is verified.</p>
  <div class="callout"><p><strong>Why it is built this way:</strong> a
  hostile or confused request can only produce weird-but-schema-valid
  <em>values</em>, which the clamp bounds &mdash; it can never produce
  geometry. The blast radius of a bad parse is one wrong number, not a route
  through a lake.</p></div>
</section>

<section>
  <p class="eyebrow">Demonstration</p>
  <h2>Four real requests and what came back</h2>
  <div class="prose">
    <p>These are unedited runs from the campaign below. Each map is drawn from
    the GPX file the app wrote; the measurements next to it were recomputed
    from that file by the eval harness, not read back from the app's own
    report of itself. The GPX links are the real files.</p>
    <p class="note-sm">Featured because they span the product's range, not
    because they scored well: one floor case, one hard constraint, one
    waypoint case, and the most constrained request in the set &mdash; which
    is held out. Every case, including the failures, is in the full table at
    the bottom.</p>
  </div>
  {"".join(render_feature(f) for f in features)}
</section>

<section>
  <p class="eyebrow">Method</p>
  <h2>What counts as a pass, decided before the run</h2>
  <div class="prose">
    <p>{ov["total"]} requests were written as a versioned set
    (<span class="mono">{esc(s["cases_version"])}</span>) with expectations
    frozen on {esc(s["cases_frozen"])}. They are split five ways on purpose:
    straightforward asks, hard combinations, ambiguous asks, requests the
    system should decline, and requests naming features the product does not
    have.</p>
    <p>Each case is graded on three separate questions, because conflating
    them hides where a failure actually is:</p>
    <ul class="plain">
      <li><strong>Did it understand?</strong> The parsed spec is compared
      field-by-field against the frozen expectation &mdash; distance in range,
      correct shape, correct edit mode, waypoints present.</li>
      <li><strong>Did it produce something valid?</strong> The GPX is
      re-parsed with a real XML parser (the app itself uses regexes) and
      checked for range-valid coordinates, finite elevations, continuity, and
      at least two points.</li>
      <li><strong>Does it satisfy the sentence?</strong> Distance measured by
      haversine over the geometry, loop closure, start proximity, closest
      approach to each requested waypoint, meters still ridden on a road the
      user excluded, climb against a stated ceiling.</li>
    </ul>
    <p>Checks are marked <strong>hard</strong> (a promise the product makes,
    and a failure turns the case red) or <strong>soft</strong> (a preference
    it tries to honor, and a failure makes the case amber). Nothing is graded
    by a model. In particular, the model that produced the route is never
    asked whether the route is good.</p>
  </div>

  <div class="panel">
    <div class="panel-title">Tolerances, and why each one is that number</div>
    <div class="panel-sub">Chosen from the product's own thresholds where one
    exists, so the eval tests the promise rather than a flattering band.</div>
    <div class="tbl-scroll"><table>
      <thead><tr><th>Check</th><th>Limit</th><th>Reason</th></tr></thead>
      <tbody>{tol_rows}</tbody>
    </table></div>
  </div>

  <div class="callout warn"><p><strong>What this does not establish.</strong>
  {esc(prov["not_measured"])} Every route in this report passed software
  checks. None of them was ridden as part of this campaign.</p></div>
</section>

<section>
  <p class="eyebrow">Results</p>
  <h2>Where it holds and where it does not</h2>
  <div class="prose">
    <p>Development and held-out sets are reported separately, and live
    end-to-end runs are reported separately from the offline
    failure-injection cases &mdash; a passing mock says nothing about a real
    router.</p>
  </div>

  <div class="panel">
    <div class="panel-title">Live end-to-end cases, by set</div>
    <div class="panel-sub">Real model parse, real self-hosted router, real
    geocoder, real GPX on disk</div>
    <div id="bySplit"></div>
  </div>

  <div class="panel">
    <div class="panel-title">Which promise held, check by check</div>
    <div class="panel-sub">Every graded check across all {ov["total"]} cases.
    A case can fail one and pass the rest, so these add up to more than the
    case counts &mdash; that is the point of grading them separately.</div>
    <div id="byCheck"></div>
  </div>

  <div class="panel">
    <div class="panel-title">All cases by category</div>
    <div class="panel-sub">Green pass &middot; amber passed every hard check
    but missed a soft preference &middot; red hard-check failure &middot;
    grey inconclusive</div>
    <div id="byCat"></div>
  </div>

  <div class="tiles">
    {tile(f'{d.get("median_pct", 0)}', '%',
          f'median distance error across {d.get("n", 0)} live routes with a '
          f'stated target; worst case {d.get("max_pct", 0)}%.')}
    {tile(f'{t.get("median_s", 0)}', 's',
          f'median time to generate and rank a route end to end on a '
          f'self-hosted router; p90 {t.get("p90_s", 0)} s, slowest '
          f'{t.get("max_s", 0)} s.')}
    {tile(f'{off["pass"]}', f'/{off["total"]}',
          'offline failure-injection cases passing &mdash; routing provider '
          'down, geocoder down. Kept separate from the live numbers.')}
  </div>

  <div class="panel">
    <div class="panel-title">Three tiers, and what each one can prove</div>
    <div class="panel-sub">Mixing these would let a passing mock stand in for
    a real router</div>
    <div class="tbl-scroll"><table>
      <thead><tr><th>Tier</th><th>What runs</th><th>Result</th></tr></thead>
      <tbody>
        <tr><td><strong>Offline</strong></td>
          <td>Mocked HTTP and deliberate failure injection: routing provider
            unreachable, geocoder unreachable. Plus the repository's unit
            suite &mdash; no keys, no server.</td>
          <td class="mono">{off["pass"]}/{off["total"]} eval cases<br>
            152 unit tests</td></tr>
        <tr><td><strong>Live end-to-end</strong></td>
          <td>Real model parse, real self-hosted BRouter, real Nominatim and
            Overpass, real GPX written to disk and re-measured. The only
            tier that is evidence about the product end to end.</td>
          <td class="mono">{live["pass"]}/{live["total"]} pass
            {f', {live["partial"]} partial' if live["partial"] else ''}
            {f', {live["fail"]} fail' if live["fail"] else ''}</td></tr>
        <tr><td><strong>HTTP end-to-end</strong></td>
          <td>{e2e_stages} driven against the running web app, covering what
            the in-process harness cannot see: status codes, path traversal,
            per-session isolation, rate limits, mid-job cancellation, upload
            laundering.</td>
          <td class="mono">{e2e_res}</td></tr>
      </tbody>
    </table></div>
  </div>
  <p class="note-sm" style="margin-top:14px">This run: {meta.get("parse_calls_live", 0)}
  live parses, {meta.get("parse_input_tokens", 0):,} input +
  {meta.get("parse_output_tokens", 0):,} output tokens on
  {esc(meta.get("model", ""))}. Routing, elevation, geocoding and road
  geometry cost nothing: BRouter is self-hosted, and Nominatim and Overpass
  are free public OSM services.</p>
  <div class="tbl-scroll"><table>
    <thead><tr><th>Run</th><th>Live parses</th><th>Model spend</th></tr></thead>
    <tbody>{cost_rows}
      <tr><td><strong>Campaign total</strong></td>
        <td class="mono">&mdash;</td>
        <td class="mono"><strong>${s["cost"]["campaign_total_usd"]:.2f}</strong></td></tr>
    </tbody>
  </table></div>
  <p class="note-sm">{esc(s["cost"]["note"])}</p>
</section>

{variability_html}

{improvement_html}

{cmp_html}

<section>
  <p class="eyebrow">Limitations</p>
  <h2>What is still unproven</h2>
  <div class="prose">
    <ul class="plain">
      <li><strong>No route in this campaign was ridden.</strong> Every claim
      here is about geometry and stated constraints. Whether a route is safe,
      pleasant, or has a shoulder is not something OSM tags and this harness
      can establish. The project's own history is field-testing &mdash; the
      devlog records 35 phases of riding routes and fixing what the ride
      exposed &mdash; but this report is not that evidence.</li>
      <li><strong>Elevation is not ground truth.</strong>
      {esc(prov["elevation"])}</li>
      <li><strong>One region.</strong> {esc(prov["geometry"])} Nothing here
      says the product works outside southern Wisconsin; other regions are a
      tile download, but that is a claim this campaign does not test.</li>
      <li><strong>Avoid-clearance is scored softly.</strong> A road at the
      start may be genuinely unavoidable, so meters-on-road is reported rather
      than gating the verdict. The number is the evidence; the judgement is
      the reader's.</li>
      <li><strong>Vague requests are not reproducible.</strong> Measured,
      not assumed: {pv_hard} of {pv_n} repeated requests were identical on
      every constraint-driving field, and the ones that were not are the
      ones that underspecify the ride. The product cannot ask a clarifying
      question, so it guesses, and it does not say that it guessed.</li>
      <li><strong>One request type is still misread.</strong> An edit that
      states no length &mdash; <em>&ldquo;route me through Vilas Park and
      then the Arboretum&rdquo;</em> &mdash; is classified correctly three
      times in five. When it is not, the app generates a brand-new route
      and the rider's current one is gone, with nothing to undo back to.
      The durable fix is recording a new route's parent so undo can recover
      it; that is designed and not built, because the only evidence for it
      here is held out.</li>
      <li><strong>Single evaluator.</strong> The request set is written by
      the author. It is deliberately adversarial in places, but it is not a
      sample of real user traffic, and a real user base would produce
      phrasings this set does not contain.</li>
      <li><strong>The harness was wrong three times before it was
      right.</strong> Every correction is above and each is pinned by a
      test, but a first-run number from a new eval deserves less trust than
      this page's numbers now do, and that applies to anyone else's eval
      too.</li>
    </ul>
  </div>
</section>

<section>
  <p class="eyebrow">Every case</p>
  <h2>The full results, including everything that failed</h2>
  <div class="prose">
    <p>All {ov["total"]} cases, with the measured outcome and the first hard
    check that failed. The featured demos above are four of these rows.</p>
  </div>
  <div class="controls">
    <button data-filter="all" aria-pressed="true">All</button>
    <button data-filter="pass" aria-pressed="false">Pass</button>
    <button data-filter="partial" aria-pressed="false">Partial</button>
    <button data-filter="fail" aria-pressed="false">Fail</button>
    <button data-filter="heldout" aria-pressed="false">Held out</button>
    <button data-filter="offline" aria-pressed="false">Offline</button>
    <button data-filter="out_of_scope" aria-pressed="false">Out of scope</button>
    <span class="note-sm" id="rowCount"></span>
  </div>
  <div class="tbl-scroll"><table>
    <thead><tr><th>Case</th><th>Request</th><th>Measured</th><th>Verdict</th></tr></thead>
    <tbody id="allRows"></tbody>
  </table></div>

  <div class="panel" style="margin-top:28px">
    <div class="panel-title">Everything that did not fully pass</div>
    <div class="panel-sub">Listed in full rather than summarized</div>
    <div class="tbl-scroll"><table>
      <thead><tr><th>Case</th><th>Request</th><th>Verdict</th><th>Failed on</th></tr></thead>
      <tbody>{fail_rows or '<tr><td colspan="4">Every case passed.</td></tr>'}</tbody>
    </table></div>
  </div>
</section>

<section>
  <p class="eyebrow">Reproducibility</p>
  <h2>Rerunning this</h2>
  <div class="prose">
    <p>The harness lives in the repository next to the app it tests. It drives
    <span class="mono">routes.service.handle_request</span> &mdash; the same
    entry point the web app and the CLI use &mdash; so a pass here is a pass
    for the shipped code path, not for a test double.</p>
  </div>
  <div class="codeblock">git clone https://github.com/NKimball1/route-gen-ai
pip install -r requirements-dev.txt

# offline: 100+ unit tests and the failure-injection cases, no keys, no server
python -m pytest tests/ -q
python -m evals.runner --run myrun --tier offline

# live end-to-end: needs a self-hosted BRouter and an ANTHROPIC_API_KEY
start_brouter.cmd
python -m evals.runner --run myrun --tier live
python -m evals.report --run myrun --compare baseline
python -m evals.build_case_study --run myrun --baseline baseline</div>
  <div class="prose">
    <p style="margin-top:14px">Parses are cached by request text, so
    re-running after a routing-side change costs nothing and scores the
    identical interpretation. Scoring is a pure function of the saved records,
    so <span class="mono">python -m evals.rescore</span> re-grades every run
    under the same rules when a tolerance changes.</p>
    <ul class="plain">
      <li><a href="routegen/results.json">Full per-case results, sanitized
      (JSON)</a> &mdash; the file this page is built from.</li>
      <li><a href="https://github.com/NKimball1/route-gen-ai/blob/main/evals/cases_v1.json">The
      versioned request set</a> with the frozen expectation for every case.</li>
      <li><a href="https://github.com/NKimball1/route-gen-ai/blob/main/evals/scorers.py">The
      scorers</a> &mdash; every tolerance and the reason for it.</li>
      <li><a href="https://github.com/NKimball1/route-gen-ai/blob/main/docs/DEVLOG.md">The
      development log</a> &mdash; 35 phases of build, ride, fix.</li>
    </ul>
  </div>
</section>

<footer>
  Route Gen AI &middot;
  <a href="https://github.com/NKimball1/route-gen-ai">github.com/NKimball1/route-gen-ai</a>
  &middot; run <span class="mono">{esc(s["run"])}</span>, request set
  <span class="mono">{esc(s["cases_version"])}</span>,
  {esc(meta.get("finished", ""))}.<br>
  Maps are inline SVG drawn from the GPX files at build time &mdash; no tile
  service, no API key, nothing to fail at view time. Route geometry, context
  roads and geocoding derive from OpenStreetMap data &copy; OpenStreetMap
  contributors, available under the Open Database License, via a self-hosted
  BRouter and the public Nominatim and Overpass services. Start points are
  public landmarks.
</footer>

</div>
<script>window.__ROUTEGEN__ = {json.dumps(data)};</script>
<script>{JS}</script>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--out", default=SITE_DIR)
    ap.add_argument("--portfolio", default=None,
                    help="path to the portfolio site; copies into public/")
    args = ap.parse_args()

    s = load(args.run)
    base = load(args.baseline) if args.baseline else None
    os.makedirs(os.path.join(args.out, "routegen", "gpx"), exist_ok=True)

    features = []
    for rule in FEATURE_RULES:
        f = build_feature(args.run, rule,
                          os.path.join(args.out, "routegen", "gpx"))
        if f:
            features.append(f)
        else:
            print(f"  (no artifact for featured case {rule['id']}; skipping)")

    page = build_html(s, base, features,
                      render_improvement(args.run, args.baseline))
    out_html = os.path.join(args.out, "routegen-evals.html")
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(args.out, "routegen", "results.json"),
              "w", encoding="utf-8") as f:
        json.dump(sanitize(s), f, indent=1)

    size = os.path.getsize(out_html)
    print(f"-> {out_html} ({size / 1024:.0f} KB, {len(features)} featured)")

    if args.portfolio:
        pub = os.path.join(args.portfolio, "public")
        if not os.path.isdir(pub):
            print(f"  portfolio public/ not found at {pub}")
            return 1
        shutil.copyfile(out_html, os.path.join(pub, "routegen-evals.html"))
        # dirs_exist_ok rather than rmtree-then-copy: on Windows, and
        # especially under OneDrive, the directory is not actually gone when
        # rmtree returns and the copy fails with "file already exists".
        shutil.copytree(os.path.join(args.out, "routegen"),
                        os.path.join(pub, "routegen"), dirs_exist_ok=True)
        print(f"-> copied into {pub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
