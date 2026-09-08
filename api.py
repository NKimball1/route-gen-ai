"""Web frontend for Route Gen AI.

  .venv\\Scripts\\python.exe -m uvicorn api:app --port 8903

One text box in, routes on a map out. Requests run as background jobs with
live log streaming; the same brain as ask.py (routes/service.py).
"""
import os
import re
import shutil
import sys
import threading
import time
import uuid
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from routes import limits

app = FastAPI(title="Route Gen AI")
app.mount("/assets", StaticFiles(directory="static"), name="assets")

# Optional shared invite code: set ROUTEGEN_INVITE_CODE to gate the
# expensive endpoint; unset = open (local/dev).
INVITE_CODE = os.environ.get("ROUTEGEN_INVITE_CODE")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:  # trust only behind your own reverse proxy
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

JOBS: dict = {}          # job id -> {"status", "buf", "result", "sid"}
RUNNING: dict = {}       # session id -> job id currently running
LOCK = threading.Lock()

SESSIONS_ROOT = os.path.join("output", "sessions")
SID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SESSION_MAX_AGE_S = 7 * 86400


def session_dir(sid: str | None) -> str | None:
    """Per-session workspace: each browser session's GPX files and
    current-route pointer live here so concurrent users never collide."""
    if not sid or not SID_RE.match(sid):
        return None
    d = os.path.join(SESSIONS_ROOT, sid)
    os.makedirs(d, exist_ok=True)
    return d


def _sweep_stale_sessions() -> None:
    if not os.path.isdir(SESSIONS_ROOT):
        return
    cutoff = time.time() - SESSION_MAX_AGE_S
    for name in os.listdir(SESSIONS_ROOT):
        d = os.path.join(SESSIONS_ROOT, name)
        try:
            if os.path.isdir(d) and os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


_sweep_stale_sessions()


class Ask(BaseModel):
    # caps: a request is a sentence, not a document (cost + abuse bound)
    text: str = Field(max_length=600)
    start: str | None = Field(default=None, max_length=200)


def _run(job_id: str, text: str, start: str | None, workdir: str) -> None:
    from routes.service import handle_request
    job = JOBS[job_id]
    t0 = time.time()
    try:
        result = handle_request(text, log_sink=job["buf"],
                                default_address=start, workdir=workdir)
        job["result"] = result
        job["status"] = "done"
        limits.log_event("ask_done", sid=job["sid"], kind=result.get("kind"),
                         candidates=len(result.get("candidates", [])),
                         seconds=round(time.time() - t0, 1))
    except Exception as e:  # surfaced to the UI, not swallowed
        job["buf"].write(f"\nERROR: {type(e).__name__}: {e}\n")
        job["status"] = "error"
        limits.log_event("ask_error", sid=job["sid"],
                         error=f"{type(e).__name__}: {e}",
                         seconds=round(time.time() - t0, 1))
    finally:
        with LOCK:
            if RUNNING.get(job["sid"]) == job_id:
                del RUNNING[job["sid"]]


@app.post("/api/ask")
def ask(body: Ask, request: Request,
        x_session_id: str | None = Header(default=None),
        x_invite_code: str | None = Header(default=None)):
    workdir = session_dir(x_session_id)
    if workdir is None:
        return JSONResponse({"error": "missing or invalid session id"},
                            status_code=400)
    if INVITE_CODE and x_invite_code != INVITE_CODE:
        return JSONResponse({"error": "invite code required"}, status_code=401)
    ip = client_ip(request)
    refusal = limits.check_ask(x_session_id, ip)
    if refusal:
        limits.log_event("ask_limited", sid=x_session_id, ip=ip, why=refusal)
        return JSONResponse({"error": refusal}, status_code=429)
    job_id = uuid.uuid4().hex[:12]
    with LOCK:
        # evict finished jobs older than an hour — JOBS grew forever
        cutoff = time.time() - 3600
        for jid in [j for j, v in JOBS.items()
                    if v["status"] != "running" and v.get("ts", 0) < cutoff]:
            del JOBS[jid]
        running = RUNNING.get(x_session_id)
        if running and JOBS.get(running, {}).get("status") == "running":
            return JSONResponse(
                {"error": "a request is already running for this session — "
                          "wait for it to finish"},
                status_code=429)
        busy = sum(1 for j in JOBS.values() if j["status"] == "running")
        if busy >= limits.Limits.JOBS_CONCURRENT:
            return JSONResponse(
                {"error": "the server is busy — try again in a minute"},
                status_code=429)
        RUNNING[x_session_id] = job_id
        JOBS[job_id] = {"status": "running", "buf": StringIO(),
                        "result": None, "sid": x_session_id,
                        "ts": time.time()}
    limits.log_event("ask", sid=x_session_id, ip=ip, text=body.text,
                     start=body.start)
    threading.Thread(target=_run,
                     args=(job_id, body.text, body.start, workdir),
                     daemon=True).start()
    return {"job": job_id}


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...),
                 x_session_id: str | None = Header(default=None),
                 x_invite_code: str | None = Header(default=None)):
    """Upload an existing GPX; it becomes the session's current route, so
    every edit ('avoid that road', 'make it longer', ...) works on it."""
    workdir = session_dir(x_session_id)
    if workdir is None:
        return JSONResponse({"error": "missing or invalid session id"},
                            status_code=400)
    if INVITE_CODE and x_invite_code != INVITE_CODE:
        return JSONResponse({"error": "invite code required"}, status_code=401)
    if not limits.allow(("upload", client_ip(request)), 20, 3600):
        return JSONResponse({"error": "too many uploads — slow down"},
                            status_code=429)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        return JSONResponse({"error": "file too large (8 MB max)"},
                            status_code=413)
    from routes.elevation import track_ascent
    from routes.preview import parse_gpx_text
    from routes.editing import _cum
    from routes.gpx_out import write_track
    from routes.service import _downsample
    points = parse_gpx_text(data.decode("utf-8", errors="ignore"))
    if len(points) < 2:
        return JSONResponse(
            {"error": "no track/route points found in that file"},
            status_code=400)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_",
                  os.path.splitext(file.filename or "route")[0])[:40] or "route"
    out = os.path.join(workdir, f"upload_{safe}.gpx")
    dist_mi = _cum(points)[-1] / 1609.344
    ascent_ft = track_ascent(points) / 0.3048
    ele_note = "" if any(p[2] is not None for p in points) else \
        " — no elevation data in this file; climbing will read low until an edit adds routed legs"
    # rewriting through write_track normalizes the format and drops
    # timestamps/HR/extensions the original may carry (privacy win)
    write_track(points, safe, f"{dist_mi:.1f} mi, {ascent_ft:.0f} ft (uploaded)",
                out)
    with open(os.path.join(workdir, "latest.txt"), "w") as f:
        f.write(out)
    limits.log_event("upload", sid=x_session_id, ip=client_ip(request),
                     name=safe, points=len(points), miles=round(dist_mi, 1))
    return {"kind": "upload", "candidates": [{
        "label": f"uploaded: {safe} — {dist_mi:.1f} mi, {ascent_ft:.0f} ft"
                 + ele_note,
        "gpx": out, "latlngs": _downsample(points),
    }]}


