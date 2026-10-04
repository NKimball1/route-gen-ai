"""Single-process web API. Session state and route history live in storage.

Run with one Uvicorn worker; the in-memory job registry deliberately does not
pretend to coordinate multiple processes. CLI and web use the same service.
"""
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from io import StringIO
from typing import Any, Iterator, Literal, TypedDict

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from routes import limits, storage
from routes.execution import Cancellation, JobCancelled
from routes.service import ServiceResult, _downsample, undo_request
from routes.spec import METERS_PER_FOOT, METERS_PER_MILE

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
app = FastAPI(title="Route Gen AI")
app.mount("/assets", StaticFiles(directory="static"), name="assets")
INVITE_CODE: str | None = os.environ.get("ROUTEGEN_INVITE_CODE")
JOBS: dict[str, "Job"] = {}
RUNNING: dict[str, str] = {}
LOCK = threading.RLock()
JOB_TTL_S = limits.HOUR_S
SESSIONS_ROOT = os.path.join("output", "sessions")
SID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
UPLOAD_MAX_MB = 8
UPLOAD_MAX_BYTES = UPLOAD_MAX_MB * 1024 * 1024


def client_ip(request: Request) -> str:
    # Uvicorn may resolve trusted proxy headers when explicitly configured.
    # Never trust an arbitrary forwarding header supplied by a client.
    return request.client.host if request.client else "unknown"


def session_dir(sid: str | None) -> str | None:
    if not sid or not SID_RE.fullmatch(sid):
        return None
    return os.path.join(SESSIONS_ROOT, sid)


@dataclass(frozen=True)
class Session:
    sid: str
    workdir: str


def authorized_session(x_session_id: str | None = Header(default=None),
                       x_invite_code: str | None = Header(default=None)) -> Session:
    workdir = session_dir(x_session_id)
    if workdir is None or x_session_id is None:
        raise HTTPException(400, "missing or invalid session id")
    if INVITE_CODE and x_invite_code != INVITE_CODE:
        raise HTTPException(401, "invite code required")
    return Session(x_session_id, workdir)


@contextmanager
def session_mutation(session: Session) -> Iterator[None]:
    with ExitStack() as stack:
        with LOCK:
            if session.sid in RUNNING:
                raise HTTPException(409, "A request is running. Wait for cancellation or completion first.")
            try:
                stack.enter_context(storage.transaction(session.workdir, blocking=False))
            except storage.SessionBusy as error:
                raise HTTPException(409, str(error)) from error
        yield


