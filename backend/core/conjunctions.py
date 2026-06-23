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
import time
from datetime import datetime, timedelta, timezone

from scipy.optimize import minimize_scalar

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import orbitcore
from backend.core.coordinate_transforms import teme_to_rtn
from backend.core.screening_volumes import gross_threshold_km, regime_for

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


# Default coarse-to-fine screening parameters. The 60 s medium step is the
# proven Phase-6 value (task 6.3); the gross threshold doubles as the report
# threshold — safe because medium_filter's velocity-aware no-skip bound
# guarantees no sub-threshold approach is dropped at that threshold.
_DEFAULT_STEP_SEC = 60.0

# Co-located / persistent-proximity suppression (Phase 7.2, "Conservative-drop").
# A real conjunction is a *crossing* — transient and fast. Docked modules and
# parked constellation formations sit inside the screening volume but co-move
# (near-zero relative speed). 19 SDS itself skips Pc when "the relative speed is
# too small" (user-settable — SFS Handbook Annex A); we mirror that, and only
# drop a pair that is ALSO either co-located to within the miss floor (docked) or
# from the same launch (formation), so an unrelated slow approach is never hidden.
# Both floors are tunable; defaults keep the real Starlink 0.34 km @ 1.26 km/s
# event and drop docked stations (~0 km, ~0 km/s).
_V_REL_FLOOR_KM_S = 0.5      # below this a pair is co-moving, not crossing
_MIN_MISS_FLOOR_KM = 0.05    # ~50 m: docked-module co-location artifact


def _launch_id(object_id: str) -> str:
    """International-designator launch prefix `YYYY-DDD` of `YYYY-DDDxxx`.
    Objects from one launch share it (trailing letters distinguish pieces).
    Empty string for unparseable ids — which never matches another empty."""
    oid = (object_id or "").strip()
    return oid[:8] if len(oid) >= 8 and oid[4] == "-" else ""


def _is_co_located(event: dict, mi: dict, mj: dict) -> bool:
    """Conservative-drop test: a persistent-proximity (co-located) pair rather
    than a crossing conjunction. Relative speed below the floor AND (within the
    miss floor OR same launch)."""
    if event["relative_speed_km_s"] >= _V_REL_FLOOR_KM_S:
        return False
    if event["miss_distance_km"] < _MIN_MISS_FLOOR_KM:
        return True
    a = _launch_id(mi.get("object_id", ""))
    b = _launch_id(mj.get("object_id", ""))
    return bool(a) and a == b


def _dedupe_to_unique_pairs(events: list[dict]) -> list[dict]:
    """Collapse to one event per unordered satellite pair — the closest approach.
    medium_filter emits one window per node-pass, so a crossing pair recurs many
    times in a window; a screening report wants the single worst (min-miss) row
    per pair, so `count` reads as at-risk pairs, not window-crossings."""
    best: dict = {}
    for e in events:
        key = frozenset((e["sat1_norad_id"], e["sat2_norad_id"]))
        cur = best.get(key)
        if cur is None or e["miss_distance_km"] < cur["miss_distance_km"]:
            best[key] = e
    return list(best.values())


