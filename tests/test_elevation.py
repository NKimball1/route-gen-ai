from routes.elevation import track_ascent


def pts(eles):
    return [(43.0, -89.5, float(e)) for e in eles]


def test_flat_is_zero():
    assert track_ascent(pts([300] * 50)) == 0.0


def test_single_hill():
    assert track_ascent(pts(list(range(300, 350)) + list(range(350, 300, -1)))) == 50.0


def test_noise_below_hysteresis_ignored():
    # 3 m sawtooth jitter should not accumulate
    eles = [300 + (3 if k % 2 else 0) for k in range(100)]
    assert track_ascent(pts(eles)) == 0.0


def test_rollers_above_hysteresis_all_count():
    # three 20 m climbs with descents between
    eles = []
    for _ in range(3):
        eles += list(range(300, 321)) + list(range(320, 299, -1))
    assert 55 <= track_ascent(pts(eles)) <= 65
