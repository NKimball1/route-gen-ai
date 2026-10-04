"""The evaluation harness is code too, so its judgements get tested.

Two of these checks were loosened after they produced false positives on real
routes. A loosened check is worthless if it can no longer catch the thing it
exists for, so each one is tested from BOTH sides: the legitimate geometry it
must accept, and the broken geometry it must still reject.
"""
import math

import pytest

from evals import geo

MILE = 1609.344
DEG_LAT = 110540.0


def _lat(m: float) -> float:
    return m / DEG_LAT


def _lon(m: float, lat: float = 43.0) -> float:
    return m / (111320.0 * math.cos(math.radians(lat)))


def straight(n: int, step_m: float, north: bool = True, start=(43.0, -89.4)):
    """A dead-straight run, one vertex every step_m."""
    out = []
    for i in range(n):
        if north:
            out.append((start[0] + _lat(step_m * i), start[1], 250.0))
        else:
            out.append((start[0], start[1] + _lon(step_m * i), 250.0))
    return out


# ---- teleport detection -------------------------------------------------

def test_section_road_corner_is_not_a_teleport():
    """The false positive that made this check useless the first time:
    Wisconsin's grid means you turn 90 degrees onto a road and then run
    dead straight for half a mile with no intermediate vertex."""
    north = straight(6, 60.0)
    corner = north[-1]
    east = [(corner[0], corner[1] + _lon(MILE / 2), 250.0)]
    east += [(corner[0], corner[1] + _lon(MILE / 2 + 60.0 * i), 250.0)
             for i in range(1, 6)]
    assert geo.suspicious_gaps(north + east) == []


def test_long_straight_rural_mile_is_not_a_teleport():
    pts = straight(4, 60.0) + [(43.0 + _lat(180 + MILE), -89.4, 250.0)]
    pts += [(43.0 + _lat(180 + MILE + 60 * i), -89.4, 250.0)
            for i in range(1, 4)]
    assert geo.suspicious_gaps(pts) == []


def test_spliced_track_is_still_caught():
    """Teeth: two fragments joined by an off-axis connector. The connector
    is entered AND left at a sharp angle, which a road corner never is."""
    a = straight(8, 60.0)                       # heading north
    far = (a[-1][0], a[-1][1] + _lon(900), 250.0)   # connector runs east
    b = [far] + [(far[0] - _lat(60 * i), far[1], 250.0) for i in range(1, 8)]
    found = geo.suspicious_gaps(a + b)
    assert len(found) == 1, found
    assert found[0]["gap_m"] > 800


def test_teleport_needs_to_be_long():
    a = straight(8, 60.0)
    near = (a[-1][0] + _lat(120), a[-1][1] + _lon(120), 250.0)
    b = [near] + [(near[0] - _lat(60 * i), near[1], 250.0) for i in range(1, 8)]
    assert geo.suspicious_gaps(a + b) == []


# ---- out-and-back detection ---------------------------------------------

def test_exact_palindrome_scores_as_an_out_and_back():
    """The bug this replaced: index-based resampling drifted out of phase
    and scored an exact out-and-back at 0.6%."""
    leg = straight(40, 80.0)
    track = leg + leg[-2::-1]
    assert geo.mirror_fraction(track) > 0.95


def test_loop_does_not_score_as_an_out_and_back():
    pts = [(43.0 + _lat(2000 * math.sin(t / 40 * 2 * math.pi)),
            -89.4 + _lon(2000 * math.cos(t / 40 * 2 * math.pi)), 250.0)
           for t in range(41)]
    assert geo.mirror_fraction(pts) < 0.2


def test_lollipop_is_not_mistaken_for_an_out_and_back():
    """A stick out, a loop, and the same stick back repeats road but is not
    a mirror -- it must not get the out-and-back exemption."""
    stick = straight(20, 80.0)
    top = stick[-1]
    loop = [(top[0] + _lat(900 * math.sin(t / 20 * 2 * math.pi)),
             top[1] + _lon(900 * (1 - math.cos(t / 20 * 2 * math.pi))), 250.0)
            for t in range(21)]
    track = stick + loop + stick[-2::-1]
    assert geo.mirror_fraction(track) < 0.6


