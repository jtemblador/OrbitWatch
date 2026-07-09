#!/usr/bin/env python3
"""
Phase 10.0 measurement gate — throwaway NumPy prototype of the smart-sieve
"path filter" bound (Hoots-Crawford-Roehrich 1984 Filter II) + a time-filter
(Filter III) work estimate, run against the real catalogs.

Purpose: measure, BEFORE writing any C++, (a) what fraction of coarse-surviving
pairs a conservative no-skip path cut can drop, and (b) the ceiling of the
time filter's medium-scan reduction. Only the numbers land in the task log.

The conservative drop test (per pair, both relative nodes):
    d(P1,P2) >= dist(P2, plane1) = r2*sin(I_R)*|sin u2|   (P1 in plane 1)
    d(P1,P2) >= | |r1| - |r2| |                           (reverse triangle)
  A close approach within D_eff therefore requires BOTH points inside the
  angular windows |sin u| <= D_eff/(r_p*sin I_R) around the mutual node line,
  AND the radius intervals over those windows to come within D_eff.
  Opposite-node combinations are excluded by the axis projection
  (d >= r1+r2 - eps >> D_eff). Drop iff both nodes' radius-interval gaps
  exceed D_eff.

Geometry is evaluated at SCREEN time (elements secularly advanced from their
per-sat epochs via J2 rates), and all windows are widened for precession
across the screening window — the no-skip discipline the C++ version will
formalize (reading nodedot/argpdot straight off the satrec).
"""
import argparse
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

_ROOT = "/home/j0e/Projects/OrbitWatch"
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orbitcore                                            # noqa: E402
from backend.core.propagator import build_satrecs_and_meta  # noqa: E402
from backend.core.conjunctions import fine_filter           # noqa: E402
from backend.core.screening_volumes import is_screenable    # noqa: E402

RE_KM = 6378.135          # WGS-72, matches the engine
J2 = 1.082616e-3          # WGS-72

# Gate margins ("realistic" mode). 10.1 formalizes these with citations.
OSC_MARGIN_KM = 10.0      # SGP4 mean-ellipse vs osculating short-period (J2)
DRIFT_KM_PER_DAY = 1.0    # secular drag/element drift over the window
RATE_MODEL_FRAC = 0.01    # J2-rate formula vs SGP4's internal secular rates


def load_elements(df: pd.DataFrame) -> dict:
    """Per-sat element arrays (radians, km, rad/day) + J2 secular rates."""
    d = {}
    d["inc"] = np.radians(df["inclination"].to_numpy(float))
    d["raan"] = np.radians(df["ra_of_asc_node"].to_numpy(float))
    d["argp"] = np.radians(df["arg_of_pericenter"].to_numpy(float))
    d["ecc"] = df["eccentricity"].to_numpy(float)
    d["a"] = df["semimajor_axis"].to_numpy(float)
    d["rp"] = df["periapsis"].to_numpy(float) + RE_KM     # radius, km
    d["ra_r"] = df["apoapsis"].to_numpy(float) + RE_KM
    d["per_alt"] = df["periapsis"].to_numpy(float)        # altitude (coarse)
    d["apo_alt"] = df["apoapsis"].to_numpy(float)
    n_rad_day = df["mean_motion"].to_numpy(float) * 2.0 * math.pi  # rad/day
    p = d["a"] * (1.0 - d["ecc"] ** 2)
    fac = 1.5 * J2 * (RE_KM / p) ** 2 * n_rad_day
    d["raan_dot"] = -fac * np.cos(d["inc"])                        # rad/day
    d["argp_dot"] = 0.5 * fac * (5.0 * np.cos(d["inc"]) ** 2 - 1.0)
    # days from each sat's epoch to the screen start (for secular advance)
    d["epoch_jd"] = np.array([
        _jd(ts.to_pydatetime()) for ts in pd.to_datetime(df["epoch"])])
    return d


