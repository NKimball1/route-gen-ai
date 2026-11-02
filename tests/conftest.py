"""Shared test setup: nothing a test does may land in the real output/ logs,
and nothing one test looks up is remembered into the next."""
import pytest

from routes import geocode, limits


@pytest.fixture(autouse=True)
def private_usage_log(tmp_path, monkeypatch):
    """Every test logs usage events to its own temporary file, so the real
    output/usage.jsonl only ever holds real traffic."""
    monkeypatch.setattr(limits, "USAGE_LOG", str(tmp_path / "usage.jsonl"))


@pytest.fixture(autouse=True)
def fresh_geocode_answers(monkeypatch):
    """Tests fake different Nominatim answers to the same query."""
    monkeypatch.setattr(geocode, "_ANSWERS", {})
