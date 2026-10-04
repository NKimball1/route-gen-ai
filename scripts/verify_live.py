"""Opt-in live HTTP smoke test using public landmarks and isolated sessions.

Run from the repository: python scripts/verify_live.py
Requires the configured parser key and BRouter. Calls the real parser/router,
writes a JSON report plus GPX evidence under output/review-validation, and never
modifies an existing browser session. This is separate from offline pytest.
"""
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from fastapi.testclient import TestClient
import api
from routes.gpx_in import parse_gpx_text
from evals.geo import track_length_m
from routes.constraints import resolve_avoid
from routes.road_avoid import on_road_meters
from routes.policy import AVOID_ROAD_TOLERANCE_M
from routes.spec import METERS_PER_MILE

PUBLIC_START = "Monona Terrace, Madison WI"
JOB_TIMEOUT_S = 360
POLL_INTERVAL_S = 0.2
DISTANCE_TOLERANCE = 0.15
GEOMETRY_TOLERANCE = 0.01


def gpx_distance(data: bytes) -> float:
    """Independent measurement of the exported horizontal geometry."""
    return track_length_m([point[:2] for point in parse_gpx_text(data.decode())])


def main() -> int:
    root = Path("output") / "review-validation" / time.strftime("%Y%m%d-%H%M%S")
    root.mkdir(parents=True, exist_ok=False)
    api.SESSIONS_ROOT = str(root / "sessions")
    sid = str(uuid.uuid4())
    headers = {"X-Session-Id": sid}
    if api.INVITE_CODE:
        headers["X-Invite-Code"] = api.INVITE_CODE
    records: list[dict[str, Any]] = []
    failures: list[str] = []

    with TestClient(api.app) as client:
        def check(condition: bool, description: str) -> None:
            print(f"{'PASS' if condition else 'FAIL'}: {description}", flush=True)
            if not condition:
                failures.append(description)

        def ask(text: str, intent: str = "auto") -> dict[str, Any]:
            print("Request: " + text, flush=True)
            response = client.post("/api/ask", headers=headers,
                                   json={"text": text, "start": PUBLIC_START, "intent": intent})
            response.raise_for_status()
            job = response.json()["job"]
            deadline = time.monotonic() + JOB_TIMEOUT_S
            while time.monotonic() < deadline:
                response = client.get(f"/api/job/{job}", headers=headers)
                response.raise_for_status()
                record = response.json()
                if record["status"] != "running":
                    records.append({"text": text, **record})
                    check(record["status"] == "done", "job completed: " + text)
                    result: dict[str, Any] = record.get("result") or {}
                    print(result.get("summary", record.get("log", "")), flush=True)
                    return result
                time.sleep(POLL_INTERVAL_S)
            client.post("/api/cancel", headers=headers)
            raise TimeoutError("The live verification job exceeded its time budget.")

        def download(result: dict[str, Any]) -> tuple[str, bytes]:
            candidates = result.get("candidates") or []
            if not candidates:
                failures.append("Expected a usable route: " + result.get("summary", ""))
                return "", b""
            path = candidates[0]["gpx"]
            response = client.get("/api/gpx", headers=headers, params={"path": path})
            response.raise_for_status()
            check(len(parse_gpx_text(response.text)) > 1, "download is valid GPX")
            return path, response.content

        try:
            first_result = ask("Give me a new 20 mile loop from home", "route")
            first, contents = download(first_result)
            if first:
                miles = gpx_distance(contents) / METERS_PER_MILE
                check(abs(miles - 20) / 20 <= DISTANCE_TOLERANCE, "exported route is within distance tolerance")
                second, _ = download(ask("Give me another new 20 mile loop from home", "route"))
                check(first != second, "generations have different immutable paths")
                original = client.get("/api/gpx", headers=headers, params={"path": first}).content
                check(hashlib.sha256(original).digest() == hashlib.sha256(contents).digest(), "previous generation is unchanged")
                undone = client.post("/api/undo", headers=headers).json()
                check(bool(undone.get("ok")) and undone["candidates"][0]["gpx"] == first, "undo restores the previous generation")
                edited = ask("Make it 25 miles total", "edit_route")
                check(edited.get("kind") == "edit", "total distance is interpreted as an edit")
                _, edited_gpx = download(edited)
                if edited_gpx:
                    miles = gpx_distance(edited_gpx) / METERS_PER_MILE
                    check(abs(miles - 25) / 25 <= DISTANCE_TOLERANCE, "edited GPX meets the new total distance")
                via = ask("Route me through Vilas Park and then the Arboretum", "edit_route")
                check(via.get("kind") == "edit", "ordered-waypoint request does not replace the ride")
                check(via.get("ok") is True, "the live ordered-waypoint edit is fully satisfied")
                download(via)

            outback = ask("Give me a new 20 mile out and back from home, maximize climbing", "route")
            download(outback)
            check(bool(outback.get("candidates")) and all("outback" in c["label"] for c in outback["candidates"]),
                  "explicit out-and-back retains its shape")
            spots = ask("Find a flat stretch near home for 4x4 at 285 watts within 20 minutes, rider plus bike 90 kg", "interval_spot")
            _, spot_gpx = download(spots)
            if spot_gpx:
                metrics = spots["candidates"][0]["metrics"]
                measured = gpx_distance(spot_gpx)
                check(abs(measured - metrics["distance_m"]) < measured * GEOMETRY_TOLERANCE, "interval GPX matches the scored road distance")
                check(metrics["seconds_out"] is not None, "power sizing reaches the result")
            avoided = ask("Give me a new 20 mile loop from home without riding Monroe Street, Madison WI", "route")
            _, avoided_gpx = download(avoided)
            if avoided_gpx:
                roads = resolve_avoid(["Monroe Street, Madison WI"]).roads
                points = parse_gpx_text(avoided_gpx.decode())
                check(bool(roads) and all(on_road_meters(points, road.ways) <= AVOID_ROAD_TOLERANCE_M for road in roads),
                      "downloaded new route respects the named-road exclusion")
        except Exception as error:
            failures.append(f"Verification stopped: {type(error).__name__}: {error}")
            raise
        finally:
            report = {"public_start": PUBLIC_START, "failures": failures, "records": records}
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("Report: " + str(root / "report.json"), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
