"""Causal maneuver detector v3 - a per-object state machine on consecutive distinct element sets.

Inputs per element set: NORAD id, EPOCH, MEAN_MOTION (rev/day), INCLINATION (deg)  [slim-v1 has all].
State per object (what a production run would carry forward in the snapshot):
  last_epoch, last_a, last_i, trend (m/day, EWMA of quiet da/dt), scale_a (m, EWMA |quiet residual|),
  scale_i (deg), n_quiet, n_consec_flag.
Step:  exp = min(trend, 0) * dt         (drag can only lower a)
       res = da - exp
       thr = max(FLOOR_A, K * scale_a)      scale_a in [5, 50] m (cap stops a warm-up during thrust inflating it)
       RAISE  if res > thr
       LOWER  if res < -max(thr, BETA * |exp|)   (drag variability: storms can double decay)
       PLANE  if |di| > max(FLOOR_I, K * scale_i)
Quiet steps update trend/scale; flagged steps do not (so sustained thrust stays flagged).
After RELEARN consecutive flags the trend is reset to the latest rate (regime change, e.g. new altitude).
"""
import pandas as pd, numpy as np
D = "/tmp/claude-0/-home-user-OrbitWatch/3c268d84-e8a1-5102-b3b8-d7dc9912af58/scratchpad/maneuver"
MU, RE = 398600.4418, 6378.137
P = dict(K=6.0, FLOOR_A_M=100.0, BETA=1.0, FLOOR_I_DEG=0.01, SCALE0_M=10.0, SCALE_MIN_M=5.0, SCALE_MAX_M=50.0,
         SCALE0_I=0.001, SCALE_MIN_I=0.0005, SCALE_MAX_I=0.003, A_TREND=0.1, A_SCALE=0.2, WARMUP=3, MIN_DT=0.05, RELEARN=6)

def detect(el, **over):
    p = {**P, **over}
    el = el.sort_values(["NORAD_CAT_ID", "EPOCH"]).reset_index(drop=True)
    n = el.MEAN_MOTION.to_numpy() * 2 * np.pi / 86400
    a = (MU / n**2) ** (1 / 3) * 1000.0          # m
    inc = el.INCLINATION.to_numpy()
    ep = el.EPOCH.to_numpy().astype("datetime64[ns]").astype(np.int64) / 86400e9
    ids = el.NORAD_CAT_ID.to_numpy()
    N = len(el)
    out = {k: np.full(N, np.nan) for k in ["dt_d", "da_m", "exp_m", "res_m", "thr_m", "di", "trend", "scale_a"]}
    fr, fl, fi = np.zeros(N, bool), np.zeros(N, bool), np.zeros(N, bool)
    prev_id = None
    for k in range(N):
        if ids[k] != prev_id:
            prev_id = ids[k]; la, li, le = a[k], inc[k], ep[k]
            trend, sa, si, nq, nc, rates = 0.0, p["SCALE0_M"], p["SCALE0_I"], 0, 0, []
            continue
        dt = ep[k] - le
        if dt < p["MIN_DT"] / 24:     # < ~1 min apart: a re-fit of the same arc, merge
            la, li, le = a[k], inc[k], ep[k]; continue
        da, di = a[k] - la, inc[k] - li
        exp = min(trend, 0.0) * dt
        res = da - exp
        thr = max(p["FLOOR_A_M"], p["K"] * sa)
        thri = max(p["FLOOR_I_DEG"], p["K"] * si)
        armed = nq >= p["WARMUP"]
        r_ = armed and res > thr
        l_ = armed and res < -max(thr, p["BETA"] * abs(exp))
        i_ = armed and abs(di) > thri
        for key, v in zip(out, [dt, da, exp, res, thr, di, trend, sa]): out[key][k] = v
        fr[k], fl[k], fi[k] = r_, l_, i_
        rate = da / dt
        if r_ or l_ or i_:
            nc += 1
            if nc >= p["RELEARN"] and not i_:
                trend = rate; nc = 0
        else:
            nc = 0
            if dt >= 0.1:
                trend = rate if nq == 0 else (1 - p["A_TREND"]) * trend + p["A_TREND"] * rate
            if nq > 0:
                sa = min(p["SCALE_MAX_M"], max(p["SCALE_MIN_M"], (1 - p["A_SCALE"]) * sa + p["A_SCALE"] * abs(res)))
                si = min(p["SCALE_MAX_I"], max(p["SCALE_MIN_I"], (1 - p["A_SCALE"]) * si + p["A_SCALE"] * abs(di)))
            nq += 1
        la, li, le = a[k], inc[k], ep[k]
    for key, v in out.items(): el[key] = v
    el["alt"] = a / 1000 - RE
    el["flag_raise"], el["flag_lower"], el["flag_i"] = fr, fl, fi
    el["flag_a"] = fr | fl
    el["flag"] = fr | fl | fi
    # --- persistence confirmation (one-run lag): the NEXT set must keep the new level.
    # Kills source flip-flop (18 SDS vs operator-ephemeris fits alternating +-300-500 m) and single bad sets.
    g = el.groupby("NORAD_CAT_ID", sort=False)
    a_m = a
    el["_a"] = a_m; el["_t"] = ep
    a_next, t_next = g._a.shift(-1), g._t.shift(-1)
    a_prev, t_prev = g._a.shift(1), g._t.shift(1)
    i_next, i_prev = g.INCLINATION.shift(-1), g.INCLINATION.shift(1)
    exp2 = el.trend.clip(upper=0) * (t_next - t_prev)
    res2 = (a_next - a_prev) - exp2
    keep_a = (np.sign(res2) == np.sign(el.res_m)) & (res2.abs() > 0.5 * el.res_m.abs()) & (res2.abs() > el.thr_m)
    keep_i = (np.sign(i_next - i_prev) == np.sign(el.di)) & ((i_next - i_prev).abs() > 0.5 * el.di.abs())
    el["flag_c"] = (el.flag_a & keep_a) | (el.flag_i & keep_i)
    el["confirm_gen"] = g.gen.shift(-1)          # first run that saw the confirming set
    return el.drop(columns=["_a", "_t"])

if __name__ == "__main__":
    import sys, json
    over = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    el = pd.read_parquet(f"{D}/elements.parquet")
    out = detect(el, **over)
    out.to_parquet(f"{D}/flags.parquet")
    print(out.flag.mean(), out.flag_raise.sum(), out.flag_lower.sum(), out.flag_i.sum())
