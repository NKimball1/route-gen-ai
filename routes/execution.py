"""Cooperative cancellation with an indivisible commit boundary.

Only the application transaction may finish a token. Once committed, a
late cancel is refused rather than reporting a committed request cancelled.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from typing import Callable, Iterator


class JobCancelled(BaseException):
    """Unwind work without being swallowed by optional-provider guards."""


class Cancellation:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cancelled = False
        self.finished = False

    def cancel(self) -> bool:
        with self._lock:
            if self.finished:
                return False
            self.cancelled = True
            return True

    def check(self) -> None:
        with self._lock:
            if self.cancelled:
                raise JobCancelled()

    def commit(self, write: Callable[[], None]) -> None:
        with self._lock:
            self.check()
            write()
            self.finished = True


_CURRENT: ContextVar[Cancellation | None] = ContextVar("cancellation", default=None)


def checkpoint() -> None:
    token = _CURRENT.get()
    if token is not None:
        token.check()


@contextmanager
def execution_scope(token: Cancellation | None) -> Iterator[None]:
    binding = _CURRENT.set(token)
    try:
        checkpoint()
        yield
    finally:
        _CURRENT.reset(binding)
