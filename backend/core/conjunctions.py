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

import numpy as np
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


def _jd_to_utc(jd: float) -> datetime:
    """Absolute Julian date (UTC) -> timezone-aware datetime.

    invjday's seconds can hit exactly 60.0 at a minute rollover; routing the
    minutes+seconds through timedelta handles every carry case cleanly.
    """
    year, mon, day, hr, minute, sec = orbitcore.invjday(jd, 0.0)
    return (datetime(year, mon, day, hr, 0, 0, tzinfo=timezone.utc)
            + timedelta(minutes=minute, seconds=sec))


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

    return {
        "jd_tca": jd_tca,
        "tca_utc": _jd_to_utc(jd_tca),
        "miss_km": float(res.fun),
        "rel_speed_km_s": math.dist(va, vb),
        "pos_a_teme": pa,
        "vel_a_teme": va,
        "pos_b_teme": pb,
        "vel_b_teme": vb,
    }


# --- Batched fine stage (Phase 7.3) -----------------------------------------
#
# The per-window scipy.minimize_scalar above is exact and is kept as the
# reference oracle, but at scale it dominates wall time (7.1 profile: 82-87%,
# 1.43 M windows for the full Starlink catalog), because every objective
# evaluation is two separate orbitcore.sgp4() Python->C++ crossings and every
# window pays scipy's per-call Python overhead.
#
# fine_filter_batch() refines ALL windows together: each iteration evaluates one
# trial time per window through ONE orbitcore.propagate_batch() crossing (GIL
# released in C++), and the bookkeeping is vectorized in NumPy — so the per-call
# boundary and the per-window scipy machinery are amortized away.
#
# The refinement is Newton's method on the relative RANGE-RATE, the standard
# operational TCA solve: the time of closest approach is the instant the range
# stops shrinking, i.e. where the relative position is perpendicular to the
# relative velocity,
#       g(t) = Δr·Δv = 0 ,   g'(t) = |Δv|² + Δr·Δa ≈ |Δv|²
# so each step is  t <- t - (Δr·Δv) / |Δv|²  (seconds). Δa (gravity gradient)
# is ~1e-4 of |Δv|² near an encounter, so this quasi-Newton step is effectively
# full Newton and converges quadratically from the medium-filter sample, which
# is already within one step of the TCA. The states propagate_batch returns
# give Δr AND Δv, so no extra work buys the derivative. Co-moving pairs
# (|Δv|≈0 — docked modules) don't step: any instant is equally close, and the
# downstream co-located suppression handles them.
_FINE_NEWTON_STEPS = 5        # converges in ~3; 5 for margin on a wide bracket
_FINE_CHUNK = 20000           # windows per propagate_batch pass (caps memory)
_EDGE_TOL_DAY = _EDGE_TOL_MIN / 1440.0       # bracket-edge tolerance, days


def _states(out: list) -> tuple:
    """(M, 3) positions and velocities from a propagate_batch result; a NaN row
    for any satellite that failed to propagate (decayed)."""
    pos = np.array(
        [it[0] if it is not None else (np.nan, np.nan, np.nan) for it in out],
        dtype=float)
    vel = np.array(
        [it[1] if it is not None else (np.nan, np.nan, np.nan) for it in out],
        dtype=float)
    return pos, vel


def _pair_states(sat_a, sat_b, ep_a, ep_b, jd):
    """Both satellites' TEME states at each window's time `jd` (absolute JD).
    One propagate_batch crossing per side; NaN rows where a satellite decayed."""
    pos_a, vel_a = _states(orbitcore.propagate_batch(
        sat_a, ((jd - ep_a) * 1440.0).tolist()))
    pos_b, vel_b = _states(orbitcore.propagate_batch(
        sat_b, ((jd - ep_b) * 1440.0).tolist()))
    return pos_a, vel_a, pos_b, vel_b


