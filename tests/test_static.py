"""Static check: an undefined name is a bug no other test can see.

A project-wide refactor once replaced a literal with a constant in
find_spot.py without adding the import. Every module still IMPORTED
fine and the whole suite passed — a missing name inside a function only
fails when that line runs — so the interval-spot feature was dead for
eleven days until an end-to-end simulation hit it.
"""
import glob
import os
import subprocess
import sys

import pytest

pytest.importorskip("pyflakes")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pyflakes(files: list[str]) -> list[str]:
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         capture_output=True, text=True).stdout
    return out.splitlines()


def test_app_code_is_lint_clean():
    """Unused imports and dead assignments are how the undefined-name bug
    started: a refactor moved a name and nobody noticed the leftovers.
    Keep the shipped code at zero warnings so a new one stands out."""
    files = (glob.glob(os.path.join(ROOT, "*.py"))
             + glob.glob(os.path.join(ROOT, "routes", "*.py"))
             + glob.glob(os.path.join(ROOT, "scripts", "*.py")))
    assert len(files) > 15
    warnings = _pyflakes(files)
    assert warnings == [], "\n".join(warnings)


def test_no_undefined_names_anywhere():
    files = (glob.glob(os.path.join(ROOT, "*.py"))
             + glob.glob(os.path.join(ROOT, "routes", "*.py"))
             + glob.glob(os.path.join(ROOT, "tests", "*.py"))
             + glob.glob(os.path.join(ROOT, "scripts", "*.py")))
    assert len(files) > 20  # the glob itself must not silently find nothing
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         capture_output=True, text=True).stdout
    undefined = [ln for ln in out.splitlines() if "undefined name" in ln]
    assert undefined == [], "\n".join(undefined)
