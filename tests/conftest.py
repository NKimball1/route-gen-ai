"""Shared test setup: nothing a test does may land in the real output/ logs."""
import pytest

from routes import limits


@pytest.fixture(autouse=True)
def private_usage_log(tmp_path, monkeypatch):
    """Every test logs usage events to its own temporary file, so the real
    output/usage.jsonl only ever holds real traffic."""
    monkeypatch.setattr(limits, "USAGE_LOG", str(tmp_path / "usage.jsonl"))