class JobLog(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.cancellation = Cancellation()

    @property
    def cancelled(self) -> bool:
        return self.cancellation.cancelled

    @cancelled.setter
    def cancelled(self, value: bool) -> None:
        if value:
            self.cancellation.cancel()

    def write(self, s: str) -> int:
        self.cancellation.check()
        return super().write(s)


class Job(TypedDict):
    status: str
    buf: JobLog
    result: ServiceResult | None
    sid: str
    ts: float


class Ask(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    start: str | None = Field(default=None, max_length=200)
    intent: Literal["auto", "route", "edit_route", "interval_spot"] = "auto"


def _run(job_id: str, text: str, start: str | None, workdir: str,
         intent: str = "auto") -> None:
    from routes.service import handle_request
    job = JOBS[job_id]
    t0 = time.time()
    try:
        path = storage.current_route(workdir)
        seen: set[str] = set()
        while path and path not in seen:
            if not storage.owned_path(path, workdir):
                raise ValueError("This legacy route references files outside the session. Upload the route to continue.")
            seen.add(path)
            path = storage.predecessor(path)
        result = handle_request(text, log_sink=job["buf"], default_address=start,
                                workdir=workdir, cancellation=job["buf"].cancellation,
                                intent=intent)
        job["buf"].cancellation.check()
        with LOCK:
            job["result"], job["status"] = result, "done"
    except JobCancelled:
        job["status"] = "cancelled"
    except Exception as error:
        # Error reporting must never raise another cancellation and strand a
        # job in 'running'. Unexpected failures are recorded, not committed.
        StringIO.write(job["buf"], f"\nERROR: {type(error).__name__}: {error}\n")
        job["status"] = "cancelled" if job["buf"].cancelled else "error"
    finally:
        with LOCK:
            if RUNNING.get(job["sid"]) == job_id:
                del RUNNING[job["sid"]]
        limits.log_event("ask_" + job["status"], sid=job["sid"],
                         seconds=round(time.time() - t0, 1))


@app.post("/api/ask", response_model=None)
def ask(body: Ask, request: Request, session: Session = Depends(authorized_session)) -> dict[str, str] | JSONResponse:
    job_id = uuid.uuid4().hex
    with LOCK:
        cutoff = time.time() - JOB_TTL_S
        for key in [key for key, value in JOBS.items()
                    if value["status"] != "running" and value["ts"] < cutoff]:
            del JOBS[key]
        if session.sid in RUNNING:
            return JSONResponse({"error": "A request is already running for this session."}, status_code=429)
        if sum(j["status"] == "running" for j in JOBS.values()) >= limits.Limits.JOBS_CONCURRENT:
            return JSONResponse({"error": "The server is busy - try again shortly."}, status_code=429)
        refusal = limits.check_ask(session.sid, client_ip(request))
        if refusal:
            return JSONResponse({"error": refusal}, status_code=429)
        RUNNING[session.sid] = job_id
        JOBS[job_id] = {"status": "running", "buf": JobLog(), "result": None,
                        "sid": session.sid, "ts": time.time()}
    limits.log_event("ask", sid=session.sid, ip=client_ip(request), text=body.text, start=body.start)
    threading.Thread(target=_run, args=(job_id, body.text, body.start, session.workdir, body.intent),
                     daemon=True).start()
    return {"job": job_id}


@app.post("/api/cancel", response_model=None)
def cancel(session: Session = Depends(authorized_session)) -> dict[str, str] | JSONResponse:
    with LOCK:
        job_id = RUNNING.get(session.sid)
        job = JOBS.get(job_id) if job_id else None
        if not job_id or not job or job["status"] != "running":
            return JSONResponse({"error": "nothing is running for this session"}, status_code=404)
        if not job["buf"].cancellation.cancel():
            return JSONResponse({"error": "The request has already committed its result."}, status_code=409)
    # The session remains reserved until the worker actually exits.
    return {"cancelled": job_id}


def _save_upload(data: bytes, filename: str, session: Session) -> dict[str, Any]:
    from routes.elevation import track_ascent
    from routes.gpx_in import parse_gpx_text
    from routes.editing import _cum
    from routes.gpx_out import write_track
    with session_mutation(session):
        try:
            points = parse_gpx_text(data.decode("utf-8-sig"), strict=True)
        except (ValueError, UnicodeError) as error:
            raise HTTPException(400, str(error)) from error
        if len(points) < 2:
            raise HTTPException(400, "No usable track/route points found in that file.")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(filename)[0])[:40] or "route"
        out = storage.artifact_path(session.workdir, f"upload_{safe}")
        miles = _cum(points)[-1] / METERS_PER_MILE
        ascent = track_ascent(points) / METERS_PER_FOOT
        missing_elevation = any(p[2] is None for p in points)
        warning = "Elevation is incomplete; climbing cannot be verified." if missing_elevation else ""
        write_track(points, safe, f"{miles:.1f} mi, {ascent:.0f} ft (uploaded)", out)
        storage.publish(out, session.workdir)
        return {"kind": "upload", "ok": "partial" if warning else True,
                "summary": "Uploaded - selected for editing." + (" " + warning if warning else ""),
                "candidates": [{"label": f"uploaded: {safe} - {miles:.1f} mi, {ascent:.0f} ft. {warning}",
                                "gpx": out, "latlngs": _downsample(points)}]}


@app.post("/api/upload", response_model=None)
async def upload(request: Request, file: UploadFile = File(...),
                 session: Session = Depends(authorized_session)) -> dict[str, Any] | JSONResponse:
    if not limits.allow(("upload", client_ip(request)), limits.Limits.UPLOAD_PER_IP_HOUR, limits.HOUR_S):
        return JSONResponse({"error": "too many uploads - slow down"}, status_code=429)
    try:
        data = await file.read(UPLOAD_MAX_BYTES + 1)
        if len(data) > UPLOAD_MAX_BYTES:
            return JSONResponse({"error": f"File too large ({UPLOAD_MAX_MB} MB max)."}, status_code=413)
        return await run_in_threadpool(_save_upload, data, file.filename or "route", session)
    finally:
        await file.close()


@app.get("/api/job/{job_id}", response_model=None)
def job(job_id: str, session: Session = Depends(authorized_session)) -> dict[str, Any] | JSONResponse:
    with LOCK:
        record = JOBS.get(job_id)
        if record is None or record["sid"] != session.sid:
            return JSONResponse({"error": "no such job"}, status_code=404)
        result: dict[str, Any] = {"status": record["status"], "log": record["buf"].getvalue()}
        if record["status"] == "done":
            result["result"] = dict(record["result"] or {})
            result["result"].pop("log", None)
        return result


@app.get("/api/gpx", response_model=None)
def gpx(path: str, session: Session = Depends(authorized_session)) -> FileResponse | JSONResponse:
    if not storage.owned_path(path, session.workdir):
        return JSONResponse({"error": "bad path"}, status_code=400)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="application/gpx+xml", filename=os.path.basename(path))


