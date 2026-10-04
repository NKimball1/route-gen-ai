"""One process-wide Nominatim request gate shared by forward/reverse lookup."""
import threading
import time

from routes.execution import checkpoint

NOMINATIM_INTERVAL_S = 1.1
_LOCK = threading.Lock()
_last_start = 0.0


def wait_for_nominatim() -> None:
    global _last_start
    with _LOCK:
        checkpoint()
        remaining = NOMINATIM_INTERVAL_S - (time.monotonic() - _last_start)
        if remaining > 0:
            time.sleep(remaining)
        checkpoint()
        _last_start = time.monotonic()
