from routes.climbs import extract_climbs
from routes.strava import decode_polyline

LAT_STEP = 0.0011  # ~122 m per step


def profile(eles, lat0=43.0, lon=-89.5):
    return [(lat0 + k * LAT_STEP, lon, float(e), k * 122.0)
            for k, e in enumerate(eles)]


def test_flat_profile_no_climbs():
    assert extract_climbs(profile([300] * 30)) == []


def test_sustained_climb_found():
    # ~1.2 km at ~4%: 10 steps gaining 5 m each
    eles = [300] * 5 + [300 + 5 * k for k in range(11)] + [355] * 5
    climbs = extract_climbs(profile(eles))
    assert len(climbs) == 1
    c = climbs[0]
    assert 45 <= c["gain_m"] <= 55
    assert 3.0 <= c["avg_grade_pct"] <= 5.0


def test_dip_tolerated_but_descent_ends_climb():
    # climb, small 8 m dip, more climb -> one climb; then real descent
    eles = ([300 + 5 * k for k in range(8)]        # up 35
            + [335 - 4, 335 - 8]                    # dip 8
            + [335 + 5 * k for k in range(6)]       # up more
            + [360 - 6 * k for k in range(8)])      # descend
    climbs = extract_climbs(profile(eles))
    assert len(climbs) == 1
    assert climbs[0]["gain_m"] >= 55


def test_shallow_approach_does_not_dilute_summit_climb():
    # Blue Mounds pattern: miles of ~0.8% approach, then a steep 1.2 km
    # summit road at ~7%. The average over the whole run fails the grade
    # threshold; the extractor must still return the steep finish.
    eles = [300 + 1.0 * k for k in range(40)]          # 4.9 km at 0.8%
    eles += [340 + 8.5 * k for k in range(11)]         # 1.2 km at ~7%
    climbs = extract_climbs(profile(eles))
    assert len(climbs) == 1
    c = climbs[0]
    assert c["gain_m"] >= 85          # the summit road, plus qualifying lead-in
    assert c["avg_grade_pct"] >= 2.5


def test_polyline_decode_known_vector():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert [(round(a, 3), round(b, 3)) for a, b in pts] == [
        (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
