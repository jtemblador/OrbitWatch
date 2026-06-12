"""
Conjunction pipeline — Python stages of the screening cascade.

The C++ core (orbitcore) does the heavy scanning:
    coarse_filter  — altitude-band pair screening (no propagation)
    medium_filter  — time-stepped distance scan, returns close-approach
                     windows as (i, j, jd_of_best_step, distance_km)

This module refines those windows:
    fine_filter    — exact Time of Closest Approach (TCA) + miss distance
                     inside a flagged window, via bounded scalar
                     minimization. Each distance evaluation propagates both
                     satellites through the C++ SGP4 engine — Python only
                     decides which times to try (~10-50 evaluations).

Stays in Python by design: it runs on the handful of windows that survive
the C++ filters, and the per-evaluation cost is already C++. See
progress/task_logs/task_6_1_batch_sgp4.md for the measured rationale
("C++ where it's hot, Python where it's not").
"""

import math
import os
import sys
from datetime import datetime, timedelta, timezone

from scipy.optimize import minimize_scalar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import orbitcore

# Optimizer stopping tolerance on the time variable, in minutes.
# 1e-5 min = 0.6 ms — far finer than SGP4's positional accuracy warrants,
# and cheap (a few extra objective evaluations).
_XATOL_MIN = 1e-5

# If the optimizer converges within this many minutes of a bracket edge,
# the true minimum likely lies just outside — widen once and retry.
_EDGE_TOL_MIN = 1e-3


def _epoch_jd(satrec) -> float:
    """Absolute Julian date of a satrec's element epoch."""
    return satrec.jdsatepoch + satrec.jdsatepochF


def fine_filter(satrec_a, satrec_b, jd_lo: float, jd_hi: float) -> dict:
    """
    Find the exact Time of Closest Approach (TCA) and miss distance for a
    satellite pair within a time bracket.

    The bracket normally comes from a medium_filter row: the flagged step
    +/- one step. If the minimum lands on a bracket edge, the bracket is
    widened once by its own width on that side and the search re-run; if
    it is still on an edge after that (caller's bracket nowhere near a
    real minimum), the edge result is returned as-is — best effort.

    Args:
        satrec_a, satrec_b: initialized orbitcore Satrec objects
        jd_lo, jd_hi: bracket as absolute Julian dates (UTC), jd_hi > jd_lo

    Returns dict:
        jd_tca:         TCA as absolute Julian date
        tca_utc:        TCA as timezone-aware datetime (UTC)
        miss_km:        minimum distance, km
        rel_speed_km_s: |v_a - v_b| at TCA, km/s
        pos_a_teme, vel_a_teme, pos_b_teme, vel_b_teme:
                        both satellites' TEME states at TCA (km, km/s) —
                        feed teme_to_rtn() for encounter geometry.

    Raises:
        ValueError: jd_hi <= jd_lo
        RuntimeError: no finite distance anywhere in the bracket (e.g. a
                      satellite cannot be propagated there)
    """
    if not (jd_hi > jd_lo):
        raise ValueError(
            f"fine_filter: jd_hi ({jd_hi}) must be > jd_lo ({jd_lo})")

    epoch_a = _epoch_jd(satrec_a)
    epoch_b = _epoch_jd(satrec_b)

    def distance_km(minutes_from_lo: float) -> float:
        """Pair distance at jd_lo + minutes. inf if propagation fails."""
        jd_t = jd_lo + minutes_from_lo / 1440.0
        try:
            (pa, _) = orbitcore.sgp4(satrec_a, (jd_t - epoch_a) * 1440.0)
            (pb, _) = orbitcore.sgp4(satrec_b, (jd_t - epoch_b) * 1440.0)
        except RuntimeError:
            return math.inf
        return math.dist(pa, pb)

    # Optimize over "minutes into the bracket" rather than raw Julian date:
    # a JD is ~2.46e6, where absolute tolerances and float spacing are
    # awkward; minutes-from-lo is a small, well-conditioned variable.
    span_min = (jd_hi - jd_lo) * 1440.0
    lo, hi = 0.0, span_min

    def optimize(bound_lo: float, bound_hi: float):
        return minimize_scalar(
            distance_km, bounds=(bound_lo, bound_hi), method="bounded",
            options={"xatol": _XATOL_MIN},
        )

    res = optimize(lo, hi)
    at_lo = (res.x - lo) < _EDGE_TOL_MIN
    at_hi = (hi - res.x) < _EDGE_TOL_MIN
    if at_lo or at_hi:
        # Minimum on a bracket edge: the true TCA likely lies just outside
        # (medium-filter bracket was off by up to a step). Widen once.
        width = hi - lo
        if at_lo:
            lo -= width
        if at_hi:
            hi += width
        res = optimize(lo, hi)

    if not math.isfinite(res.fun):
        raise RuntimeError(
            "fine_filter: no finite distance in bracket — a satellite "
            "cannot be propagated there (decayed?)")

    jd_tca = jd_lo + res.x / 1440.0
    (pa, va) = orbitcore.sgp4(satrec_a, (jd_tca - epoch_a) * 1440.0)
    (pb, vb) = orbitcore.sgp4(satrec_b, (jd_tca - epoch_b) * 1440.0)

    # invjday seconds can hit exactly 60.0 at minute rollover; routing the
    # minutes+seconds through timedelta handles all carry cases.
    year, mon, day, hr, minute, sec = orbitcore.invjday(jd_tca, 0.0)
    tca_utc = (datetime(year, mon, day, hr, 0, 0, tzinfo=timezone.utc)
               + timedelta(minutes=minute, seconds=sec))

    return {
        "jd_tca": jd_tca,
        "tca_utc": tca_utc,
        "miss_km": float(res.fun),
        "rel_speed_km_s": math.dist(va, vb),
        "pos_a_teme": pa,
        "vel_a_teme": va,
        "pos_b_teme": pb,
        "vel_b_teme": vb,
    }
