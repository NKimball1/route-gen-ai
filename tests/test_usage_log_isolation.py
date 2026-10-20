"""The test suite never writes the real usage log.

Found 10-20: each full run appended two "sessions_pruned" rows to
output/usage.jsonl, from a test that exercised the server's pruning with
the real log in place. tests/conftest.py now points the log at a temporary
file for every test.
"""
import os

from routes import limits


def test_the_usage_log_is_not_the_real_one(tmp_path):
    assert os.path.abspath(limits.USAGE_LOG) != os.path.abspath(os.path.join("output", "usage.jsonl"))


def test_events_logged_by_a_test_land_in_its_own_temp_file():
    limits.log_event("test_event", n=1)
    with open(limits.USAGE_LOG, encoding="utf-8") as f:
        assert '"test_event"' in f.read()
