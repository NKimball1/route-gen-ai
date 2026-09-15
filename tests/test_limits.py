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


def test_same_ip_hashes_the_same_so_abusers_are_still_countable():
    from routes.limits import pseudonymize_ip
    assert pseudonymize_ip("10.0.0.1") == pseudonymize_ip("10.0.0.1")
    assert pseudonymize_ip("10.0.0.1") != pseudonymize_ip("10.0.0.2")


def test_salt_changes_the_hash(monkeypatch):
    from routes import limits
    a = limits.pseudonymize_ip("10.0.0.1")
    monkeypatch.setattr(limits, "IP_SALT", "secret")
    assert limits.pseudonymize_ip("10.0.0.1") != a
