#!/usr/bin/env python3
"""
Phase 10.1b measurement gate (v3, the validated construction) — the TIME filter
(Hoots-Crawford-Roehrich Filter III), proven no-skip BEFORE any C++. This file
is the EXECUTABLE SPEC and the independent oracle for the 10.2 C++ build; see
stage1b_timefilter_spec.md for the prose version and the results table.

The gate rule (per pair, at a time t): a conjunction can only occur while BOTH
satellites are within a small angular window of the mutual node line (the
intersection of their orbit planes):
    |u1(t) - u1_node(t)| <= du1 + margins   AND   same for sat 2   (mod pi)
with du_k = arcsin(D_eff / (r_p * sin I_R)); near-coplanar pairs are always
active (kept). The exactness of the underlying predicate was verified directly:
at the fine-refined TCA of 8,206 real events, the osculating perpendicular
plane-distances |r1.h2|, |r2.h1| never exceeded the gross threshold (0
violations) — a mathematical necessary condition for a close approach.

The v3 construction (each element cures a failure mode this gate itself found
and measured; the naive filter fails all four ways):
  1. VALIDATE AT TCA, NOT AT jd_flag — the medium filter's flag time is its
     best SAMPLED step, ~1 step (~2 deg of motion) from the true TCA; checking
     membership there manufactures false violations. The C++ time windows get
     the step motion back as a +-1-step pad in TIME (exact), never angular.
  2. ANCHOR AT SCREEN START, NOT EPOCH — SGP4's drag-secular terms compound
     over stale epochs (measured 35 deg along-track at a 21.8-day epoch age).
     THREE propagations per sat (start / mid / end of the scan window),
     rv2coe'd to osculating elements.
  3. EQUINOCTIAL CHORD RATES — rates are finite differences over the window
     chord (captures true local drift: J2 + drag + resonance), taken on the
     mean longitude lam = argp + M, because the osculating (argp, M) SPLIT is
     ill-conditioned for near-circular orbits (J2's forced ecc-vector wobble
     ~1e-3 ~ e itself; an ordinary e=0.0011 sat broke the M unwrap). The split
     survives only inside the equation-of-center, where error <= 2e.
  4. PER-SAT MEASURED CURVATURE MARGIN — the midpoint second difference of lam
     bounds the chord's interior deviation (exactly, for quadratic drift;
     x1.5 for tails). ~0 for normal sats, ~6 deg for an actively-reentering
     143-km-perigee Starlink (ndot 0.127 rev/day^2) — the flat-margin killer.

Validated: 1,403,999 real events across 4 catalogs, 0 uncovered at a 0.25 deg
base margin; the zero-margin mutation breaks every catalog (the check bites).
"""
import argparse
import math
import os
import sys

import numpy as np
import pandas as pd

