"""Rider physics: how fast does a given power move a rider up a given grade?

Used to size interval stretches from WATTS instead of a fixed speed guess
("4 minutes at 285 W" is a distance only once you know the grade). The
model is the standard steady-state balance: power at the wheel equals
rolling resistance + gravity + aerodynamic drag.

    P * eff = (Crr + grade) * m * g * v + 0.5 * rho * CdA * v^3

Solved for v by bisection (the right-hand side is monotonic for v > 0
on any grade a bike can hold). Defaults describe a road bike on the
hoods; a 5% error in CdA moves the flat speed ~2%, which is well inside
the spread between two riders' positions — good enough to size a
stretch, not a substitute for a power meter.
"""
import math
import os

G: float = 9.81               # m/s^2
CRR: float = 0.005            # rolling resistance, good road tire on asphalt
CDA_M2: float = 0.32          # drag area, road bike on the hoods
RHO: float = 1.2              # air density at ~300 m, kg/m^3
DRIVETRAIN_EFF: float = 0.975  # chain losses: watts at the pedal -> wheel
# Rider + bike + kit when the user hasn't said. Env-tunable because it is
# the one number that varies most between riders and matters on a climb.
DEFAULT_TOTAL_KG: float = float(os.environ.get("ROUTEGEN_TOTAL_KG", "84"))


def speed_mps(watts: float, grade_pct: float,
              total_kg: float = DEFAULT_TOTAL_KG) -> float:
    """Steady-state speed (m/s) holding `watts` on `grade_pct`."""
    if watts <= 0:
        return 0.0
    wheel_w = watts * DRIVETRAIN_EFF
    resist = (CRR + grade_pct / 100.0) * total_kg * G   # N, may be negative
    aero = 0.5 * RHO * CDA_M2

    def deficit(v: float) -> float:
        return resist * v + aero * v ** 3 - wheel_w

    lo, hi = 0.0, 40.0   # 144 km/h: no grade here needs more
    for _ in range(60):
        mid = (lo + hi) / 2
        if deficit(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def seconds_for(length_m: float, grade_pct: float, watts: float,
                total_kg: float = DEFAULT_TOTAL_KG) -> float:
    """Time to cover a stretch at constant power."""
    v = speed_mps(watts, grade_pct, total_kg)
    return math.inf if v <= 0 else length_m / v


def mmss(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "--:--"
    return f"{int(seconds // 60)}:{int(round(seconds % 60)):02d}"
