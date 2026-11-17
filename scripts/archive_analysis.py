#!/usr/bin/env python3
"""
Phase 11.1 — archive analytics: what four months of robot-job snapshots say
about the screener's own predictions.

Reads every snapshot the 9.5 cron archived to the orphan `data` branch (both
shapes: pre-10.10 full `satellites[]` OMM, post-10.10 slim-v1 columnar
`elements`) and writes one metrics JSON + three figures. Offline and
deterministic: no network, no re-screen, no clock — every number is one the
live site published; the same directory in gives byte-identical JSON out.

Get the archive (one-time, ~600 MB extracted):
    git fetch origin data
    mkdir -p /tmp/ow-archive && git archive FETCH_HEAD | tar -x -C /tmp/ow-archive
Run from the project root:
    python scripts/archive_analysis.py /tmp/ow-archive/snapshots
    python scripts/archive_analysis.py DIR --json OUT.json --img-dir OUT_DIR --no-figures

Measures (full-catalog runs only — the 22 capped 5,000-object runs of Jul 8–14
are a head slice of the catalog, not comparable, and are excluded):
  1. prediction stability — each event matched to the previous run's event for
     the same unordered pair with TCA within ±900 s (the 9.10 link key), runs
     ≤ 12 h apart. Split by whether either object's elements actually changed
     (epoch moved > 5 s): an unchanged pair re-propagates the same elements and
     has Δmiss ≡ 0 up to rounding, so it says nothing about prediction error.
  2. events per run by pair class — Starlink–Starlink / Starlink–other /
     other–other, where "Starlink" uses the SAME family rule as the site's
     "Hide same-constellation pairs" filter: a faithful port of
     `constellationOf` in frontend/js/snapshot-data.js (`constellation_of`).
  3. element age at the event (max of the two objects' ages) vs the catalog's
     single oldest element (`meta.max_epoch_age_days`).
  4. the 11.1c screening-box counterfactual — every event re-tested against the
     SFS ellipsoid it would have had if each Starlink in LEO 1 (perigee
     ≤ 500 km) were held in LEO 2. The archived `rtn_km` makes this exact per
     archived event; scripts/box_confounder.py checks it against a real
     re-screen.
"""
import argparse
import glob
import gzip
import json
import os
import re
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from backend.core.screening_volumes import (  # noqa: E402  (pure module, no .so)
    DEEP_SPACE, ECC_MAX, LEO_1, LEO_2, LEO_3, LEO_4)

GM_EARTH = 398600.8   # km³/s² — WGS-72, as tle_fetcher._derive_orbit_params
R_EARTH = 6378.135    # km
FULL_CATALOG_MIN = 10_000       # runs below this are the capped 5k head slices
TCA_TOL_S = 900.0               # 9.10 link key: same pair, TCA within ±15 min
MAX_GAP_H = 12.0                # "consecutive" runs (drops outage gaps)
EPOCH_TOL_S = 5.0               # elements "changed" if an epoch moved more than this
CLASSES = ("Starlink–Starlink", "Starlink–other", "other–other")
AGE_BINS = (0, 0.25, 0.5, 1, 2, 3, np.inf)
LEAD_BINS = (0, 4, 8, 12, 16, 20, 24.01)

# ── pure helpers (unit-tested in tests/test_archive_analysis.py) ──────────────

# Faithful port of frontend/js/snapshot-data.js `_CONSTELLATION_RE` +
# `constellationOf` (10.10): anchored at the start, word boundary after the
# family, SKYSAT folded into FLOCK (both Planet). JS `\b` and Python `\b` agree
# on these ASCII names. Keep the two lists in sync.
_CONSTELLATION_RE = re.compile(
    r"^(STARLINK|ONEWEB|KUIPER|QIANFAN|HULIANWANG|IRIDIUM|GLOBALSTAR|ORBCOMM|"
    r"FLOCK|SKYSAT|LEMUR|HAWK|ICEYE|GEESAT|JILIN)\b")


def constellation_of(name) -> str | None:
    """Catalog-name family, or None — port of the frontend's constellationOf."""
    m = _CONSTELLATION_RE.match(name or "")
    if not m:
        return None
    return "FLOCK" if m.group(1) == "SKYSAT" else m.group(1)


