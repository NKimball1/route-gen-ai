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

from fastapi import FastAPI, Header
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Route Gen AI")

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
    text: str
    start: str | None = None  # the user's starting point (address string)


def _run(job_id: str, text: str, start: str | None, workdir: str) -> None:
    from routes.service import handle_request
    job = JOBS[job_id]
    try:
        result = handle_request(text, log_sink=job["buf"],
                                default_address=start, workdir=workdir)
        job["result"] = result
        job["status"] = "done"
    except Exception as e:  # surfaced to the UI, not swallowed
        job["buf"].write(f"\nERROR: {type(e).__name__}: {e}\n")
        job["status"] = "error"
    finally:
        with LOCK:
            if RUNNING.get(job["sid"]) == job_id:
                del RUNNING[job["sid"]]


@app.post("/api/ask")
def ask(body: Ask, x_session_id: str | None = Header(default=None)):
    workdir = session_dir(x_session_id)
    if workdir is None:
        return JSONResponse({"error": "missing or invalid session id"},
                            status_code=400)
    job_id = uuid.uuid4().hex[:12]
    with LOCK:
        running = RUNNING.get(x_session_id)
        if running and JOBS.get(running, {}).get("status") == "running":
            return JSONResponse(
                {"error": "a request is already running for this session — "
                          "wait for it to finish"},
                status_code=429)
        RUNNING[x_session_id] = job_id
        JOBS[job_id] = {"status": "running", "buf": StringIO(),
                        "result": None, "sid": x_session_id}
    threading.Thread(target=_run,
                     args=(job_id, body.text, body.start, workdir),
                     daemon=True).start()
    return {"job": job_id}


@app.get("/api/geocode")
def api_geocode(q: str):
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
            or not norm.startswith("output") or not norm.endswith(".gpx"):
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