def _minimize_tca(sat_a, sat_b, ep_a, ep_b, lo, hi):
    """Vectorized TCA per window via Newton on the range-rate (see module note).

    Starts at the bracket midpoint (the medium-filter sample) and steps toward
    Δr·Δv = 0, clamped to [lo, hi] so a pathological window can't run away.
    Tracks the best (min-distance) time evaluated, so the result is never worse
    than a sample we took; NaN (decayed) distances never win.
    """
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    t = 0.5 * (lo + hi)                       # = jd_flag, the medium sample
    t_best = t.copy()
    d2_best = np.full(t.shape, np.inf)

    for _ in range(_FINE_NEWTON_STEPS):
        pos_a, vel_a, pos_b, vel_b = _pair_states(sat_a, sat_b, ep_a, ep_b, t)
        dr = pos_a - pos_b
        dv = vel_a - vel_b
        d2 = np.einsum("ij,ij->i", dr, dr)
        d2 = np.where(np.isnan(d2), np.inf, d2)
        better = d2 < d2_best
        t_best = np.where(better, t, t_best)
        d2_best = np.where(better, d2, d2_best)

        g = np.einsum("ij,ij->i", dr, dv)     # range-rate * |Δr|,  km²/s
        gprime = np.einsum("ij,ij->i", dv, dv)  # ≈ dg/dt,          km²/s²
        # Step only where |Δv| is real and non-zero: co-moving pairs (|Δv|≈0)
        # and decayed windows (NaN) keep step 0 — they don't move off the sample.
        step_sec = np.divide(g, gprime, out=np.zeros_like(g),
                             where=gprime > 1e-9)
        t = np.clip(t - step_sec / 86400.0, lo, hi)

    return t_best


