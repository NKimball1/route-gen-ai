from routes.overlap import repeated_fraction

STEP = 0.0006  # ~66 m per step


def test_clean_loop_low_overlap():
    # a rectangle: no road ridden twice
    pts = []
    for k in range(40):
        pts.append((43.0 + k * STEP, -89.5, None))
    for k in range(40):
        pts.append((43.0 + 40 * STEP, -89.5 + k * STEP, None))
    for k in range(40):
        pts.append((43.0 + (40 - k) * STEP, -89.5 + 40 * STEP, None))
    for k in range(40):
        pts.append((43.0, -89.5 + (40 - k) * STEP, None))
    assert repeated_fraction(pts) < 0.08


def test_lollipop_stick_detected():
    # out a long stick, around a small loop, back the SAME stick — the
    # pattern despur cannot see (retrace separated by the loop in between)
    stick = [(43.0 + k * STEP, -89.5, None) for k in range(60)]
    top = stick[-1]
    loop = []
    for k in range(10):
        loop.append((top[0] + k * STEP, top[1] + k * STEP, None))
    for k in range(10):
        loop.append((top[0] + (10 - k) * STEP, top[1] + (10 + k) * STEP, None))
    for k in range(20):
        loop.append((top[0], top[1] + (20 - k) * STEP, None))
    pts = stick + loop + list(reversed(stick))
    frac = repeated_fraction(pts)
    assert frac > 0.3  # the stick is ~60% of the distance, ridden twice
