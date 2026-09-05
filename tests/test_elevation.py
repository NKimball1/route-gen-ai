from routes.elevation import track_ascent

LAT_STEP = 0.00025  # ~28 m per point, close to the resample step


def pts(eles):
    return [(43.0 + k * LAT_STEP, -89.5, float(e)) for k, e in enumerate(eles)]


def test_flat_is_zero():
    assert track_ascent(pts([300] * 80)) < 1.0


def test_single_hill_measured_close():
    # 50 m up over ~1.4 km, then back down
    eles = [300 + k for k in range(51)] + [350 - k for k in range(51)]
    a = track_ascent(pts(eles))
    assert 42 <= a <= 52  # smoothing softens the crest slightly


def test_dem_noise_suppressed():
    # ±2 m sawtooth jitter: real DEM noise, not climbing
    eles = [300 + (2 if k % 2 else 0) for k in range(120)]
    assert track_ascent(pts(eles)) < 8


def test_small_rollers_count():
    # THE flat-route lesson: 6 m rollers are real climbing riders feel and
    # devices count — the old 10 m hysteresis discarded all of them.
    eles = []
    for _ in range(8):
        eles += [300 + 0.75 * k for k in range(9)] + [306 - 0.75 * k for k in range(9)]
    a = track_ascent(pts(eles))
    assert a >= 25  # a healthy share of the ~48 m of roller gain survives