@app.post("/api/current", response_model=None)
def set_current(body: Ask, session: Session = Depends(authorized_session)) -> dict[str, str]:
    if not storage.owned_path(body.text, session.workdir) or not os.path.isfile(body.text):
        raise HTTPException(400, "bad path")
    with session_mutation(session):
        storage.select_route(body.text, session.workdir)
        storage.note_outcome(session.workdir, False)
    return {"current": body.text}


@app.get("/api/current", response_model=None)
def get_current(session: Session = Depends(authorized_session)) -> dict[str, Any]:
    current = storage.current_route(session.workdir)
    candidates = []
    if current and storage.owned_path(current, session.workdir):
        from routes.service import _route_row
        candidates = [_route_row(current)]
    return {"current": current if candidates else None, "candidates": candidates}


@app.post("/api/undo", response_model=None)
def undo(session: Session = Depends(authorized_session)) -> ServiceResult:
    with session_mutation(session):
        result = undo_request(session.workdir)
        # Old sessions could select files from the shared CLI directory.
        # Do not expose those files through a web session after migration.
        if any(not storage.owned_path(c["gpx"], session.workdir) for c in result.get("candidates", [])):
            raise HTTPException(400, "The legacy parent belongs outside this session. Upload it to use it here.")
        return result


@app.get("/api/health")
def health() -> JSONResponse:
    from routes.providers import BRouterProvider, brouter_reachable
    up = brouter_reachable(BRouterProvider().base_url)
    with LOCK:
        running = sum(j["status"] == "running" for j in JOBS.values())
    return JSONResponse({"ok": up, "brouter": "up" if up else "down", "jobs_running": running},
                        status_code=200 if up else 503)


@app.get("/api/geocode", response_model=None)
def api_geocode(q: str, request: Request,
                session: Session = Depends(authorized_session)) -> dict[str, Any] | JSONResponse:
    refusal = limits.check_geocode(client_ip(request))
    if refusal:
        return JSONResponse({"error": refusal}, status_code=429)
    from routes.geocode import geocode_flexible, GeocodeNotFound, GeocodeUnavailable
    try:
        lat, lon, name = geocode_flexible(q)
        return {"lat": lat, "lon": lon, "name": name}
    except GeocodeNotFound:
        return JSONResponse({"error": "Could not find that place."}, status_code=404)
    except GeocodeUnavailable:
        return JSONResponse({"error": "Address lookup is temporarily unavailable."}, status_code=503)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join("static", "index.html"))


# Text pages: every static/pages/<slug>.html is a real URL (/about, ...).
# Server-rendered static HTML with its own <title>/<meta> — what SEO wants.
# Adding a future page = dropping one file in that directory.
PAGES_DIR: str = os.path.join("static", "pages")
PAGE_RE: re.Pattern[str] = re.compile(r"^[a-z0-9-]{1,40}$")


@app.get("/{slug}", response_model=None)
def text_page(slug: str) -> FileResponse | JSONResponse:
    if PAGE_RE.match(slug):
        path = os.path.join(PAGES_DIR, f"{slug}.html")
        if os.path.exists(path):
            return FileResponse(path)
    return JSONResponse({"error": "not found"}, status_code=404)