def pair_class(a_name, b_name) -> str:
    """Starlink–Starlink / Starlink–other / other–other for one event."""
    k = (constellation_of(a_name) == "STARLINK") + (constellation_of(b_name) == "STARLINK")
    return CLASSES[2 - k]


def orbit_shape(mean_motion, eccentricity):
    """(perigee km, period min) from rev/day + e — tle_fetcher's derivation."""
    mm = np.asarray(mean_motion, dtype=float)
    e = np.asarray(eccentricity, dtype=float)
    n = mm * 2.0 * np.pi / 86400.0
    a = np.cbrt(GM_EARTH / n**2)
    return a * (1.0 - e) - R_EARTH, 1440.0 / mm


_VOLS = (LEO_1, LEO_2, LEO_3, LEO_4, DEEP_SPACE)
VOL_NAMES = np.array([v.name for v in _VOLS])
_VOL_AXES = np.array([(v.r_km, v.t_km, v.n_km) for v in _VOLS])
_VOL_RADIUS = np.array([v.circumscribing_radius() for v in _VOLS])


def regime_index(perigee_km, eccentricity, period_min) -> np.ndarray:
    """Vectorised screening_volumes.regime_for → index into _VOLS (the LEO 1
    fallback included). Test-locked against regime_for on a grid."""
    p, e, t = (np.asarray(x, dtype=float) for x in (perigee_km, eccentricity, period_min))
    circ = e < ECC_MAX
    return np.select(
        [circ & (p <= 500), circ & (p <= 750), circ & (p <= 1200), circ & (p <= 2000),
         circ & (t > 1300) & (t < 1800)],
        [0, 1, 2, 3, 4], default=0)


def pair_volume(ia, ib) -> np.ndarray:
    """The larger-of-two rule from conjunctions.run_screen (max circumscribing
    radius; on a tie the first argument wins, as Python's max does)."""
    ia, ib = np.asarray(ia), np.asarray(ib)
    return np.where(_VOL_RADIUS[ib] > _VOL_RADIUS[ia], ib, ia)


def inside(vol_idx, rtn) -> np.ndarray:
    """ScreeningVolume.contains, vectorised: rtn is (n, 3) km in the primary's RTN."""
    ax = _VOL_AXES[np.asarray(vol_idx)]
    return ((np.asarray(rtn, dtype=float) / ax) ** 2).sum(axis=1) <= 1.0


def hold_starlink_in_leo2(vol_idx, is_starlink) -> np.ndarray:
    """Counterfactual regime: a Starlink that would be LEO 1 stays LEO 2."""
    v = np.asarray(vol_idx).copy()
    v[np.asarray(is_starlink, dtype=bool) & (v == 0)] = 1
    return v


def match_consecutive(ev: pd.DataFrame, runs: pd.DataFrame,
                      tca_tol_s=TCA_TOL_S, max_gap_h=MAX_GAP_H,
                      epoch_tol_s=EPOCH_TOL_S) -> pd.DataFrame:
    """Pair each event with the previous run's event for the same unordered
    pair and TCA within ±tca_tol_s (closest TCA wins). Returns one row per
    match: dmiss, the earlier prediction's lead time and max element age, and
    `fresh` (either object's epoch moved > epoch_tol_s between the runs).

    ev columns: k (run index), p1 < p2 (norad ids), tca (datetime),
    miss_km, lead_h, age_d (max of the two), ep1, ep2 (epochs of p1, p2).
    runs: index k → gen (datetime)."""
    cur = ev[["k", "p1", "p2", "tca", "miss_km", "ep1", "ep2"]]
    prev = ev[["k", "p1", "p2", "tca", "miss_km", "lead_h", "age_d", "ep1", "ep2"]].copy()
    prev["k"] += 1
    j = cur.merge(prev, on=["k", "p1", "p2"], suffixes=("", "_0"))
    dt = (j["tca"] - j["tca_0"]).abs().dt.total_seconds()
    j = j[dt <= tca_tol_s].assign(dt=dt[dt <= tca_tol_s])
    j = (j.sort_values(["k", "p1", "p2", "tca", "dt", "tca_0"], kind="mergesort")
          .drop_duplicates(["k", "p1", "p2", "tca"]))
    gen = runs["gen"]
    gap_h = (j["k"].map(gen) - (j["k"] - 1).map(gen)).dt.total_seconds() / 3600.0
    j = j[gap_h <= max_gap_h]
    moved = lambda a, b: (a - b).abs().dt.total_seconds() > epoch_tol_s  # noqa: E731
    return pd.DataFrame({
        "k": j["k"].to_numpy(),
        "dmiss": (j["miss_km"] - j["miss_km_0"]).abs().to_numpy(),
        "miss_0": j["miss_km_0"].to_numpy(),
        "miss_1": j["miss_km"].to_numpy(),
        "lead_h": j["lead_h"].to_numpy(),
        "age_d": j["age_d"].to_numpy(),
        "fresh": (moved(j["ep1"], j["ep1_0"]) | moved(j["ep2"], j["ep2_0"])).to_numpy(),
        "both_fresh": (moved(j["ep1"], j["ep1_0"]) & moved(j["ep2"], j["ep2_0"])).to_numpy(),
    })


