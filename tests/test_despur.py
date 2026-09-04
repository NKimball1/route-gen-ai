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


def test_corridor_catches_offset_return():
    from routes.despur import corridor_despur
    # Out east on one line, back on a parallel line ~17 m north — exact
    # matching misses it; the corridor pass must not.
    lon_step = 0.0007  # ~57 m of longitude
    out_leg = [(43.0, -89.5 + k * lon_step, 300.0) for k in range(10)]
    back_leg = [(43.00015, -89.5 + (9 - k) * lon_step, 300.0) for k in range(10)]
    main = road(11)
    pts = main[:6] + out_leg + back_leg + main[5:]
    exact_clean, exact_removed, _ = despur(pts)
    assert exact_removed == 0.0  # the offset defeats exact matching
    clean, removed, _ = corridor_despur(pts)
    assert removed > 700  # ~half a km each way
    # the surviving track stays on the main road
    lons = [p[1] for p in clean]
    assert max(lons) < -89.49


def test_corridor_leaves_clean_loop_alone():
    from routes.despur import corridor_despur
    pts = road(60)
    clean, removed, _ = corridor_despur(pts)
    assert removed == 0.0
    assert clean == pts  # untouched, original geometry preserved


def test_full_palindrome_collapses():
    # A deliberate out-and-back IS one giant spur to this detector — which is
    # why providers despur only the one-way leg before mirroring (see
    # BRouterProvider._outback), never the finished out-and-back track.
    one_way = road(10)
    pts = one_way + list(reversed(one_way[:-1]))
    clean, removed_dist, _ = despur(pts)
    assert len(clean) < len(pts)
    assert removed_dist > 0
