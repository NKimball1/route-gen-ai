"""Job cancellation (api.py): the log buffer is the cancellation point."""
import threading
import time

import pytest

import api
from api import JobCancelled, JobLog


def test_cancelled_buffer_raises_on_next_write():
    buf = JobLog()
    buf.write("progress\n")
    buf.cancelled = True
    with pytest.raises(JobCancelled):
        buf.write("more\n")
    assert buf.getvalue() == "progress\n"  # what was logged stays readable


def test_cancel_signal_passes_through_except_exception():
    buf = JobLog()
    buf.cancelled = True
    with pytest.raises(JobCancelled):
        try:
            buf.write("x")
        except Exception:  # the pipeline's "never fatal" guards
            pytest.fail("a broad except swallowed the cancellation")


def test_running_job_unwinds_and_reports_cancelled(monkeypatch):
    from routes import service

    def chatty_pipeline(text, log_sink=None, **kw):
        for _ in range(500):
            log_sink.write("tick\n")
            time.sleep(0.005)
        return {"kind": "route", "candidates": []}

    monkeypatch.setattr(service, "handle_request", chatty_pipeline)
    monkeypatch.setattr(api.limits, "log_event", lambda *a, **k: None)
    job_id = "test-cancel"
    api.JOBS[job_id] = {"status": "running", "buf": JobLog(), "result": None,
                        "sid": "sid-1", "ts": time.time()}
    api.RUNNING["sid-1"] = job_id
    t = threading.Thread(target=api._run, args=(job_id, "x", None, "."),
                         daemon=True)
    t.start()
    time.sleep(0.05)
    api.JOBS[job_id]["buf"].cancelled = True
    t.join(timeout=2)
    assert not t.is_alive()
    assert api.JOBS[job_id]["status"] == "cancelled"
    assert api.JOBS[job_id]["result"] is None
    assert "sid-1" not in api.RUNNING
    del api.JOBS[job_id]