def binned(df: pd.DataFrame, col: str, bins) -> list[dict]:
    """|Δmiss| median / p90 / share > 1 km per bin of `col`."""
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        d = df.loc[(df[col] > lo) & (df[col] <= hi), "dmiss"]
        out.append({"lo": float(lo), "hi": None if np.isinf(hi) else float(hi), "n": int(len(d)),
                    "median_km": _r(d.median(), 3), "p90_km": _r(d.quantile(0.9), 2),
                    "gt1km_pct": _r(100 * (d > 1).mean(), 1)})
    return out


def _r(x, nd):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


# ── loading ───────────────────────────────────────────────────────────────────

def _utc_naive(s) -> pd.Series:
    return pd.to_datetime(s, format="ISO8601", utc=True).dt.tz_convert(None)


def load_one(path: str):
    """One archived snapshot → (run dict, events frame). Both archive shapes."""
    with gzip.open(path) as f:
        d = json.load(f)
    m = d["meta"]
    gen = pd.Timestamp(m["generated_at"]).tz_convert(None)
    if "elements" in d:                                   # slim-v1 (10.10+): columnar
        el = pd.DataFrame(d["elements"])
    else:                                                 # full: one OMM per object
        el = pd.DataFrame([s.get("omm", s) for s in d["satellites"]])
    el = el[["NORAD_CAT_ID", "EPOCH", "OBJECT_NAME", "MEAN_MOTION", "ECCENTRICITY"]]
    el = el.drop_duplicates("NORAD_CAT_ID").set_index("NORAD_CAT_ID")
    epoch = _utc_naive(el["EPOCH"])
    age = (gen - epoch).dt.total_seconds() / 86400.0
    perigee, period = orbit_shape(el["MEAN_MOTION"], el["ECCENTRICITY"])
    reg = pd.Series(regime_index(perigee, el["ECCENTRICITY"], period), index=el.index)
    star = el["OBJECT_NAME"].map(lambda s: constellation_of(s) == "STARLINK")

    c = pd.DataFrame(d["conjunctions"])
    if c.empty:
        c = pd.DataFrame(columns=["a", "b", "tca", "miss_km", "rel_speed_km_s", "rtn_km", "regime"])
    a, b = c["a"].astype(int).to_numpy(), c["b"].astype(int).to_numpy()
    rtn = np.array(c["rtn_km"].tolist(), dtype=float).reshape(-1, 3)
    tca = _utc_naive(c["tca"])
    ia, ib = reg.reindex(a).to_numpy(), reg.reindex(b).to_numpy()
    sa, sb = star.reindex(a).to_numpy(dtype=bool), star.reindex(b).to_numpy(dtype=bool)
    actual = pair_volume(ia, ib)
    held = pair_volume(hold_starlink_in_leo2(ia, sa), hold_starlink_in_leo2(ib, sb))
    ev = pd.DataFrame({
        "gen": gen, "p1": np.minimum(a, b), "p2": np.maximum(a, b),
        "tca": tca.to_numpy(), "miss_km": c["miss_km"].astype(float).to_numpy(),
        "lead_h": ((tca - gen).dt.total_seconds() / 3600.0).to_numpy(),
        "cls": np.array(CLASSES)[2 - (sa.astype(int) + sb.astype(int))],
        "age_d": np.fmax(age.reindex(a).to_numpy(), age.reindex(b).to_numpy()),
        "ep1": epoch.reindex(np.minimum(a, b)).to_numpy(),
        "ep2": epoch.reindex(np.maximum(a, b)).to_numpy(),
        "regime_ok": (VOL_NAMES[actual] == c["regime"].to_numpy()),
        "kept_if_held": inside(held, rtn) if len(c) else np.zeros(0, bool),
    })
    run = {"gen": gen, "file": os.path.basename(path), "n_sat": int(m["n_satellites"]),
           "n_conj": int(m["n_conjunctions"]), "window_h": m["screen"].get("window_hours"),
           "meta_max_age_d": m.get("max_epoch_age_days"),
           "cat_max_age_d": float(age.max()), "cat_median_age_d": float(age.median()),
           "starlink_leo1": int((star & (reg == 0)).sum()), "starlink": int(star.sum())}
    return run, ev


