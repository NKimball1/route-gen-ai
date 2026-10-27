from routes.limits import allow


def test_allows_up_to_limit():
    key = ("t1",)
    assert all(allow(key, 3, 60, now=100 + i) for i in range(3))
    assert not allow(key, 3, 60, now=104)


def test_window_slides():
    key = ("t2",)
    for i in range(3):
        assert allow(key, 3, 60, now=100 + i)
    assert not allow(key, 3, 60, now=110)
    # first hit (t=100) expires after 60 s
    assert allow(key, 3, 60, now=161)


def test_keys_independent():
    assert allow(("a", "x"), 1, 60, now=100)
    assert allow(("a", "y"), 1, 60, now=100)
    assert not allow(("a", "x"), 1, 60, now=101)


# ---- usage log privacy: IPs are pseudonymized before they touch disk ----

def test_usage_log_never_stores_a_raw_ip(tmp_path, monkeypatch):
    import json
    from routes import limits
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(limits, "USAGE_LOG", str(log))
    limits.log_event("ask", sid="abc", ip="203.0.113.7", text="hi")
    row = json.loads(log.read_text().strip())
    assert row["ip"] != "203.0.113.7"
    assert row["ip"] == limits.pseudonymize_ip("203.0.113.7")
    assert len(row["ip"]) == 16
    assert "203.0.113.7" not in log.read_text()


def test_text_logging_off_drops_everything_the_rider_typed(tmp_path, monkeypatch):
    """ROUTEGEN_LOG_TEXT=0 dropped the request text but kept "start", the
    typed start address -- often the rider's home (found 10-26)."""
    from routes import limits
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(limits, "USAGE_LOG", str(log))
    monkeypatch.setattr(limits, "LOG_TEXT", False)
    limits.log_event("ask", sid="abc", ip="203.0.113.7",
                     text="loop from 1 Example St", start="1 Example St, Madison WI")
    written = log.read_text()
    assert "Example St" not in written
    assert '"event": "ask"' in written and '"sid": "abc"' in written


def test_same_ip_hashes_the_same_so_abusers_are_still_countable():
    from routes.limits import pseudonymize_ip
    assert pseudonymize_ip("10.0.0.1") == pseudonymize_ip("10.0.0.1")
    assert pseudonymize_ip("10.0.0.1") != pseudonymize_ip("10.0.0.2")


def test_salt_changes_the_hash(monkeypatch):
    from routes import limits
    a = limits.pseudonymize_ip("10.0.0.1")
    monkeypatch.setattr(limits, "IP_SALT", "secret")
    assert limits.pseudonymize_ip("10.0.0.1") != a


def test_usage_log_rotates_so_disk_use_stays_bounded(tmp_path, monkeypatch):
    """The log kept every typed request and start address forever (open
    item since 10-17). Past the cap it moves aside to usage.jsonl.1,
    replacing the older copy: at most two capped files on disk."""
    from routes import limits
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(limits, "USAGE_LOG", str(log))
    monkeypatch.setattr(limits, "USAGE_LOG_MAX_BYTES", 1000)
    for i in range(200):
        limits.log_event("ask", sid=f"s{i}", text="x" * 40)
    older = tmp_path / "usage.jsonl.1"
    assert older.exists()
    assert log.stat().st_size <= 1000 + 200 and older.stat().st_size <= 1000 + 200
    assert '"sid": "s199"' in log.read_text()          # newest is kept
    assert '"sid": "s0"' not in log.read_text() + older.read_text()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["usage.jsonl", "usage.jsonl.1"]


def test_usage_log_cap_of_zero_keeps_everything(tmp_path, monkeypatch):
    from routes import limits
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(limits, "USAGE_LOG", str(log))
    monkeypatch.setattr(limits, "USAGE_LOG_MAX_BYTES", 0)
    for i in range(50):
        limits.log_event("ask", sid=f"s{i}", text="x" * 40)
    assert len(log.read_text().splitlines()) == 50
    assert not (tmp_path / "usage.jsonl.1").exists()


def test_a_failed_rotation_still_logs_the_event(tmp_path, monkeypatch):
    """On Windows a reader holding the file open blocks the rename; the
    event must still be written (rotation just waits for the next one)."""
    from routes import limits
    log = tmp_path / "usage.jsonl"
    log.write_text("x" * 2000 + "\n")
    monkeypatch.setattr(limits, "USAGE_LOG", str(log))
    monkeypatch.setattr(limits, "USAGE_LOG_MAX_BYTES", 1000)

    def locked(src, dst):
        raise PermissionError("in use")
    monkeypatch.setattr(limits.os, "replace", locked)
    limits.log_event("ask", sid="kept")
    assert '"sid": "kept"' in log.read_text()