def run_screen(
    satrecs: list,
    meta: list[dict],
    start_utc: datetime,
    duration_hours: float,
    threshold_km: float | None = None,
    step_sec: float = _DEFAULT_STEP_SEC,
    pad_km: float | None = None,
    timings: dict | None = None,
    volumes: list | None = None,
    suppress: bool = True,
) -> list[dict]:
    """
    Run the full conjunction cascade over an index-aligned satellite set and
    return close-approach events sorted by miss distance (closest first).

    Pure function (no propagator, no I/O) so it can be driven directly from a
    fixed fixture in tests. The orchestration is:

        coarse_filter  (C++)  -> candidate pairs by altitude-band overlap
        medium_filter  (C++)  -> time-stepped windows that may dip below
                                 threshold (velocity-aware, no skips)
        fine_filter    (Py)   -> exact TCA + miss distance per window
        teme_to_rtn    (Py)   -> miss vector in the primary's RTN frame

    Args:
        satrecs:  list of initialized Satrec objects (positional indices are
                  the contract shared with medium_filter's (i, j) output).
        meta:     index-aligned metadata, meta[k] describing satrecs[k]:
                  {norad_id, name, object_type, epoch_age_days, periapsis_km,
                   apoapsis_km, object_id, eccentricity, period_min}.
        start_utc:       screening window start (UTC datetime).
        duration_hours:  window length in hours (> 0).
        threshold_km:    legacy Euclidean report distance in km (> 0). Used only
                         when `volumes` is None: the report cut is then
                         miss < threshold_km, with no suppression / de-dupe
                         (preserves the Phase-6 behavior). Provide this OR volumes.
        volumes:         index-aligned list of SFS ScreeningVolume, one per
                         satrec (see screening_volumes.regime_for). When given,
                         this is the SFS path: the report cut is the per-pair RTN
                         ellipsoid test, the medium gross threshold becomes the
                         largest semi-axis present (no-skip), and the result is
                         co-located-suppressed + de-duped to unique pairs.
        suppress:        SFS path only — apply Conservative-drop co-located
                         suppression (default True). No effect on the legacy path.
        step_sec:        medium-filter time step in seconds.
        pad_km:          coarse-filter altitude-band margin. Defaults to the
                         Euclidean gross threshold (threshold_km, or the largest
                         semi-axis in the SFS path) — a pair must close radially
                         to within that to ever count, so a smaller pad could
                         drop real conjunctions.
        timings:         optional dict; if provided, populated with per-stage
                         wall times (t_coarse / t_medium / t_fine, seconds) and
                         counts (n_sats, n_pairs, n_windows, n_events) for
                         profiling (see scripts/profile_screening.py). None
                         (default) → no measurement, no behavior change.
                         t_coarse / t_medium include the C++↔Python
                         survivor/row materialization — the scaling_tracker #3
                         boundary cost this profiling is meant to expose.

    Returns:
        list of event dicts matching the ConjunctionEvent schema fields,
        sorted ascending by miss_distance_km. Empty list if nothing is found.
    """
    use_volumes = volumes is not None
    if not use_volumes and threshold_km is None:
        raise ValueError(
            "run_screen: provide threshold_km (legacy Euclidean) or volumes "
            "(SFS ellipsoids)")

    # Index alignment IS the screening contract: medium_filter returns (i, j)
    # positions, and we map them back to identity via meta[i]/meta[j]. A length
    # mismatch would silently attach the wrong satellites to an event — guard it.
    if len(satrecs) != len(meta):
        raise ValueError(
            f"run_screen: satrecs ({len(satrecs)}) and meta ({len(meta)}) "
            "lengths differ — they must be index-aligned")
    if use_volumes and len(volumes) != len(satrecs):
        raise ValueError(
            f"run_screen: volumes ({len(volumes)}) must be index-aligned with "
            f"satrecs ({len(satrecs)})")

    # The medium filter's Euclidean gross threshold: in the SFS path the largest
    # semi-axis present (no-skip — every in-ellipsoid point has Euclidean norm
    # <= its largest semi-axis); in the legacy path threshold_km itself.
    gross_km = gross_threshold_km(volumes) if use_volumes else threshold_km
    if pad_km is None:
        pad_km = gross_km

    periapsis = [m["periapsis_km"] for m in meta]
    apoapsis = [m["apoapsis_km"] for m in meta]

    # ⚠ PERF (Phase 7, scaling_tracker #3): coarse_filter hands survivor pairs
    # back to Python and we pass them straight into medium_filter — millions of
    # (i, j) tuples crossing the C++↔Python boundary twice (~378 ns/pair each
    # way, measured 6.2). Measured on the full 10,544-sat Starlink catalog (7.1):
    # 49% of all pairs survive at a 50 km pad (27.3 M), 19% at 5 km (10.5 M) —
    # altitude-band screening is inclination-blind, so a constellation's stacked
    # shells (43°/53°/97° all near ~475 km) barely cull. Fine at <=300 sats; at
    # scale, 7.3 fuses the coarse cut inside medium_filter so survivors never
    # materialize as Python objects.
    _t = time.perf_counter()
    pairs = orbitcore.coarse_filter(periapsis, apoapsis, pad_km)
    if timings is not None:
        timings["n_sats"] = len(satrecs)
        timings["n_pairs"] = len(pairs)
        timings["t_coarse"] = time.perf_counter() - _t
    if not pairs:
        if timings is not None:
            timings["n_windows"] = 0
            timings["n_events"] = 0
            timings["n_suppressed"] = 0
            timings["t_medium"] = 0.0
            timings["t_fine"] = 0.0
        return []

    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    jd_w, jd_f = orbitcore.jday(
        start_utc.year, start_utc.month, start_utc.day,
        start_utc.hour, start_utc.minute,
        start_utc.second + start_utc.microsecond / 1e6,
    )
    jd_start = jd_w + jd_f
    jd_end = jd_start + duration_hours / 24.0

    _t = time.perf_counter()
    rows = orbitcore.medium_filter(
        satrecs, pairs, jd_start, jd_end, step_sec, gross_km)
    if timings is not None:
        timings["t_medium"] = time.perf_counter() - _t
        timings["n_windows"] = len(rows)

    _t = time.perf_counter()
    step_day = step_sec / 86400.0
    events = []
    n_suppressed = 0
    for (i, j, jd_flag, _d_flag) in rows:
        try:
            out = fine_filter(
                satrecs[i], satrecs[j], jd_flag - step_day, jd_flag + step_day)
        except RuntimeError:
            # A satellite can't be propagated across this window (decayed):
            # drop the event, leave the rest of the screen intact.
            continue

        r_km, t_km, n_km = teme_to_rtn(
            out["pos_a_teme"], out["vel_a_teme"], out["pos_b_teme"])

        if use_volumes:
            # SFS report cut: is the RTN miss inside the pair's screening
            # ellipsoid? Use the larger of the two objects' regimes so a mixed
            # pair is never under-screened (co-altitude pairs share one regime).
            vol = max(volumes[i], volumes[j],
                      key=lambda v: v.circumscribing_radius())
            if not vol.contains(r_km, t_km, n_km):
                continue
            regime = vol.name
        else:
            # Legacy Euclidean cut: the window was bracketed by the velocity-aware
            # bound but the refined minimum may still be outside the threshold.
            if out["miss_km"] >= threshold_km:
                continue
            regime = ""

        mi, mj = meta[i], meta[j]
        event = {
            "sat1_norad_id": mi["norad_id"],
            "sat1_name": mi["name"],
            "sat1_object_type": mi["object_type"],
            "sat1_epoch_age_days": mi["epoch_age_days"],
            "sat2_norad_id": mj["norad_id"],
            "sat2_name": mj["name"],
            "sat2_object_type": mj["object_type"],
            "sat2_epoch_age_days": mj["epoch_age_days"],
            "tca": out["tca_utc"].isoformat(),
            "miss_distance_km": out["miss_km"],
            "relative_speed_km_s": out["rel_speed_km_s"],
            "r_km": r_km,
            "t_km": t_km,
            "n_km": n_km,
            "screening_regime": regime,
        }

        if use_volumes and suppress and _is_co_located(event, mi, mj):
            n_suppressed += 1
            continue
        events.append(event)

    if use_volumes:
        # One row per unique pair (the closest approach) so count = at-risk pairs.
        events = _dedupe_to_unique_pairs(events)
    events.sort(key=lambda e: e["miss_distance_km"])
    if timings is not None:
        timings["t_fine"] = time.perf_counter() - _t
        timings["n_events"] = len(events)
        timings["n_suppressed"] = n_suppressed
    return events