def load_archive(snap_dir: str, workers: int = 4):
    paths = sorted(glob.glob(os.path.join(snap_dir, "*.json.gz")))
    if not paths:
        sys.exit(f"no *.json.gz under {snap_dir}")
    with Pool(workers) as p:
        res = p.map(load_one, paths, chunksize=4)          # map preserves order
    runs = pd.DataFrame([r for r, _ in res]).sort_values("gen").reset_index(drop=True)
    k_of = dict(zip(runs["gen"], runs.index))
    ev = pd.concat([e.assign(k=k_of[r["gen"]]) for r, e in res if len(e)], ignore_index=True)
    return runs, ev


# ── metrics ───────────────────────────────────────────────────────────────────

def compute(runs: pd.DataFrame, ev: pd.DataFrame) -> tuple[dict, dict]:
    full = runs[runs["n_sat"] >= FULL_CATALOG_MIN]
    runs_f = full.copy()
    ev = ev[ev["k"].isin(full.index)]
    out = {"archive": {
        "snapshots": len(runs), "full_catalog_runs": len(full),
        "excluded_capped_runs": int((runs["n_sat"] < FULL_CATALOG_MIN).sum()),
        "first": str(runs["gen"].iloc[0]), "full_from": str(full["gen"].iloc[0]),
        "last": str(runs["gen"].iloc[-1]), "events": int(len(ev)),
        "window_hours": sorted({float(w) for w in runs["window_h"]}),
        "archived_regime_reproduced_pct": _r(100 * ev["regime_ok"].mean(), 3)}}

    # 1 — prediction stability
    mt = match_consecutive(ev, runs)
    fr, st = mt[mt["fresh"]], mt[~mt["fresh"]]
    out["stability"] = {
        "matched": int(len(mt)), "identical_elements_pct": _r(100 * (~mt["fresh"]).mean(), 1),
        "identical_dmiss_max_km": _r(st["dmiss"].max(), 4),
        "identical_dmiss_p99_km": _r(st["dmiss"].quantile(0.99), 4),
        "all_median_km": _r(mt["dmiss"].median(), 3), "all_p90_km": _r(mt["dmiss"].quantile(0.9), 2),
        "fresh_n": int(len(fr)), "fresh_median_km": _r(fr["dmiss"].median(), 3),
        "fresh_p90_km": _r(fr["dmiss"].quantile(0.9), 2),
        "fresh_gt1km_pct": _r(100 * (fr["dmiss"] > 1).mean(), 1),
        "fresh_gt5km_pct": _r(100 * (fr["dmiss"] > 5).mean(), 1),
        "both_fresh_median_km": _r(mt.loc[mt["both_fresh"], "dmiss"].median(), 3),
        "fresh_by_age_d": binned(fr, "age_d", AGE_BINS),
        "fresh_by_lead_h": binned(fr, "lead_h", LEAD_BINS),
    }
    sub1 = mt[mt["miss_0"] < 1]
    for tag, d in (("all", sub1), ("fresh", sub1[sub1["fresh"]])):
        out["stability"][f"lt1km_rematched_{tag}"] = {
            "n": int(len(d)), "still_lt1_pct": _r(100 * (d["miss_1"] < 1).mean(), 1),
            "moved_gt5_pct": _r(100 * (d["miss_1"] >= 5).mean(), 1)}

    # 2 — events per run by pair class
    cls = ev.groupby(["k", "cls"]).size().unstack(fill_value=0).reindex(
        index=full.index, columns=list(CLASSES), fill_value=0)
    gen = full["gen"]
    first_wk = gen < gen.iloc[0] + pd.Timedelta(days=7)
    last_wk = gen > gen.iloc[-1] - pd.Timedelta(days=7)
    per = {}
    for c in [*CLASSES, "total"]:
        s = cls.sum(axis=1) if c == "total" else cls[c]
        per[c] = {"first_week_median": _r(s[first_wk.values].median(), 0),
                  "last_week_median": _r(s[last_wk.values].median(), 0),
                  "ratio": _r(s[last_wk.values].median() / s[first_wk.values].median(), 2)}
    tot = cls.sum(axis=1)
    out["pair_class"] = {"first_week": [str(gen[first_wk].iloc[0].date()), str(gen[first_wk].iloc[-1].date())],
                         "last_week": [str(gen[last_wk].iloc[0].date()), str(gen[last_wk].iloc[-1].date())],
                         "per_run": per,
                         "starlink_starlink_share_first_last_pct": [
                             _r(100 * (cls[CLASSES[0]] / tot)[first_wk.values].median(), 1),
                             _r(100 * (cls[CLASSES[0]] / tot)[last_wk.values].median(), 1)],
                         "growth_from_starlink_starlink_pct": _r(
                             100 * (per[CLASSES[0]]["last_week_median"] - per[CLASSES[0]]["first_week_median"])
                             / (per["total"]["last_week_median"] - per["total"]["first_week_median"]), 1)}

    # 3 — element age at the event vs the catalog's oldest element
    g = ev.groupby("k")["age_d"]
    age = pd.DataFrame({"median": g.median(), "p95": g.quantile(0.95),
                        "gt3_pct": g.apply(lambda s: 100 * (s > 3).mean())}).reindex(full.index)
    age["cat_max"] = full["meta_max_age_d"].astype(float)
    out["element_age"] = {
        "event_median_d": _r(ev["age_d"].median(), 2), "event_p95_d": _r(ev["age_d"].quantile(0.95), 2),
        "event_gt3d_pct": _r(100 * (ev["age_d"] > 3).mean(), 2),
        "per_run_median_range_d": [_r(age["median"].min(), 2), _r(age["median"].max(), 2)],
        "per_run_p95_range_d": [_r(age["p95"].min(), 2), _r(age["p95"].max(), 2)],
        "catalog_max_range_d": [_r(age["cat_max"].min(), 1), _r(age["cat_max"].max(), 1)],
        "catalog_median_range_d": [_r(full["cat_median_age_d"].min(), 2), _r(full["cat_median_age_d"].max(), 2)],
        "meta_max_vs_elements_max_abs_diff_d": _r((full["meta_max_age_d"] - full["cat_max_age_d"]).abs().max(), 3),
    }

    # 4 — 11.1c box counterfactual (archive side; box_confounder.py re-screens)
    ss = ev[ev["cls"] == CLASSES[0]]
    held = ss.groupby("k")["kept_if_held"].sum().reindex(full.index, fill_value=0)
    norm = cls[CLASSES[0]]
    r_norm = norm[last_wk.values].median() / norm[first_wk.values].median()
    r_held = held[last_wk.values].median() / held[first_wk.values].median()
    out["box_counterfactual"] = {
        "starlink_starlink_first_week": _r(norm[first_wk.values].median(), 0),
        "starlink_starlink_last_week": _r(norm[last_wk.values].median(), 0),
        "held_in_leo2_first_week": _r(held[first_wk.values].median(), 0),
        "held_in_leo2_last_week": _r(held[last_wk.values].median(), 0),
        "ratio_actual": _r(r_norm, 2), "ratio_held_in_leo2": _r(r_held, 2),
        "box_share_of_log_growth_pct": _r(100 * (1 - np.log(r_held) / np.log(r_norm)), 1),
        "box_only_events_pct_first_last": [
            _r(100 * (1 - held / norm)[first_wk.values].median(), 1),
            _r(100 * (1 - held / norm)[last_wk.values].median(), 1)],
        "starlink_in_leo1_first_last": [int(full.loc[first_wk.values, "starlink_leo1"].median()),
                                        int(full.loc[last_wk.values, "starlink_leo1"].median())],
    }
    series = {"gen": gen, "cls": cls, "age": age, "fresh": fr, "held": held}
    return out, series