# ---- GPX validation -----------------------------------------------------

def _gpx(body: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<trk><trkseg>{body}</trkseg></trk></gpx>')


def test_strict_gpx_accepts_a_real_track(tmp_path):
    p = tmp_path / "ok.gpx"
    p.write_text(_gpx('<trkpt lat="43.07" lon="-89.38"><ele>260.0</ele></trkpt>'
                      '<trkpt lat="43.08" lon="-89.39"><ele>262.0</ele></trkpt>'))
    pts = geo.parse_gpx_strict(str(p))
    assert len(pts) == 2 and pts[0][2] == 260.0


@pytest.mark.parametrize("body,fragment", [
    ('<trkpt lat="43.07" lon="-89.38"/>', "track point"),
    ('<trkpt lat="943.07" lon="-89.38"/><trkpt lat="43.08" lon="-89.39"/>',
     "out of range"),
    ('<trkpt lat="43.07" lon="-89.38"><ele>NaN</ele></trkpt>'
     '<trkpt lat="43.08" lon="-89.39"/>', "non-finite"),
    ('<trkpt lat="abc" lon="-89.38"/><trkpt lat="43.08" lon="-89.39"/>',
     "bad lat-lon"),
])
def test_strict_gpx_rejects_bad_tracks(tmp_path, body, fragment):
    p = tmp_path / "bad.gpx"
    p.write_text(_gpx(body))
    with pytest.raises(geo.GpxProblem) as e:
        geo.parse_gpx_strict(str(p))
    assert fragment in str(e.value)


def test_strict_gpx_rejects_malformed_xml(tmp_path):
    """The app's own preview parses GPX with regexes, which accept this.
    The eval uses a real XML parser so a file a device would reject fails."""
    p = tmp_path / "torn.gpx"
    p.write_text('<?xml version="1.0"?><gpx><trk><trkseg>'
                 '<trkpt lat="43.07" lon="-89.38"/>')
    with pytest.raises(geo.GpxProblem):
        geo.parse_gpx_strict(str(p))


# ---- measurement sanity --------------------------------------------------

def test_track_length_matches_a_known_distance():
    # The helper lays points out with the app's flat-earth constant
    # (110540 m/deg); geo measures with haversine on the WGS-84 mean radius.
    # They differ by ~0.6%, which is exactly the kind of gap an independent
    # implementation is supposed to expose.
    pts = straight(2, MILE)
    assert geo.track_length_m(pts) == pytest.approx(MILE, rel=0.01)


def test_min_dist_uses_segments_not_just_vertices():
    """A waypoint beside the middle of a long straight leg is 50 m away,
    even though the nearest VERTEX is half a mile off."""
    pts = [(43.0, -89.4, None), (43.0 + _lat(MILE), -89.4, None)]
    target = (43.0 + _lat(MILE / 2), -89.4 + _lon(50.0))
    assert geo.min_dist_to_track_m(target, pts) == pytest.approx(50, abs=5)
    assert geo.haversine_m(target, pts[0]) > 700


# ---- the scorer's own failure modes -------------------------------------

def test_teleport_detail_renders_when_one_is_found():
    """A renamed key in this format string raised KeyError mid-campaign and
    took 37 unfinished cases with it: the detail string is only built when a
    teleport EXISTS, so every clean route hid the bug."""
    from evals import scorers

    a = straight(8, 60.0)
    far = (a[-1][0], a[-1][1] + _lon(900), 250.0)
    b = [far] + [(far[0] - _lat(60 * i), far[1], 250.0) for i in range(1, 8)]
    found = [{**g, "road": False, "why": "the router found no road"}
             for g in geo.suspicious_gaps(a + b)]
    m = scorers.Measured(
        gpx_path="x.gpx", gpx_valid=True, n_points=16, distance_mi=1.0,
        max_point_gap_m=900.0, teleports=found,
        repeat_frac=0.0, mirror_frac=0.0)
    case = {"expect": {"request_type": "route", "route": {}}}
    checks = scorers.score_route(case, m, {"request_type": "route"})
    cont = next(c for c in checks if c.name == "geometry.continuous")
    assert cont.status == "fail"
    assert "break" in cont.detail and "no road" in cont.detail


# ---- is a long straight segment a road, or a hole? ----------------------

def _jog():
    """South on a section road, half a mile west, south again -- a grid jog.
    Geometrically identical to a splice: 90 degrees in, 90 degrees out, and
    no vertex along the connector."""
    down = [(43.0 - _lat(60 * i), -89.4, 250.0) for i in range(6)]
    corner = down[-1]
    west = (corner[0], corner[1] - _lon(MILE / 2), 250.0)
    tail = [west] + [(west[0] - _lat(60 * i), west[1], 250.0)
                     for i in range(1, 6)]
    return down + tail


def test_a_grid_jog_looks_exactly_like_a_splice_to_geometry():
    """Stated as a test because it is the reason the road check exists."""
    assert len(geo.suspicious_gaps(_jog())) == 1


def _fake_router(monkeypatch, road_m, reachable=True):
    import evals.roadcheck as rc
    monkeypatch.setattr(rc, "_load", dict)
    monkeypatch.setattr(rc, "_save", lambda d: None)

    class P:
        base_url = "http://fake"

        def route(self, wps, avoid=None, protect=None):
            if road_m is None:
                return None
            return {"points": [], "distance_m": road_m, "ascent_m": 0.0,
                    "major_m": 0.0}

    import routes.providers as rp
    monkeypatch.setattr(rp, "BRouterProvider", lambda *a, **k: P())
    monkeypatch.setattr(rp, "brouter_reachable",
                        lambda url, timeout_s=1.0: reachable)


def test_gap_with_a_road_along_it_is_not_a_break(monkeypatch):
    from evals import roadcheck
    pts = _jog()
    gaps = geo.suspicious_gaps(pts)
    _fake_router(monkeypatch, road_m=810.0)     # straight line is ~805 m
    breaks, roads, unknown = roadcheck.classify_gaps(pts, gaps)
    assert (len(breaks), len(roads), len(unknown)) == (0, 1, 0)


def test_gap_the_router_can_only_detour_around_is_a_break(monkeypatch):
    from evals import roadcheck
    pts = _jog()
    gaps = geo.suspicious_gaps(pts)
    _fake_router(monkeypatch, road_m=6000.0)    # a long way round
    breaks, roads, unknown = roadcheck.classify_gaps(pts, gaps)
    assert (len(breaks), len(roads), len(unknown)) == (1, 0, 0)
    assert "detour" in breaks[0]["why"]


def test_gap_with_no_road_at_all_is_a_break(monkeypatch):
    from evals import roadcheck
    pts = _jog()
    _fake_router(monkeypatch, road_m=None)
    breaks, roads, unknown = roadcheck.classify_gaps(pts,
                                                     geo.suspicious_gaps(pts))
    assert len(breaks) == 1 and breaks[0]["road"] is False


def test_unreachable_router_leaves_the_gap_unjudged(monkeypatch):
    """Not knowing is a third answer. Calling an unchecked gap a break would
    turn a dead router into fake evidence about route quality."""
    from evals import roadcheck
    pts = _jog()
    _fake_router(monkeypatch, road_m=810.0, reachable=False)
    breaks, roads, unknown = roadcheck.classify_gaps(pts,
                                                     geo.suspicious_gaps(pts))
    assert (len(breaks), len(roads), len(unknown)) == (0, 0, 1)
    assert unknown[0]["road"] is None
