"""Web frontend for Route Gen AI.

  .venv\\Scripts\\python.exe -m uvicorn api:app --port 8903

One text box in, routes on a map out. Requests run as background jobs with
live log streaming; the same brain as ask.py (routes/service.py).
"""
import os
import sys
import threading
import uuid
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Route Gen AI")

JOBS: dict = {}  # id -> {"status", "buf", "result"}


class Ask(BaseModel):
    text: str


def _run(job_id: str, text: str) -> None:
    from routes.service import handle_request
    job = JOBS[job_id]
    try:
        result = handle_request(text, log_sink=job["buf"])
        job["result"] = result
        job["status"] = "done"
    except Exception as e:  # surfaced to the UI, not swallowed
        job["buf"].write(f"\nERROR: {type(e).__name__}: {e}\n")
        job["status"] = "error"


@app.post("/api/ask")
def ask(body: Ask):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "buf": StringIO(), "result": None}
    threading.Thread(target=_run, args=(job_id, body.text), daemon=True).start()
    return {"job": job_id}


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
def set_current(body: Ask):  # body.text = gpx path to make current
    norm = os.path.normpath(body.text)
    if norm.startswith("..") or os.path.isabs(norm) \
            or not norm.startswith("output") or not os.path.exists(norm):
        return JSONResponse({"error": "bad path"}, status_code=400)
    with open(os.path.join("output", "routes", "latest.txt"), "w") as f:
        f.write(norm)
    return {"current": norm}


@app.get("/api/current")
def get_current():
    from edit_route import current_route
    return {"current": current_route()}


@app.get("/")
def index():
    return FileResponse(os.path.join("static", "index.html"))
