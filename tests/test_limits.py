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