def _jd(dt: datetime) -> float:
    jd, fr = orbitcore.jday(dt.year, dt.month, dt.day,
                            dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
    return jd + fr


def advance_to(d: dict, jd_start: float) -> dict:
    """Secularly advance RAAN/argp from per-sat epoch to the screen start,
    and record the advance magnitude (its model error widens the windows)."""
    age = jd_start - d["epoch_jd"]                       # days (can be < 0)
    out = dict(d)
    out["raan"] = d["raan"] + d["raan_dot"] * age
    out["argp"] = d["argp"] + d["argp_dot"] * age
    out["adv_err_u"] = RATE_MODEL_FRAC * (
        np.abs(d["raan_dot"] * age) + np.abs(d["argp_dot"] * age))  # rad
    return out


def _cos_interval_bounds(center: np.ndarray, half: np.ndarray):
    """Min/max of cos(x) over [center-half, center+half] (vectorized).
    half >= pi covers the full circle."""
    c = np.mod(center, 2.0 * math.pi)
    lo, hi = c - half, c + half
    full = half >= math.pi
    ce, cl = np.cos(lo), np.cos(hi)
    cmax = np.maximum(ce, cl)
    cmin = np.minimum(ce, cl)
    # 0 (mod 2pi) inside [lo, hi]?  <=> mod(-lo, 2pi) <= hi-lo
    has0 = np.mod(-lo, 2.0 * math.pi) <= (hi - lo)
    haspi = np.mod(math.pi - lo, 2.0 * math.pi) <= (hi - lo)
    cmax = np.where(full | has0, 1.0, cmax)
    cmin = np.where(full | haspi, -1.0, cmin)
    return cmin, cmax


def _radius_interval(p, e, nu_c, nu_h, pad):
    """[r_min, r_max] of r = p/(1+e cos nu) over nu in [nu_c-nu_h, nu_c+nu_h]."""
    cmin, cmax = _cos_interval_bounds(nu_c, nu_h)
    return p / (1.0 + e * cmax) - pad, p / (1.0 + e * cmin) + pad


def pair_block(d, I, J, D_gross, T_days, realistic):
    """Vectorized pair metrics for index arrays I, J (same length).
    Returns dict of masks/arrays: coarse, drop, frac (time-filter fraction)."""
    D_eff = D_gross + (OSC_MARGIN_KM + DRIFT_KM_PER_DAY * T_days
                       if realistic else 0.0)

    per_i, apo_i = d["per_alt"][I], d["apo_alt"][I]
    per_j, apo_j = d["per_alt"][J], d["apo_alt"][J]
    coarse = (per_i <= apo_j + D_gross) & (per_j <= apo_i + D_gross)

    i1, o1 = d["inc"][I], d["raan"][I]
    i2, o2 = d["inc"][J], d["raan"][J]
    si1, ci1, si2, ci2 = np.sin(i1), np.cos(i1), np.sin(i2), np.cos(i2)
    # h-hat = (sin i sin O, -sin i cos O, cos i); k = h1 x h2 (node line)
    h1 = np.stack([si1 * np.sin(o1), -si1 * np.cos(o1), ci1], axis=-1)
    h2 = np.stack([si2 * np.sin(o2), -si2 * np.cos(o2), ci2], axis=-1)
    k = np.cross(h1, h2)
    sinIR = np.linalg.norm(k, axis=-1)
    sinIR = np.maximum(sinIR, 1e-12)

    def node_angle(hh, oo):
        nhat = np.stack([np.cos(oo), np.sin(oo), np.zeros_like(oo)], axis=-1)
        that = np.cross(hh, nhat)   # in-plane, 90 deg ahead of asc node
        return np.arctan2(np.einsum("...k,...k->...", k, that),
                          np.einsum("...k,...k->...", k, nhat))

    u1 = node_angle(h1, o1)
    u2 = node_angle(h2, o2)

    rp1, rp2 = d["rp"][I], d["rp"][J]
    du1 = np.arcsin(np.clip(D_eff / (rp1 * sinIR), 0.0, 1.0))
    du2 = np.arcsin(np.clip(D_eff / (rp2 * sinIR), 0.0, 1.0))

    if realistic:
        rel_odot = np.abs(d["raan_dot"][I] - d["raan_dot"][J])   # rad/day
        prec1 = rel_odot * T_days * (1.0 + 2.0 * si2 / sinIR) \
            + d["adv_err_u"][I]
        prec2 = rel_odot * T_days * (1.0 + 2.0 * si1 / sinIR) \
            + d["adv_err_u"][J]
        prec1 = np.minimum(prec1, math.pi)
        prec2 = np.minimum(prec2, math.pi)
        w1 = np.abs(d["argp_dot"][I]) * T_days                   # nu widen
        w2 = np.abs(d["argp_dot"][J]) * T_days
    else:
        prec1 = prec2 = w1 = w2 = 0.0

    p1 = d["a"][I] * (1.0 - d["ecc"][I] ** 2)
    p2 = d["a"][J] * (1.0 - d["ecc"][J] ** 2)
    nu1_h = du1 + prec1 + w1
    nu2_h = du2 + prec2 + w2

    def gap_at(off):
        r1lo, r1hi = _radius_interval(
            p1, d["ecc"][I], u1 + off - d["argp"][I], nu1_h, 0.0)
        r2lo, r2hi = _radius_interval(
            p2, d["ecc"][J], u2 + off - d["argp"][J], nu2_h, 0.0)
        return np.maximum(0.0, np.maximum(r2lo - r1hi, r1lo - r2hi))

    drop = (gap_at(0.0) > D_eff) & (gap_at(math.pi) > D_eff) & coarse

    # Time-filter ceiling: fraction of scan steps where both objects can be
    # inside their node windows (2 nodes, independent-phase estimate).
    e1 = np.minimum(du1 + prec1, math.pi / 2)
    e2 = np.minimum(du2 + prec2, math.pi / 2)
    frac = np.minimum(1.0, 2.0 * (e1 / math.pi) * (e2 / math.pi))
    frac = np.where(drop, 0.0, frac)
    return {"coarse": coarse, "drop": drop, "frac": frac, "sinIR": sinIR}


def sweep(d, D_gross, T_days, realistic, flagged=None):
    """Full upper-triangle sweep in row blocks sized to ~4M pairs (keeps the
    ~20 float64 temporaries under ~1 GB). If `flagged` (a set of (i,j)) is
    given, collects dropped pairs that the medium filter flagged."""
    n = len(d["inc"])
    block = max(64, int(4e6 / max(n, 1)))
    tot = coarse = dropped = 0
    frac_sum = 0.0
    ecc_pairs_dropped = 0
    violations = []
    ecc_mask = d["ecc"] >= 0.01
    for a in range(0, n, block):
        b = min(a + block, n)
        ii = np.arange(a, b)
        II = np.repeat(ii, n - 1 - ii)
        JJ = np.concatenate([np.arange(i + 1, n) for i in ii]) \
            if len(ii) else np.array([], dtype=int)
        m = pair_block(d, II, JJ, D_gross, T_days, realistic)
        tot += len(II)
        coarse += int(m["coarse"].sum())
        dropped += int(m["drop"].sum())
        frac_sum += float(m["frac"][m["coarse"]].sum())
        ecc_pairs_dropped += int((m["drop"] & (ecc_mask[II] | ecc_mask[JJ])).sum())
        if flagged is not None and m["drop"].any():
            for i, j in zip(II[m["drop"]], JJ[m["drop"]]):
                if (int(i), int(j)) in flagged:
                    violations.append((int(i), int(j)))
    return {"tot": tot, "coarse": coarse, "drop": dropped,
            "frac_sum": frac_sum, "ecc_drop": ecc_pairs_dropped,
            "violations": violations}


def noskip_check(df, D, hours, step_sec, jd_start, realistic):
    """Ground truth: run real coarse+medium; assert no flagged pair is dropped."""
    satrecs, meta = build_satrecs_and_meta(df)
    per = [m["periapsis_km"] for m in meta]
    apo = [m["apoapsis_km"] for m in meta]
    t0 = time.perf_counter()
    pairs = orbitcore.coarse_filter(per, apo, D)
    rows = orbitcore.medium_filter(
        satrecs, pairs, jd_start, jd_start + hours / 24.0, step_sec, D)
    t_truth = time.perf_counter() - t0
    del pairs  # 25M+ tuples at full catalog (~4.5 GB) — free before the sweep
    flagged = set((min(i, j), max(i, j)) for i, j, _, _ in rows)

    d = advance_to(load_elements(df), jd_start)
    res = sweep(d, D, hours / 24.0, realistic, flagged=flagged)
    res["n_flagged_pairs"] = len(flagged)
    res["n_windows"] = len(rows)
    res["t_truth"] = t_truth

    # For strict violations, the fine oracle decides if any is a TRUE miss <= D
    true_viol = []
    if res["violations"]:
        vset = set(res["violations"])
        by_pair = {}
        for i, j, jd, _dist in rows:
            key = (min(i, j), max(i, j))
            if key in vset:
                by_pair.setdefault(key, []).append(jd)
        step_day = step_sec / 86400.0
        for (i, j), jds in by_pair.items():
            best = math.inf
            for jd in jds:
                try:
                    out = fine_filter(satrecs[i], satrecs[j],
                                      jd - step_day, jd + step_day)
                    best = min(best, out["miss_km"])
                except (RuntimeError, ValueError):
                    pass
            if best <= D:
                true_viol.append((i, j, best))
    res["true_violations"] = true_viol
    return res


def fmt_pct(x, base):
    return f"{x:,} ({100.0 * x / max(base, 1):.1f}%)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--gross", type=float, default=51.0)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--step", type=float, default=60.0)
    ap.add_argument("--start", default=None, help="ISO start (default: median epoch)")
    ap.add_argument("--max-sats", type=int, default=None, help="head-slice")
    ap.add_argument("--screenable-only", action="store_true")
    ap.add_argument("--noskip", action="store_true",
                    help="also run the real coarse+medium ground-truth check")
    args = ap.parse_args()

    df = pd.read_parquet(args.parquet)
    # Match build_snapshot.py's production order: head-slice, THEN screenable.
    if args.max_sats and len(df) > args.max_sats:
        df = df.head(args.max_sats).reset_index(drop=True)
    if args.screenable_only:
        keep = [is_screenable(p, e, pr) for p, e, pr in
                zip(df["periapsis"], df["eccentricity"], df["period"])]
        df = df[keep].reset_index(drop=True)

    if args.start:
        start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    else:
        start = pd.to_datetime(df["epoch"]).median().to_pydatetime()
    jd_start = _jd(start)
    T = args.hours / 24.0

    print(f"catalog={args.parquet}  n={len(df)}  gross={args.gross} km  "
          f"window={args.hours} h  start={start.isoformat()}")

    d = advance_to(load_elements(df), jd_start)
    for label, realistic in (("idealized (no margins)", False),
                             ("realistic (margins on)", True)):
        t0 = time.perf_counter()
        r = sweep(d, args.gross, T, realistic)
        dt = time.perf_counter() - t0
        after = r["coarse"] - r["drop"]
        print(f"\n[{label}]  ({dt:.0f}s)")
        print(f"  total pairs      {r['tot']:,}")
        print(f"  coarse survive   {fmt_pct(r['coarse'], r['tot'])}")
        print(f"  path drops       {fmt_pct(r['drop'], r['coarse'])} of coarse"
              f"   [{r['ecc_drop']:,} involve e>=0.01]")
        print(f"  path survive     {fmt_pct(after, r['tot'])} of total")
        if realistic:
            print(f"  time-filter ceiling: predicted medium pair-step work = "
                  f"{100.0 * r['frac_sum'] / max(r['coarse'], 1):.2f}% "
                  f"of coarse-survivor full scan")

    if args.noskip:
        print("\n[no-skip ground truth: real coarse+medium on this catalog]")
        res = noskip_check(df, args.gross, args.hours, args.step, jd_start,
                           realistic=True)
        print(f"  medium: {res['n_windows']:,} windows over "
              f"{res['n_flagged_pairs']:,} flagged pairs "
              f"({res['t_truth']:.0f}s)")
        print(f"  strict violations (dropped but medium-flagged): "
              f"{len(res['violations'])}")
        print(f"  TRUE violations (fine miss <= gross): "
              f"{len(res['true_violations'])}  {res['true_violations'][:10]}")


if __name__ == "__main__":
    main()