_ROOT = "/home/j0e/Projects/OrbitWatch"
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, os.path.join(_ROOT, "backend"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orbitcore                                              # noqa: E402
from path_gate import OSC_MARGIN_KM, _jd                      # noqa: E402
from backend.core.propagator import build_satrecs_and_meta    # noqa: E402
from backend.core.conjunctions import fine_filter_batch, _epoch_jd  # noqa: E402
from backend.core.screening_volumes import is_screenable      # noqa: E402


# ---------------------------------------------------------------------------
# Anchored elements: osculating states at the scan window's start / mid / end
# (3 propagations per sat) -> classical elements; equinoctial chord rates over
# the window + a measured per-sat curvature margin. This is exactly what the
# C++ window builder will do.
# ---------------------------------------------------------------------------

def _osculating(satrecs, jd):
    """Osculating classical elements at jd from one propagate_batch call."""
    ep = np.array([_epoch_jd(s) for s in satrecs])
    out = orbitcore.propagate_batch(satrecs, ((jd - ep) * 1440.0).tolist())
    r = np.array([o[0] if o else (np.nan,) * 3 for o in out])
    v = np.array([o[1] if o else (np.nan,) * 3 for o in out])
    mu = orbitcore.getgravconst(orbitcore.GravConst.WGS72)["mus"]

    rmag = np.linalg.norm(r, axis=1)
    h = np.cross(r, v)
    hmag = np.maximum(np.linalg.norm(h, axis=1), 1e-12)
    hh = h / hmag[:, None]
    inc = np.arccos(np.clip(hh[:, 2], -1.0, 1.0))
    raan = np.arctan2(hh[:, 0], -hh[:, 1])          # atan2(n_y, n_x), n = z x h
    rv = np.einsum("ij,ij->i", r, v)
    v2 = np.einsum("ij,ij->i", v, v)
    evec = ((v2 - mu / rmag)[:, None] * r - rv[:, None] * v) / mu
    ecc = np.linalg.norm(evec, axis=1)
    nvec = np.stack([-hh[:, 1], hh[:, 0], np.zeros(len(r))], axis=-1)
    nhat = nvec / np.maximum(np.linalg.norm(nvec, axis=1), 1e-12)[:, None]
    ehat = evec / np.maximum(ecc, 1e-12)[:, None]
    te = np.cross(hh, ehat)
    nu = np.arctan2(np.einsum("ij,ij->i", r, te),
                    np.einsum("ij,ij->i", r, ehat))
    that = np.cross(hh, nhat)
    u = np.arctan2(np.einsum("ij,ij->i", r, that),
                   np.einsum("ij,ij->i", r, nhat))
    argp = u - nu
    E = 2.0 * np.arctan2(np.sqrt(1.0 - ecc) * np.sin(nu / 2.0),
                         np.sqrt(1.0 + ecc) * np.cos(nu / 2.0))
    M = E - ecc * np.sin(E)
    a = 1.0 / np.maximum(2.0 / rmag - v2 / mu, 1e-12)
    return {"ok": np.isfinite(rmag), "inc": inc, "raan": raan, "argp": argp,
            "M": M, "ecc": ecc, "a": a}


def _wrap_pi(x):
    return np.mod(x + math.pi, 2.0 * math.pi) - math.pi


def anchor_at(satrecs, jd0, jd1=None):
    """Three-point anchor: osculating elements at jd0, the midpoint, and jd1.
    Rates are chord finite-differences over [jd0, jd1] — they capture the TRUE
    local drift (J2 secular + drag-secular + deep-space resonance), which the
    satrec's at-epoch linear rates miss for high-drag sats with stale epochs
    (measured: ~3 deg/day of extra mean-anomaly rate at a 20-day epoch age).
    The midpoint measures each sat's drift CURVATURE, which becomes its
    personal window margin (see the curv note below). All unwraps are taken
    against the satrec's reference rates (branch-safe: drag shifts the true
    advance by only ~degrees over one day). Falls back to the satrec's linear
    rates for any sat that fails a propagation — pair_active_at then treats it
    as always-active (conservative keep)."""
    if jd1 is None:
        jd1 = jd0 + 1.0
    T = jd1 - jd0
    e0 = _osculating(satrecs, jd0)
    e1 = _osculating(satrecs, jd1)
    em = _osculating(satrecs, jd0 + T / 2.0)     # midpoint (curvature anchor)
    n_ref = np.array([s.mdot for s in satrecs]) * 1440.0        # rad/day
    argp_ref = np.array([s.argpdot for s in satrecs]) * 1440.0
    raan_ref = np.array([s.nodedot for s in satrecs]) * 1440.0

    both = e0["ok"] & e1["ok"]
    raan_dot = np.where(both,
                        _wrap_pi(e1["raan"] - e0["raan"]) / T, raan_ref)

    # ⚠ Near-circular conditioning: the osculating (argp, M) SPLIT is
    # ill-defined when e ~ J2's forced ecc-vector wobble (~1e-3) — the perigee
    # direction can rotate arbitrarily between the two anchors, and if it
    # crosses +-180 deg the M unwrap picks the wrong branch (observed: one
    # ordinary 573x588 km sat, e=0.0011, produced margin-independent misses).
    # The equinoctial cure: chord the MEAN LONGITUDE lam = argp + M, whose sum
    # is well-conditioned regardless of the split; unwrap against the combined
    # reference rate (drag shifts the true advance by only ~degrees over the
    # chord — far inside the +-half-rev branch window). The (argp, M) split is
    # still chorded for the equation-of-center term, where any branch error is
    # suppressed by e itself (EoC <= 2e rad: negligible exactly when the split
    # is untrustworthy; well-conditioned when e is large enough to matter).
    lam0 = e0["argp"] + e0["M"]
    lam1 = e1["argp"] + e1["M"]
    lam_ref = n_ref + argp_ref
    dlam = _wrap_pi(lam1 - lam0 - lam_ref * T) + lam_ref * T
    lam_rate = np.where(both, dlam / T, lam_ref)

    dM = _wrap_pi(e1["M"] - e0["M"] - n_ref * T) + n_ref * T
    m_rate = np.where(both, dM / T, n_ref)

    # Per-sat curvature margin from the midpoint second difference: for a
    # quadratic drift lam(t) = lam0 + r*t + c*t^2/2, the chord's max interior
    # deviation is c*T^2/8 = |lam0 - 2*lam_mid + lam1| / 2. Model-free — it
    # measures whatever SGP4 actually does (t^2 drag for near-reentry sats
    # where isimp=1 makes the drift EXACTLY quadratic, deep-space resonance,
    # even bogus negative-bstar fits). ~0 for normal sats; ~6 deg for an
    # actively-reentering 143-km-perigee Starlink (ndot 0.127 rev/day^2 —
    # measured, the sole 2-deg-flat-margin survivor). x1.5 for higher-order
    # tails. Sats with any failed anchor fall back to always-active anyway.
    lam_m = em["argp"] + em["M"]
    ref_half = lam_ref * (T / 2.0)                  # branch-safe unwrap ref
    d1 = _wrap_pi(lam_m - lam0 - ref_half) + ref_half
    d2 = _wrap_pi(lam1 - lam_m - ref_half) + ref_half
    curv = np.where(both & em["ok"], 1.5 * np.abs(d2 - d1) / 2.0, 0.0)

    return {
        "ok": e0["ok"] & e1["ok"] & em["ok"],       # any anchor failure ->
        # pair_active_at treats the sat as always-active (conservative)
        "inc": e0["inc"], "raan0": e0["raan"],
        "lam0": lam0, "lam_rate": lam_rate,
        "M0": e0["M"], "m_rate": m_rate, "ecc": e0["ecc"],
        "rp": e0["a"] * (1.0 - e0["ecc"]),          # perigee radius, km
        "raan_dot": raan_dot, "curv": curv,
    }


def _kepler_nu_from_M(M, e):
    M = np.mod(M + math.pi, 2.0 * math.pi) - math.pi
    E = M.copy()
    for _ in range(12):
        E = E - (E - e * np.sin(E) - M) / (1.0 - e * np.cos(E))
    return 2.0 * np.arctan2(np.sqrt(1.0 + e) * np.sin(E / 2.0),
                            np.sqrt(1.0 - e) * np.cos(E / 2.0))


def _ang_dist_to_node(u, u_node):
    d = np.mod(u - u_node, math.pi)
    return np.minimum(d, math.pi - d)


def pair_active_at(d, I, J, t, jd0, D_eff, u_margin):
    """Is each pair (I[k], J[k]) inside its angular node windows at time t[k]?
    Elements anchored at jd0 and advanced by the CHORD rates from anchor_at;
    window width = arcsin bound + flat u_margin + each sat's measured curvature
    margin. Node geometry is recomputed at t (nodal precession exact).
    Near-coplanar pairs and pairs with a failed anchor are always active."""
    dt = t - jd0
    o1 = d["raan0"][I] + d["raan_dot"][I] * dt
    o2 = d["raan0"][J] + d["raan_dot"][J] * dt
    lam1 = d["lam0"][I] + d["lam_rate"][I] * dt
    lam2 = d["lam0"][J] + d["lam_rate"][J] * dt
    M1 = d["M0"][I] + d["m_rate"][I] * dt
    M2 = d["M0"][J] + d["m_rate"][J] * dt
    i1, i2 = d["inc"][I], d["inc"][J]
    si1, si2 = np.sin(i1), np.sin(i2)

    h1 = np.stack([si1 * np.sin(o1), -si1 * np.cos(o1), np.cos(i1)], axis=-1)
    h2 = np.stack([si2 * np.sin(o2), -si2 * np.cos(o2), np.cos(i2)], axis=-1)
    k = np.cross(h1, h2)
    sinIR = np.maximum(np.linalg.norm(k, axis=-1), 1e-12)

    def node_angle(hh, oo):
        nhat = np.stack([np.cos(oo), np.sin(oo), np.zeros_like(oo)], axis=-1)
        that = np.cross(hh, nhat)
        return np.arctan2(np.einsum("...k,...k->...", k, that),
                          np.einsum("...k,...k->...", k, nhat))

    # u = lam + equation-of-center (nu - M): lam carries the well-conditioned
    # phase; the EoC's (argp, M)-split sensitivity is suppressed by e itself.
    # Both nu and M must be wrapped consistently before differencing (lam/M are
    # unwrapped over many revs), and the EoC re-wrapped — it is <= 2e < pi.
    eoc1 = _wrap_pi(_kepler_nu_from_M(M1, d["ecc"][I]) - _wrap_pi(M1))
    eoc2 = _wrap_pi(_kepler_nu_from_M(M2, d["ecc"][J]) - _wrap_pi(M2))
    u1 = lam1 + eoc1
    u2 = lam2 + eoc2
    u1n, u2n = node_angle(h1, o1), node_angle(h2, o2)

    rp1, rp2 = d["rp"][I], d["rp"][J]
    du1 = (np.arcsin(np.clip(D_eff / (rp1 * sinIR), 0.0, 1.0))
           + u_margin + d["curv"][I])
    du2 = (np.arcsin(np.clip(D_eff / (rp2 * sinIR), 0.0, 1.0))
           + u_margin + d["curv"][J])
    coplanar = (rp1 * sinIR <= D_eff) | (rp2 * sinIR <= D_eff)
    bad = ~(d["ok"][I] & d["ok"][J])

    return (coplanar | bad |
            ((_ang_dist_to_node(u1, u1n) <= du1) &
             (_ang_dist_to_node(u2, u2n) <= du2)))


# ---------------------------------------------------------------------------
# The gate: refine ALL flagged windows -> every real event checked at its TCA.
# ---------------------------------------------------------------------------

def run_gate(df, gross, hours, step_sec, jd0, margins_deg, cache=None):
    satrecs, meta = build_satrecs_and_meta(df)

    if cache and os.path.exists(cache):
        z = np.load(cache)
        I, J, T, miss = z["I"], z["J"], z["T"], z["miss"]
        print(f"  [events loaded from cache: {len(I):,}]")
    else:
        per = [m["periapsis_km"] for m in meta]
        apo = [m["apoapsis_km"] for m in meta]
        n_pairs, rows = orbitcore.screen_pairs(
            satrecs, per, apo, gross, jd0, jd0 + hours / 24.0, step_sec, gross)
        print(f"  flagged windows: {len(rows):,}  (coarse pairs={n_pairs:,})")
        fine = fine_filter_batch(satrecs, rows, step_sec)
        ev = [(r[0], r[1], f["jd_tca"], f["miss_km"]) for r, f in
              zip(rows, fine) if f is not None and f["miss_km"] <= gross]
        if not ev:
            print("  no real events")
            return
        I = np.array([e[0] for e in ev])
        J = np.array([e[1] for e in ev])
        T = np.array([e[2] for e in ev])
        miss = np.array([e[3] for e in ev])
        if cache:
            np.savez(cache, I=I, J=J, T=T, miss=miss)
    print(f"  real events (fine miss <= gross): {len(I):,}")

    d = anchor_at(satrecs, jd0, jd0 + hours / 24.0)   # chord over the window
    D_eff = gross + OSC_MARGIN_KM
    for mdeg in margins_deg:
        act = pair_active_at(d, I, J, T, jd0, D_eff, math.radians(mdeg))
        nviol = int((~act).sum())
        worst = [(int(I[k]), int(J[k]), round(float(miss[k]), 2))
                 for k in np.nonzero(~act)[0][:6]]
        print(f"  u_margin={mdeg:>4}deg: TCA-uncovered real events = "
              f"{nviol}   {worst if nviol else ''}")
    return d, satrecs, I, J, T


def coverage(df, d, gross, hours, step_sec, jd0, u_margin,
             sample_pairs=4000, step_stride=8):
    """Mean fraction of scan steps a coarse-surviving pair is active — the
    realized pair-step reduction, with the final margin."""
    D_eff = gross + OSC_MARGIN_KM
    per = df["periapsis"].to_numpy(float)
    apo = df["apoapsis"].to_numpy(float)
    n = len(df)
    rng = np.random.RandomState(0)
    ii = rng.randint(0, n, size=sample_pairs * 4)
    jj = rng.randint(0, n, size=sample_pairs * 4)
    ok = ii < jj
    ii, jj = ii[ok], jj[ok]
    coarse = (per[ii] <= apo[jj] + gross) & (per[jj] <= apo[ii] + gross)
    ii, jj = ii[coarse][:sample_pairs], jj[coarse][:sample_pairs]
    steps = np.arange(0, int(hours * 3600 / step_sec), step_stride)
    times = jd0 + steps * (step_sec / 86400.0)
    frac = np.zeros(len(ii))
    for t in times:
        frac += pair_active_at(d, ii, jj, np.full(len(ii), t), jd0,
                               D_eff, u_margin)
    return float(frac.mean() / len(times))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--gross", type=float, default=51.0)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--step", type=float, default=30.0)
    ap.add_argument("--start", default="2026-07-08T12:00:00")
    ap.add_argument("--max-sats", type=int, default=None)
    ap.add_argument("--screenable-only", action="store_true")
    ap.add_argument("--margins", default="0,0.25,0.5,1,2",
                    help="comma list of u-margins (deg) to sweep; 0 = mutation")
    ap.add_argument("--coverage-margin", type=float, default=1.0)
    ap.add_argument("--cache", default=None,
                    help="npz path to cache/reuse the refined event list")
    args = ap.parse_args()

    df = pd.read_parquet(args.parquet)
    if args.max_sats and len(df) > args.max_sats:
        df = df.head(args.max_sats).reset_index(drop=True)
    if args.screenable_only:
        keep = [is_screenable(p, e, pr) for p, e, pr
                in zip(df["periapsis"], df["eccentricity"], df["period"])]
        df = df[keep].reset_index(drop=True)

    from datetime import datetime, timezone
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    jd0 = _jd(start)
    margins = [float(x) for x in args.margins.split(",")]

    print(f"catalog={os.path.basename(args.parquet)} n={len(df)} "
          f"gross={args.gross} hours={args.hours} step={args.step} "
          f"(3-point anchor, chord rates + curvature margins, checked at TCA)")
    out = run_gate(df, args.gross, args.hours, args.step, jd0, margins,
                   cache=args.cache)
    if out:
        d = out[0]
        cov = coverage(df, d, args.gross, args.hours, args.step, jd0,
                       math.radians(args.coverage_margin))
        print(f"  realized coverage @ {args.coverage_margin}deg margin: "
              f"{100 * cov:.3f}% of pair-steps  =>  ~{1 / max(cov, 1e-9):.0f}x "
              f"medium reduction")


if __name__ == "__main__":
    main()
