"""Rate limiting and usage logging for the web API.

Session ids are client-generated (trivially forged), so limits apply per
session AND per IP, with a global daily cap as the cost backstop. All
tunable via env without code changes.
"""
import json
import os
import threading
import time
from collections import defaultdict, deque

LOCK = threading.Lock()
_WINDOWS: dict = defaultdict(deque)

HOUR_S = 3600
DAY_S = 86400


class Limits:
    """Env-tunable knobs (defaults sized for a friendly beta)."""
    ASK_PER_SESSION_HOUR = int(os.environ.get("RATE_ASK_SESSION_HOUR", 12))
    ASK_PER_IP_HOUR = int(os.environ.get("RATE_ASK_IP_HOUR", 20))
    ASK_GLOBAL_DAY = int(os.environ.get("RATE_ASK_GLOBAL_DAY", 400))
    GEOCODE_PER_IP_HOUR = int(os.environ.get("RATE_GEOCODE_IP_HOUR", 30))
    UPLOAD_PER_IP_HOUR = int(os.environ.get("RATE_UPLOAD_IP_HOUR", 20))
    JOBS_CONCURRENT = int(os.environ.get("RATE_JOBS_CONCURRENT", 3))


def allow(key, limit: int, period_s: float, now: float | None = None) -> bool:
    """Sliding-window check: True and records the hit if under limit."""
    now = time.time() if now is None else now
    with LOCK:
        dq = _WINDOWS[key]
        while dq and dq[0] <= now - period_s:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def check_ask(sid: str, ip: str) -> str | None:
    """None if allowed, else a human-readable refusal."""
    if not allow(("ask_g",), Limits.ASK_GLOBAL_DAY, DAY_S):
        return "daily capacity reached — try again tomorrow"
    if not allow(("ask_s", sid), Limits.ASK_PER_SESSION_HOUR, HOUR_S):
        return "hourly limit reached for this session — take a break"
    if not allow(("ask_ip", ip), Limits.ASK_PER_IP_HOUR, HOUR_S):
        return "hourly limit reached — take a break"
    return None


def check_geocode(ip: str) -> str | None:
    if not allow(("geo", ip), Limits.GEOCODE_PER_IP_HOUR, HOUR_S):
        return "too many lookups — slow down"
    return None


# ---- usage log: one JSON line per event, greppable, gitignored ----

USAGE_LOG = os.path.join("output", "usage.jsonl")
LOG_TEXT = os.environ.get("ROUTEGEN_LOG_TEXT", "1") == "1"


def log_event(event: str, **fields) -> None:
    """Append a usage event. Never raises — logging must not break serving."""
    try:
        if not LOG_TEXT:
            fields.pop("text", None)
        row = {"ts": round(time.time(), 3), "event": event, **fields}
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with LOCK:
            with open(USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