def fine_filter_batch(satrecs: list, rows: list, step_sec: float) -> list:
    """Refine every medium-filter window at once; the batched fine stage.

    Args:
        satrecs:  the screen's index-aligned Satrec list.
        rows:     medium_filter output, list of (i, j, jd_flag, d_flag). The
                  bracket for each window is jd_flag +/- one step (the medium
                  filter guarantees the true TCA lies within it).
        step_sec: medium-filter step in seconds (sets the bracket half-width).

    Returns:
        A list aligned with `rows`; each entry is either None (a satellite in
        the pair could not be propagated across the window — decayed) or a dict
        matching fine_filter's geometry keys:
            jd_tca, miss_km, rel_speed_km_s,
            pos_a_teme, vel_a_teme, pos_b_teme, vel_b_teme
        tca_utc is intentionally omitted: run_screen builds it (via _jd_to_utc)
        only for the windows that survive the report cut, not all 1.4 M.
    """
    n = len(rows)
    results: list = [None] * n
    if n == 0:
        return results

    step_day = step_sec / 86400.0
    for start in range(0, n, _FINE_CHUNK):
        chunk = rows[start:start + _FINE_CHUNK]
        sat_a = [satrecs[r[0]] for r in chunk]
        sat_b = [satrecs[r[1]] for r in chunk]
        ep_a = np.array([_epoch_jd(s) for s in sat_a], dtype=float)
        ep_b = np.array([_epoch_jd(s) for s in sat_b], dtype=float)
        jd_flag = np.array([r[2] for r in chunk], dtype=float)
        lo = jd_flag - step_day
        hi = jd_flag + step_day

        t_star = _minimize_tca(sat_a, sat_b, ep_a, ep_b, lo, hi)

        # Edge-widen (mirrors fine_filter): if the minimum landed on a bracket
        # edge, the true TCA likely sits just outside — widen that window by its
        # own width on that side and re-minimize once. Rare: the medium bracket
        # normally contains the TCA (the velocity-aware no-skip bound), so this
        # is a safety net for a pathological row, not a routine path.
        at_lo = (t_star - lo) < _EDGE_TOL_DAY
        at_hi = (hi - t_star) < _EDGE_TOL_DAY
        edge = at_lo | at_hi
        if edge.any():
            idx = np.nonzero(edge)[0]
            width = hi[idx] - lo[idx]
            wlo = np.where(at_lo[idx], lo[idx] - width, lo[idx])
            whi = np.where(at_hi[idx], hi[idx] + width, hi[idx])
            t_star[idx] = _minimize_tca(
                [sat_a[k] for k in idx], [sat_b[k] for k in idx],
                ep_a[idx], ep_b[idx], wlo, whi)

        # Authoritative states (position + velocity) at each refined TCA.
        pos_a, vel_a, pos_b, vel_b = _pair_states(
            sat_a, sat_b, ep_a, ep_b, t_star)
        d = pos_a - pos_b
        miss = np.sqrt(np.einsum("ij,ij->i", d, d))
        dv = vel_a - vel_b
        rel_speed = np.sqrt(np.einsum("ij,ij->i", dv, dv))

        for k in range(len(chunk)):
            if not math.isfinite(miss[k]):
                continue  # decayed at TCA — drop, like fine_filter's RuntimeError
            # Plain float tuples (not numpy scalars) to match fine_filter's
            # output shape and stay clean through teme_to_rtn + JSON.
            results[start + k] = {
                "jd_tca": float(t_star[k]),
                "miss_km": float(miss[k]),
                "rel_speed_km_s": float(rel_speed[k]),
                "pos_a_teme": tuple(float(x) for x in pos_a[k]),
                "vel_a_teme": tuple(float(x) for x in vel_a[k]),
                "pos_b_teme": tuple(float(x) for x in pos_b[k]),
                "vel_b_teme": tuple(float(x) for x in vel_b[k]),
            }
    return results


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
    fused: bool = False,
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
        fused:           when True, run the fused C++ stage (screen_pairs) that
                         computes the coarse cut and the medium scan in one call
                         so survivor pairs never materialize as Python objects
                         (Phase 10.1a — the memory lever for the full catalog).
                         Byte-identical events to the default two-call path;
                         default False keeps the exact Phase-6/7 path.
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

    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    jd_w, jd_f = orbitcore.jday(
        start_utc.year, start_utc.month, start_utc.day,
        start_utc.hour, start_utc.minute,
        start_utc.second + start_utc.microsecond / 1e6,
    )
    jd_start = jd_w + jd_f
    jd_end = jd_start + duration_hours / 24.0

    if fused:
        # Fused stage (Phase 10.1a): one C++ call does the coarse cut AND the
        # medium scan, so the survivor pairs never materialize as Python objects
        # (the full active catalog's ~48 M pairs ≈ 5 GB of tuples; ~0.8 GB as
        # the internal C++ vector). Byte-identical rows to the two-call path
        # below. t_coarse folds into the scan; n_pairs comes back for the
        # survivor-reduction profile.
        _t = time.perf_counter()
        n_pairs, rows = orbitcore.screen_pairs(
            satrecs, periapsis, apoapsis, pad_km,
            jd_start, jd_end, step_sec, gross_km)
        if timings is not None:
            timings["n_sats"] = len(satrecs)
            timings["n_pairs"] = n_pairs
            timings["t_coarse"] = 0.0
            timings["t_medium"] = time.perf_counter() - _t
            timings["n_windows"] = len(rows)
        if n_pairs == 0:
            if timings is not None:
                timings["n_events"] = 0
                timings["n_suppressed"] = 0
                timings["t_fine"] = 0.0
            return []
    else:
        # ⚠ PERF (Phase 7, scaling_tracker #3): coarse_filter hands survivor
        # pairs back to Python and we pass them straight into medium_filter —
        # millions of (i, j) tuples crossing the C++↔Python boundary twice
        # (~378 ns/pair each way, measured 6.2). Measured on the full 10,544-sat
        # Starlink catalog (7.1): 49% of all pairs survive at 50 km (27.3 M) —
        # altitude-band screening is inclination-blind. The fused=True path
        # (Phase 10.1a, screen_pairs) eliminates this materialization.
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

        _t = time.perf_counter()
        rows = orbitcore.medium_filter(
            satrecs, pairs, jd_start, jd_end, step_sec, gross_km)
        if timings is not None:
            timings["t_medium"] = time.perf_counter() - _t
            timings["n_windows"] = len(rows)

    _t = time.perf_counter()
    # Refine every window in one batched pass (Phase 7.3) rather than a per-window
    # scipy call: fine[k] aligns with rows[k], or is None if the pair decayed
    # across its window. This is the 7.1-profiled bottleneck (82-87% of wall time).
    fine = fine_filter_batch(satrecs, rows, step_sec)
    events = []
    n_suppressed = 0
    for (i, j, _jd_flag, _d_flag), out in zip(rows, fine):
        if out is None:
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
            "tca": _jd_to_utc(out["jd_tca"]).isoformat(),
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
