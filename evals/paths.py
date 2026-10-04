"""Repo-relative paths in saved results.

Records used to carry absolute Windows paths, which meant every result file
published the machine's username and none of them resolved on anyone else's
checkout. Paths are stored relative to the repository root, with forward
slashes, and resolved back when a file actually has to be opened.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Absolute paths that leaked into free-text progress logs, too.
_ABS_RE = re.compile(re.escape(ROOT) + r"[\\/]?", re.IGNORECASE)
_ABS_RE_FWD = re.compile(re.escape(ROOT.replace("\\", "/")) + r"/?",
                         re.IGNORECASE)


def rel(path: str | None) -> str | None:
    """Repo-relative, POSIX-separated. Leaves anything outside the repo be."""
    if not path:
        return path
    try:
        r = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:                       # different drive on Windows
        return path.replace("\\", "/")
    if r.startswith(".."):
        return path.replace("\\", "/")
    return r.replace("\\", "/")


def resolve(path: str | None) -> str | None:
    """Back to something openable, whoever cloned the repo."""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(ROOT, path.replace("/", os.sep))


def scrub(text: str | None) -> str | None:
    """Strip the repo's absolute prefix out of captured log text."""
    if not text:
        return text
    return _ABS_RE_FWD.sub("", _ABS_RE.sub("", text))