class ConjunctionScreener:
    """
    Orchestrates the conjunction cascade over a propagator's loaded catalog,
    keeping the API router thin. The actual screening is run_screen() — this
    just supplies it the propagator's index-aligned satrecs + metadata.
    """

    def __init__(self, propagator):
        self.propagator = propagator

    def screen(
        self,
        start_utc: datetime,
        duration_hours: float,
        threshold_km: float | None = None,
        step_sec: float = _DEFAULT_STEP_SEC,
        pad_km: float | None = None,
        timings: dict | None = None,
        suppress: bool = True,
    ) -> list[dict]:
        """Screen the propagator's catalog; see run_screen for semantics.

        Default (threshold_km=None): the SFS path — each object's screening
        ellipsoid from `regime_for` (perigee + eccentricity + period), the
        per-pair ellipsoid report cut, co-located suppression + de-dupe to
        unique pairs. Passing a `threshold_km` selects the legacy Euclidean
        path (no suppression / de-dupe) for back-compat or debugging.

        `timings` (optional dict) surfaces stage times + counts, including
        `n_suppressed`, for the router/profiler.
        """
        satrecs, meta = self.propagator.get_all_satrecs()
        if threshold_km is None:
            volumes = [
                regime_for(
                    m["periapsis_km"], m["eccentricity"], m["period_min"])
                for m in meta
            ]
            return run_screen(
                satrecs, meta, start_utc, duration_hours, step_sec=step_sec,
                pad_km=pad_km, timings=timings, volumes=volumes,
                suppress=suppress)
        return run_screen(
            satrecs, meta, start_utc, duration_hours, threshold_km=threshold_km,
            step_sec=step_sec, pad_km=pad_km, timings=timings)
