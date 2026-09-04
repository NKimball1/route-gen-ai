from routes.despur import despur

LAT_STEP = 0.0005  # ~55 m of latitude per step


def road(n, lat0=43.0, lon=-89.5, ele=300.0):
    """n points heading north at ~55 m spacing."""
    return [(lat0 + k * LAT_STEP, lon, ele) for k in range(n)]


def test_clean_track_untouched():
    pts = road(20)
    clean, removed_dist, removed_ascent = despur(pts)
    assert clean == pts
    assert removed_dist == 0.0
    assert removed_ascent == 0.0


def test_spur_is_excised():
    main = road(11)
    # Detour east from point 5 and retrace: 5 -> B1 -> B2 -> B3 -> B2 -> B1 -> 5
    b = [(main[5][0], main[5][1] + k * LAT_STEP, 310.0) for k in (1, 2, 3)]
    pts = main[:6] + b + list(reversed(b[:-1])) + main[5:]
    clean, removed_dist, removed_ascent = despur(pts)
    assert clean == main
    assert removed_dist > 200  # both legs of the ~165 m spur
    assert removed_ascent > 0  # the spur climbed 10 m and came back


def test_short_wiggle_kept():
    # A turnaround shorter than min_spur_m (e.g. a driveway-sized nub) stays.
    main = road(11)
    nub = [(main[5][0], main[5][1] + 0.0002, 300.0)]  # ~16 m out and back
    pts = main[:6] + nub + main[5:]
    clean, removed_dist, _ = despur(pts)
    assert removed_dist == 0.0
    assert len(clean) == len(pts)


def test_full_palindrome_collapses():
    # A deliberate out-and-back IS one giant spur to this detector — which is
    # why providers despur only the one-way leg before mirroring (see
    # BRouterProvider._outback), never the finished out-and-back track.
    one_way = road(10)
    pts = one_way + list(reversed(one_way[:-1]))
    clean, removed_dist, _ = despur(pts)
    assert len(clean) < len(pts)
    assert removed_dist > 0