@app.get("/api/geocode")
def api_geocode(q: str, request: Request):
    refusal = limits.check_geocode(client_ip(request))
    if refusal:
        return JSONResponse({"error": refusal}, status_code=429)
    from routes.geocode import geocode_flexible
    try:
        lat, lon, name = geocode_flexible(q)
        return {"lat": lat, "lon": lon, "name": name}
    except ValueError:
        return JSONResponse({"error": f"could not find {q!r}"}, status_code=404)


@app.get("/api/job/{job_id}")
def job(job_id: str):
    j = JOBS.get(job_id)
    if j is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    out = {"status": j["status"], "log": j["buf"].getvalue()}
    if j["status"] == "done":
        result = dict(j["result"])
        result.pop("log", None)
        out["result"] = result
    return out


@app.get("/api/gpx")
def gpx(path: str):
    # only serve GPX files from our own output tree
    norm = os.path.normpath(path)
    if norm.startswith("..") or os.path.isabs(norm) \
            or not norm.startswith("output" + os.sep)             or not norm.endswith(".gpx"):
        return JSONResponse({"error": "bad path"}, status_code=400)
    if not os.path.exists(norm):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(norm, media_type="application/gpx+xml",
                        filename=os.path.basename(norm))


@app.post("/api/current")
def set_current(body: Ask, x_session_id: str | None = Header(default=None)):
    workdir = session_dir(x_session_id)
    if workdir is None:
        return JSONResponse({"error": "missing or invalid session id"},
                            status_code=400)
    norm = os.path.normpath(body.text)
    # a session may only select its own files (or the shared CLI dir)
    allowed = (norm.startswith(os.path.normpath(workdir) + os.sep)
               or norm.startswith(os.path.join("output", "routes")))
    if norm.startswith("..") or os.path.isabs(norm) or not allowed \
            or not norm.endswith(".gpx") or not os.path.exists(norm):
        return JSONResponse({"error": "bad path"}, status_code=400)
    with open(os.path.join(workdir, "latest.txt"), "w") as f:
        f.write(norm)
    return {"current": norm}


@app.post("/api/undo")
def undo(x_session_id: str | None = Header(default=None)):
    """Step the session's current route back one version (no LLM call)."""
    workdir = session_dir(x_session_id)
    if workdir is None:
        return JSONResponse({"error": "missing or invalid session id"},
                            status_code=400)
    from edit_route import current_route, predecessor
    from routes.preview import _parse_desc, _parse_gpx
    from routes.service import _downsample
    cur = current_route(workdir)
    prev = cur and predecessor(cur)
    if not prev:
        return JSONResponse(
            {"ok": False, "summary": "Nothing to undo — this is the "
                                     "earliest version.", "candidates": []})
    with open(os.path.join(workdir, "latest.txt"), "w") as f:
        f.write(prev)
    limits.log_event("undo", sid=x_session_id, to=os.path.basename(prev))
    return {"ok": True, "summary": f"Undone — back to {os.path.basename(prev)}.",
            "candidates": [{
                "label": f"{os.path.basename(prev)} — {_parse_desc(prev)}",
                "gpx": prev, "latlngs": _downsample(_parse_gpx(prev)),
            }]}


@app.get("/api/current")
def get_current(x_session_id: str | None = Header(default=None)):
    workdir = session_dir(x_session_id)
    if workdir is None:
        return {"current": None}
    from edit_route import current_route
    return {"current": current_route(workdir)}


@app.get("/")
def index():
    return FileResponse(os.path.join("static", "index.html"))


# Text pages: every static/pages/<slug>.html is a real URL (/about, ...).
# Server-rendered static HTML with its own <title>/<meta> — what SEO wants.
# Adding a future page = dropping one file in that directory.
PAGES_DIR = os.path.join("static", "pages")
PAGE_RE = re.compile(r"^[a-z0-9-]{1,40}$")


@app.get("/{slug}")
def text_page(slug: str):
    if PAGE_RE.match(slug):
        path = os.path.join(PAGES_DIR, f"{slug}.html")
        if os.path.exists(path):
            return FileResponse(path)
    return JSONResponse({"error": "not found"}, status_code=404)
