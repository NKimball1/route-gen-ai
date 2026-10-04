"""Immutable artifacts and atomic session history, shared by CLI and API.

Long-running requests stage selection/history changes in memory. One atomic
JSON replacement commits them only after all work and cancellation checks.
Legacy latest.txt/lineage.json sessions are read on first use. latest.txt is
maintained as a compatibility mirror; state.json is authoritative thereafter.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterator
import uuid
from weakref import WeakValueDictionary

from routes.execution import Cancellation

DEFAULT_WORKDIR = os.path.join("output", "routes")
STATE_FILE = "state.json"
_LOCKS: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


class SessionBusy(Exception):
    """Another operation is using the selected route."""


def _key(workdir: str) -> str:
    return os.path.normcase(os.path.abspath(workdir))


def _lock(workdir: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(_key(workdir), threading.RLock())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _recover(root: Path, state_path: Path) -> dict[str, Any]:
    """A damaged state.json must not lock the rider out of their session.

    Set the damaged file aside for diagnosis (never delete it), keep the
    current route from the latest.txt mirror, and start fresh history.
    Stale legacy lineage.json is deliberately not resurrected: undo simply
    has nothing to step back to until new edits are made.
    """
    kept = state_path.with_name(f"{STATE_FILE}.damaged-{uuid.uuid4().hex[:8]}")
    try:
        os.replace(state_path, kept)
    except OSError:
        pass  # another reader already moved it
    try:
        current = (root / "latest.txt").read_text(encoding="utf-8-sig").strip() or None
    except OSError:
        current = None
    state: dict[str, Any] = {"current": current, "parents": {},
                             "last_edit_changed": True}
    try:
        _atomic_text(state_path, json.dumps(state, indent=2))
    except OSError:
        pass
    print(f"Route history was damaged and has been reset; the damaged file "
          f"was kept as {kept.name}. The current route is unchanged; undo "
          "history starts over.")
    return state


def _load(workdir: str) -> dict[str, Any]:
    root = Path(workdir)
    state_path = root / STATE_FILE
    if state_path.exists():
        state = _read_json(state_path)
        if not isinstance(state.get("parents"), dict) or "current" not in state:
            return _recover(root, state_path)
        return state
    try:
        current = (root / "latest.txt").read_text(encoding="utf-8-sig").strip()
    except OSError:
        current = None
    try:
        changed = (root / "last_edit_outcome.txt").read_text().strip() != "unchanged"
    except OSError:
        changed = True
    return {"current": current, "parents": _read_json(root / "lineage.json"),
            "last_edit_changed": changed}


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class _Transaction:
    workdir: str
    state: dict[str, Any]
    dirty: bool = False

    def commit(self) -> None:
        if not self.dirty:
            return
        root = Path(self.workdir)
        _atomic_text(root / STATE_FILE, json.dumps(self.state, indent=2))
        # A mirror failure must not turn an already committed transaction
        # into an apparent failure. Application readers use STATE_FILE.
        try:
            _atomic_text(root / "latest.txt", self.state.get("current") or "")
        except OSError:
            pass


_ACTIVE: ContextVar[_Transaction | None] = ContextVar("route_transaction", default=None)


@contextmanager
def transaction(workdir: str = DEFAULT_WORKDIR, *, blocking: bool = True,
                cancellation: Cancellation | None = None) -> Iterator[None]:
    active = _ACTIVE.get()
    if active is not None:
        if _key(active.workdir) != _key(workdir):
            raise ValueError("Cannot change two sessions in one transaction")
        yield
        return
    lock = _lock(workdir)
    if not lock.acquire(blocking=blocking):
        raise SessionBusy("A request is running. Wait for it to finish or cancel it first.")
    binding = None
    try:
        tx = _Transaction(workdir, _load(workdir))
        binding = _ACTIVE.set(tx)
        yield
        if cancellation is None:
            tx.commit()
        else:
            cancellation.commit(tx.commit)
    finally:
        if binding is not None:
            _ACTIVE.reset(binding)
        lock.release()


def _state(workdir: str) -> dict[str, Any]:
    tx = _ACTIVE.get()
    return tx.state if tx and _key(tx.workdir) == _key(workdir) else _load(workdir)


def current_route(workdir: str = DEFAULT_WORKDIR) -> str | None:
    path = _state(workdir).get("current")
    return path if isinstance(path, str) and os.path.isfile(path) else None


def _change(workdir: str, **fields: Any) -> None:
    with transaction(workdir):
        tx = _ACTIVE.get()
        assert tx is not None
        tx.state.update(fields)
        tx.dirty = True


def select_route(path: str, workdir: str = DEFAULT_WORKDIR) -> None:
    if not os.path.isfile(path):
        raise ValueError("That route file no longer exists.")
    _change(workdir, current=path)


def note_outcome(workdir: str, changed: bool) -> None:
    _change(workdir, last_edit_changed=changed)


def last_edit_changed(workdir: str) -> bool:
    return bool(_state(workdir).get("last_edit_changed", True))


def record_parent(out_path: str, parent_path: str) -> None:
    workdir = os.path.dirname(out_path) or "."
    with transaction(workdir):
        parents = dict(_state(workdir)["parents"])
        name = os.path.basename(out_path)
        if name in parents and parents[name] != parent_path:
            raise ValueError("An immutable route cannot acquire a different parent.")
        parents[name] = parent_path
        _change(workdir, parents=parents)


def predecessor(route_path: str) -> str | None:
    workdir = os.path.dirname(route_path) or "."
    parent = _state(workdir)["parents"].get(os.path.basename(route_path))
    if parent:
        full = parent if os.path.dirname(parent) else os.path.join(workdir, parent)
        return full if os.path.isfile(full) else None
    # Legacy edit numbers remain readable; new routes always record lineage.
    base, ext = os.path.splitext(route_path)
    stem, sep, number = base.rpartition("_edit")
    if sep and number.isdigit():
        for n in range(int(number) - 1, 0, -1):
            candidate = f"{stem}_edit{n}{ext}"
            if os.path.isfile(candidate):
                return candidate
        if os.path.isfile(stem + ext):
            return stem + ext
    return None


def artifact_path(workdir: str, stem: str, suffix: str = ".gpx") -> str:
    os.makedirs(workdir, exist_ok=True)
    return os.path.join(workdir, f"{stem}_{uuid.uuid4().hex}{suffix}")


def publish(path: str, workdir: str = DEFAULT_WORKDIR) -> None:
    """Select a new artifact while preserving the previous selection."""
    with transaction(workdir):
        previous = current_route(workdir)
        if previous and previous != path:
            record_parent(path, previous)
        select_route(path, workdir)
        note_outcome(workdir, True)


def undo(workdir: str) -> str | None:
    with transaction(workdir):
        current = current_route(workdir)
        parent = predecessor(current) if current else None
        if parent:
            select_route(parent, workdir)
            note_outcome(workdir, False)
        return parent


def owned_path(path: str, workdir: str) -> bool:
    """Containment after resolving traversal and symlinks, on every OS."""
    root = Path(workdir).resolve()
    target = Path(path).resolve()
    return target.is_relative_to(root) and target.suffix.lower() == ".gpx"
