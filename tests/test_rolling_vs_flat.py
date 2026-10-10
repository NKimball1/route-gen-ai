"""A rolling road is not a flat one (routes/intervals.py).

Field case, 2026-09-27: a 'flat' 2x20 search ranked Paulson Rd first --
7.1 mi averaging -0.2%, but 370 ft of climbing -- because flatness was
scored by AVERAGE grade, and a road that climbs 30 m and descends 30 m
averages zero. Length then broke the tie in the rolling road's favor.
"""
import math

import pytest

from routes.intervals import IntervalSpec, find_spots
from tests.test_spot_results import DenseProvider

O = (43.0, -89.5)


class TwoSpokes(DenseProvider):
    """East: 10 km of rollers averaging 0% (5 m sine, 1 km wavelength,
    ~10 m of climbing per km). West: dead flat, but only 3.5 km of it."""
    def route(self, waypoints, avoid=None, protect=None):
        a, b = waypoints[0], waypoints[-1]
        east = b[1] > a[1] + 0.01
        west = b[1] < a[1] - 0.01
        if not (east or west):
            return None
        n = 400
        span = 10000.0 if east else 3500.0
        pts = []
        for k in range(n + 1):
            f = k / n
            x = span * f
            lon = a[1] + (1 if east else -1) * x / (111320.0 * math.cos(math.radians(a[0])))
            ele = 300.0 + (5.0 * math.sin(2 * math.pi * x / 1000.0) if east else 0.0)
            pts.append((a[0], lon, ele))
        from routes.editing import _cum
        return {"points": pts, "distance_m": _cum(pts)[-1], "ascent_m": 0.0, "major_m": 0.0}


def test_a_flat_stretch_beats_a_longer_rolling_one(monkeypatch):
    import routes.interruptions as interruptions
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: [])
    spec = IntervalSpec("x", 2, 10.0, "flat", 30.0)          # ~5.4 km reps, no watts
    spots = find_spots(spec, O[0], O[1], TwoSpokes(), n_spokes=4, top=2)
    assert len(spots) == 2
    best = spots[0]
    assert best.stretch.points[-1][1] < O[1], "the rolling eastern spoke outranked the flat one"
    assert max(p[2] for p in best.stretch.points) - min(p[2] for p in best.stretch.points) < 1.0


def test_the_climbing_shown_is_the_climbing_ranked(monkeypatch):
    """One climbing figure: a Spot's ft/mi in the web metrics and label is
    its Stretch's own figure, the one the finder ranked it on -- so a
    Stretch can't rank as flat while the table shows it rolling."""
    import routes.interruptions as interruptions
    from routes.spec import METERS_PER_FOOT, METERS_PER_MILE
    from routes.spot_service import spot_label, spot_metrics
    monkeypatch.setattr(interruptions, "fetch_controls", lambda *a, **k: [])
    spec = IntervalSpec("x", 2, 10.0, "flat", 30.0)
    spots = find_spots(spec, O[0], O[1], TwoSpokes(), n_spokes=4, top=2)
    rolling = next(s for s in spots if s.stretch.points[-1][1] > O[1])
    ranked_ft_per_mile = (rolling.stretch.climb_m_per_km * METERS_PER_MILE / 1000.0
                          / METERS_PER_FOOT)
    shown = spot_metrics(spec, rolling)["climb_ft_per_mile"]
    assert shown == pytest.approx(ranked_ft_per_mile, rel=1e-9)
    assert 45 < shown < 55            # ~10 m up per km of 5 m rollers
    assert f"{shown:.0f} ft/mi" in spot_label(spec, rolling)
