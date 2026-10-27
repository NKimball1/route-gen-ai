"""Rate limiting and usage logging for the web API.

Session ids are client-generated (trivially forged), so limits apply per
session AND per IP, with a global daily cap as the cost backstop. All
tunable via env without code changes.
"""
import hashlib
import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any

LOCK = threading.Lock()
# limiter key, e.g. ("ask_ip", "203.0.113.7") -> timestamps of recent hits
_WINDOWS: dict[tuple[str, ...], deque[float]] = defaultdict(deque)

HOUR_S: int = 3600
DAY_S: int = 86400


class Limits:
    """Env-tunable knobs (defaults sized for a friendly beta)."""
    ASK_PER_SESSION_HOUR: int = int(os.environ.get("RATE_ASK_SESSION_HOUR", 12))
    ASK_PER_IP_HOUR: int = int(os.environ.get("RATE_ASK_IP_HOUR", 20))
    ASK_GLOBAL_DAY: int = int(os.environ.get("RATE_ASK_GLOBAL_DAY", 400))
    GEOCODE_PER_IP_HOUR: int = int(os.environ.get("RATE_GEOCODE_IP_HOUR", 30))
    UPLOAD_PER_IP_HOUR: int = int(os.environ.get("RATE_UPLOAD_IP_HOUR", 20))
    JOBS_CONCURRENT: int = int(os.environ.get("RATE_JOBS_CONCURRENT", 3))


def allow(key: tuple[str, ...], limit: int, period_s: float, now: float | None = None) -> bool:
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
    quotas = [
        (("ask_s", sid), Limits.ASK_PER_SESSION_HOUR, HOUR_S,
         "hourly limit reached for this session - take a break"),
        (("ask_ip", ip), Limits.ASK_PER_IP_HOUR, HOUR_S,
         "hourly limit reached - take a break"),
        (("ask_g",), Limits.ASK_GLOBAL_DAY, DAY_S,
         "daily capacity reached - try again tomorrow"),
    ]
    now = time.time()
    with LOCK:
        for key, limit, period, refusal in quotas:
            window = _WINDOWS[key]
            while window and window[0] <= now - period:
                window.popleft()
            if len(window) >= limit:
                return refusal
        for key, _, _, _ in quotas:
            _WINDOWS[key].append(now)
    return None


def check_geocode(ip: str) -> str | None:
    if not allow(("geo", ip), Limits.GEOCODE_PER_IP_HOUR, HOUR_S):
        return "too many lookups — slow down"
    return None


# ---- usage log: one JSON line per event, greppable, gitignored ----

USAGE_LOG: str = os.path.join("output", "usage.jsonl")
# Past this size the log moves aside to usage.jsonl.1 (replacing the older
# copy), so disk use and how long typed text is kept both stay bounded.
# 0 keeps everything in one file.
USAGE_LOG_MAX_BYTES: int = int(float(os.environ.get("ROUTEGEN_USAGE_LOG_MB", 20)) * 1_000_000)
LOG_TEXT: bool = os.environ.get("ROUTEGEN_LOG_TEXT", "1") == "1"
# Everything a rider typed: the request and the start address (often home).
TYPED_FIELDS: tuple[str, ...] = ("text", "start")
# IPs are pseudonymized before they touch disk: a salted hash still lets
# you count distinct visitors and spot one abuser, without keeping a
# list of who they were. Set a secret salt in production; with the
# empty default, IPv4 hashes are brute-forceable (4 billion inputs).
IP_SALT: str = os.environ.get("ROUTEGEN_IP_SALT", "")


def pseudonymize_ip(ip: str) -> str:
    return hashlib.sha256((IP_SALT + ip).encode()).hexdigest()[:16]


def _rotate_usage_log() -> None:
    """Move a full log aside (caller holds LOCK). A rename that fails --
    e.g. the file held open by a reader on Windows -- only delays it."""
    try:
        if USAGE_LOG_MAX_BYTES and os.path.getsize(USAGE_LOG) >= USAGE_LOG_MAX_BYTES:
            os.replace(USAGE_LOG, USAGE_LOG + ".1")
    except OSError:
        pass


def log_event(event: str, **fields: Any) -> None:
    """Append a usage event. Never raises — logging must not break serving."""
    try:
        if not LOG_TEXT:
            for name in TYPED_FIELDS:
                fields.pop(name, None)
        if fields.get("ip"):
            fields["ip"] = pseudonymize_ip(fields["ip"])
        row = {"ts": round(time.time(), 3), "event": event, **fields}
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with LOCK:
            _rotate_usage_log()
            with open(USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