# ── figures (same validated palette as analyze_archive.py) ────────────────────
INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")


def _style(ax, ygrid=True):
    ax.set_facecolor(SURF)
    if ygrid:
        ax.grid(axis="y", color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK2, length=0)


def _header(fig, title, subtitle):
    fig.text(0.04, 0.965, title, fontsize=15, weight="semibold", color=INK, va="top")
    fig.text(0.04, 0.905, subtitle, fontsize=11, color=INK2, va="top")


def _end_labels(ax, items, min_gap=0.085, floor=0.0):
    """Direct labels at the right end of a chart, nudged apart (axes fraction)
    so they never collide. items: [(text, y_data)]."""
    to_ax = lambda y: ax.transLimits.transform(  # noqa: E731
        ax.transScale.transform((1, y)))[1]
    ys = sorted(((to_ax(y), t) for t, y in items), key=lambda v: v[0])
    placed = []
    for y, t in ys:
        y = max(y, floor)
        if placed and y - placed[-1][0] < min_gap:
            y = placed[-1][0] + min_gap
        placed.append((y, t))
    for y, t in placed:
        ax.text(1.012, y, t, transform=ax.transAxes, va="center", fontsize=9.5, color=INK)


def render(metrics: dict, s: dict, img_dir: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, NullLocator
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11,
                         "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"]})
    os.makedirs(img_dir, exist_ok=True)
    meta = {"Software": None}                 # no version stamp → reproducible PNG bytes
    km = FuncFormatter(lambda v, _: f"{v:g} km")
    paths = []

    def save(fig, name):
        p = os.path.join(img_dir, name)
        fig.savefig(p, facecolor=SURF, metadata=meta)
        paths.append(p)

    # 1 — |Δmiss| vs element age, lead-time companion
    st = metrics["stability"]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10, 4.8), dpi=200, facecolor=SURF,
                                  gridspec_kw={"width_ratios": [3, 2], "wspace": 0.25})
    fig.subplots_adjust(left=0.08, right=0.98, top=0.71, bottom=0.17)
    _header(fig, "Prediction change tracks element age far more than lead time",
            f"|Δmiss| between consecutive runs (≤ 12 h apart) when new elements arrived: "
            f"{st['fresh_n']:,} re-predictions.\n{st['identical_elements_pct']:.0f}% of re-predictions "
            f"reused identical elements (Δmiss ≡ 0) and are excluded.")
    panels = ((ax, "fresh_by_age_d", "Older element of the pair at the earlier run (days)", SERIES[0]),
              (ax2, "fresh_by_lead_h", "Lead time of the earlier prediction (hours)", MUTED))
    for a, key, xl, col in panels:
        rows = [r for r in st[key] if r["n"]]
        x = np.arange(len(rows))
        a.plot(x, [r["p90_km"] for r in rows], color=col, lw=1.4, ls=(0, (3, 2)),
               marker="o", ms=5, mec=SURF, mew=1.2)
        a.plot(x, [r["median_km"] for r in rows], color=col, lw=2, marker="o", ms=8, mec=SURF, mew=1.5)
        a.set_yscale("log")
        a.set_ylim(0.1, 20)
        a.yaxis.set_minor_locator(NullLocator())
        a.set_yticks([0.1, 0.3, 1, 3, 10])
        a.yaxis.set_major_formatter(km)
        a.set_xticks(x, [(f">{r['lo']:g}" if r["hi"] is None else f"{r['lo']:g}–{min(r['hi'], 24):g}")
                         for r in rows], fontsize=9)
        a.set_xlabel(xl, color=INK2, fontsize=10)
        _style(a)
    ax2.set_title("same re-predictions, by lead time", loc="left", fontsize=10, color=INK2)
    fig.legend([Line2D([], [], color=INK2, lw=2, marker="o", ms=6),
                Line2D([], [], color=INK2, lw=1.4, ls=(0, (3, 2)), marker="o", ms=4)],
               ["median", "90th percentile"], loc="upper left", bbox_to_anchor=(0.035, 0.80),
               ncol=2, frameon=False, fontsize=10, labelcolor=INK2)
    save(fig, "archive-dmiss-vs-age.png")

    # 2 — stacked events per run by pair class (daily mean of the 2–3 runs/day)
    cls = s["cls"].set_axis(pd.DatetimeIndex(s["gen"].to_numpy()), axis=0)
    day = cls.resample("1D").mean().dropna()
    pc = metrics["pair_class"]["per_run"]
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=200, facecolor=SURF)
    fig.subplots_adjust(left=0.07, right=0.80, top=0.74, bottom=0.10)
    _header(fig, "The growth in conjunctions is Starlink–Starlink",
            f"Events per 24 h screen by pair class, daily mean of the cron runs "
            f"({day.index[0]:%b %-d} – {day.index[-1]:%b %-d, %Y})")
    order = list(reversed(CLASSES))                 # other–other at the baseline
    cols = list(reversed(SERIES))
    ax.stackplot(day.index, *[day[c].to_numpy() for c in order], colors=cols,
                 edgecolor=SURF, linewidth=1.0)
    last = np.array([day[c].iloc[-7:].mean() for c in order])
    mids = np.cumsum(last) - last / 2
    _style(ax)
    ax.set_xlim(day.index[0], day.index[-1])
    ax.set_ylim(0, None)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %-d"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:g}K" if v else "0"))
    ax.set_ylabel("events per run", color=INK2, fontsize=10)
    _end_labels(ax, [(f"{c}\n{pc[c]['first_week_median']:,.0f} → {pc[c]['last_week_median']:,.0f}", y)
                     for c, y in zip(order, mids)], min_gap=0.15, floor=0.08)
    fig.text(0.81, 0.80, "first-week → last-week\nmedian per run", fontsize=8.5, color=MUTED, va="top")
    fig.legend([Patch(color=c) for c in SERIES], list(CLASSES), loc="upper left",
               bbox_to_anchor=(0.035, 0.84), ncol=3, frameon=False, fontsize=10,
               labelcolor=INK2, handlelength=1.2)
    save(fig, "archive-events-by-class.png")

    # 3 — event element age vs the catalog's single oldest element
    gen, age = s["gen"], s["age"]
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=200, facecolor=SURF)
    fig.subplots_adjust(left=0.07, right=0.80, top=0.74, bottom=0.10)
    _header(fig, "Flagged events run on fresh elements",
            "Element age at the event (older of the two objects), per run, vs the single oldest "
            "element in the catalog")
    lines = ((SERIES[1], "cat_max", 1.6, "-", "catalog's oldest element"),
             (SERIES[0], "p95", 1.3, (0, (3, 2)), "event age, 95th percentile"),
             (SERIES[0], "median", 1.8, "-", "event age, median"))
    for col_, key, lw, ls, lab in lines:
        ax.plot(gen, age[key].to_numpy(), color=col_, lw=lw, ls=ls, label=lab)
    ax.set_yscale("log")
    ax.set_ylim(0.25, 50)
    ax.yaxis.set_minor_locator(NullLocator())
    ax.set_yticks([0.3, 1, 3, 10, 30])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g} d"))
    _style(ax)
    ax.set_xlim(gen.iloc[0], gen.iloc[-1])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %-d"))
    tail = {key: float(age[key].iloc[-30:].median()) for _, key, *_ in lines}
    _end_labels(ax, [("oldest in catalog", tail["cat_max"]), ("event p95", tail["p95"]),
                     ("event median", tail["median"])], min_gap=0.07)
    ax.text(1.012, 0.02, f"last-10-day medians:\n{tail['cat_max']:.0f} d / {tail['p95']:.1f} d / "
            f"{tail['median']:.1f} d", transform=ax.transAxes, fontsize=8.5, color=MUTED)
    ax.legend(loc="upper left", bbox_to_anchor=(-0.035, 1.16), ncol=3, frameon=False,
              fontsize=10, labelcolor=INK2)
    save(fig, "archive-element-age.png")
    plt.close("all")
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("snap_dir", help="directory of archived *.json.gz snapshots")
    ap.add_argument("--json", default=os.path.join(_ROOT, "docs", "findings", "archive_metrics.json"))
    ap.add_argument("--img-dir", default=os.path.join(_ROOT, "docs", "img"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()

    runs, ev = load_archive(args.snap_dir, args.workers)
    metrics, series = compute(runs, ev)
    os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
        f.write("\n")
    print(json.dumps(metrics, indent=1, sort_keys=True))
    if not args.no_figures:
        for p in render(metrics, series, args.img_dir):
            print("wrote", os.path.relpath(p, _ROOT))
    print("wrote", os.path.relpath(args.json, _ROOT))


if __name__ == "__main__":
    main()
