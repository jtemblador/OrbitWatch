#!/usr/bin/env python3
"""
Phase 11.1 — archive analytics: what four months of robot-job snapshots say
about the screener's own predictions and about the catalog itself. ONE script
(11.1e folded in the 10.10 `analyze_archive.py`), two modes over one loader:

  metrics (default) — prediction stability, events by pair class, element age,
      the 11.1c box counterfactual → docs/findings/archive_metrics.json +
      docs/img/archive-*.png.
  trends — the Starlink write-up's per-run series (docs/findings/
      starlink-shell-lowering.md): catalog size, Starlink count by 25 km
      altitude band, conjunctions by pair class and by the pair's mean-altitude
      band, cron-slot coverage, per-object tracking of the lowering, the
      band-crowding power law → docs/findings/archive_trends.csv +
      docs/findings/img/{starlink-by-altitude,conjunctions-by-family,
      band-crowding}.png.

Reads every snapshot the 9.5 cron archived to the orphan `data` branch (both
shapes: pre-10.10 full `satellites[]` OMM, post-10.10 slim-v1 columnar
`elements`), from EITHER an extracted directory OR a git ref (`--ref`,
read with `git cat-file`, no extraction). Offline and deterministic: no
network, no re-screen, no clock — every number is one the live site
published; the same input gives byte-identical JSON/CSV out.

    git fetch origin data
    python scripts/archive_analysis.py --ref origin/data                   # metrics
    python scripts/archive_analysis.py trends --ref origin/data --until 2026-10-29T01:00
    mkdir -p /tmp/ow-archive && git archive origin/data | tar -x -C /tmp/ow-archive
    python scripts/archive_analysis.py /tmp/ow-archive/snapshots --no-figures

`--until` (UTC; a bare date means through the end of that day) pins a run to a
fixed slice of the append-only archive. The Starlink write-up is pinned to
`--until 2026-10-29T01:00` — the last run before it was drafted.

Population: full-catalog runs only (`--min-sats`, default 10,000) — the 22
capped 5,000-object runs of Jul 8–14 are a head slice of the catalog, not
comparable, and are excluded by both modes.

"Starlink" everywhere = `constellation_of(name) == "STARLINK"` — a faithful
port of the site's `constellationOf` (frontend/js/snapshot-data.js): the name
STARTS with the family followed by a word boundary ("STARLINKX" is not one).

Metrics-mode measures:
  1. prediction stability — each event matched to the previous run's event for
     the same unordered pair with TCA within ±900 s (the 9.10 link key), runs
     ≤ 12 h apart. Split by whether either object's elements actually changed
     (epoch moved > 5 s): an unchanged pair re-propagates the same elements and
     has Δmiss ≡ 0 up to rounding, so it says nothing about prediction error.
  2. events per run by pair class — Starlink–Starlink / Starlink–other /
     other–other.
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
import subprocess
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
    m = _CONSTELLATION_RE.match(name if isinstance(name, str) else "")
    if not m:
        return None
    return "FLOCK" if m.group(1) == "SKYSAT" else m.group(1)


def pair_class(a_name, b_name) -> str:
    """Starlink–Starlink / Starlink–other / other–other for one event."""
    k = (constellation_of(a_name) == "STARLINK") + (constellation_of(b_name) == "STARLINK")
    return CLASSES[2 - k]


def semi_major_axis(mean_motion):
    """Semi-major axis (km) from mean motion (rev/day) — Kepler's third law, WGS-72."""
    n = np.asarray(mean_motion, dtype=float) * 2.0 * np.pi / 86400.0
    return np.cbrt(GM_EARTH / n**2)


def mean_altitude(mean_motion):
    """Orbit-averaged altitude (km above the equatorial radius) — the trends
    mode's altitude: a − R⊕, independent of eccentricity."""
    return semi_major_axis(mean_motion) - R_EARTH


def orbit_shape(mean_motion, eccentricity):
    """(perigee km, period min) from rev/day + e — tle_fetcher's derivation."""
    mm = np.asarray(mean_motion, dtype=float)
    e = np.asarray(eccentricity, dtype=float)
    return semi_major_axis(mm) * (1.0 - e) - R_EARTH, 1440.0 / mm


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


# A snapshot "item" is either a filesystem path (str) or a ("git", repo, ref, path)
# tuple — both picklable for the worker pool, both named "<generated_at>.json.gz"
# with ':' written as '-' (2026-10-29T00-58-01.304098Z.json.gz).

def snapshot_time(item) -> pd.Timestamp:
    """generated_at from an archive file name (naive UTC), without opening it."""
    base = _item_name(item)
    return pd.Timestamp(base[:11] + base[11:19].replace("-", ":")).tz_localize(None)


def parse_until(until):
    """`--until` → inclusive naive-UTC cutoff. A bare YYYY-MM-DD means the end
    of that day; anything else is a timestamp (Z / offset honoured)."""
    if until is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until):
        return pd.Timestamp(until) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    t = pd.Timestamp(until)
    return t.tz_convert(None) if t.tzinfo else t


def _item_name(item) -> str:
    return os.path.basename(item[-1] if isinstance(item, tuple) else item)


def list_snapshots(snap_dir=None, ref=None, until=None, repo=_ROOT) -> list:
    """Archive items, oldest first (ISO names sort chronologically), from an
    extracted directory or a git ref, cut at `until` (see parse_until)."""
    if (snap_dir is None) == (ref is None):
        raise ValueError("give exactly one of snap_dir / ref")
    if ref is not None:
        out = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", ref],
                             capture_output=True, check=True, text=True).stdout.split()
        items = [("git", repo, ref, p) for p in sorted(out)
                 if p.startswith("snapshots/") and p.endswith(".json.gz")]
    else:
        items = sorted(glob.glob(os.path.join(snap_dir, "*.json.gz")))
    cut = parse_until(until)
    return items if cut is None else [i for i in items if snapshot_time(i) <= cut]


def read_snapshot(item) -> dict:
    """Decoded snapshot JSON for one item (directory path or git blob)."""
    if isinstance(item, tuple):
        _, repo, ref, path = item
        blob = subprocess.run(["git", "-C", repo, "cat-file", "-p", f"{ref}:{path}"],
                              capture_output=True, check=True).stdout
        return json.loads(gzip.decompress(blob))
    with gzip.open(item) as f:
        return json.load(f)


def elements_frame(d: dict) -> pd.DataFrame:
    """Per-object element table for either archive shape, one row per NORAD id
    (first wins), indexed by NORAD_CAT_ID."""
    if "elements" in d:                                   # slim-v1 (10.10+): columnar
        el = pd.DataFrame(d["elements"])
    else:                                                 # full: one OMM per object
        el = pd.DataFrame([s.get("omm", s) for s in d["satellites"]])
    el["NORAD_CAT_ID"] = el["NORAD_CAT_ID"].astype(int)
    return el.drop_duplicates("NORAD_CAT_ID").set_index("NORAD_CAT_ID")


def is_starlink(names) -> np.ndarray:
    """The one Starlink rule, vectorised over a name sequence."""
    return np.array([constellation_of(n) == "STARLINK" for n in names], dtype=bool)


def load_one(item):
    """One archived snapshot → (run dict, events frame). Both archive shapes."""
    d = read_snapshot(item)
    m = d["meta"]
    gen = pd.Timestamp(m["generated_at"]).tz_convert(None)
    el = elements_frame(d)[["EPOCH", "OBJECT_NAME", "MEAN_MOTION", "ECCENTRICITY"]]
    epoch = _utc_naive(el["EPOCH"])
    age = (gen - epoch).dt.total_seconds() / 86400.0
    perigee, period = orbit_shape(el["MEAN_MOTION"], el["ECCENTRICITY"])
    reg = pd.Series(regime_index(perigee, el["ECCENTRICITY"], period), index=el.index)
    star = pd.Series(is_starlink(el["OBJECT_NAME"]), index=el.index)

    c = pd.DataFrame(d["conjunctions"])
    if c.empty:
        c = pd.DataFrame(columns=["a", "b", "tca", "miss_km", "rel_speed_km_s", "rtn_km", "regime"])
    a, b = c["a"].astype(int).to_numpy(), c["b"].astype(int).to_numpy()
    rtn = np.array(c["rtn_km"].tolist(), dtype=float).reshape(-1, 3)
    tca = _utc_naive(c["tca"])
    ia, ib = reg.reindex(a).to_numpy(), reg.reindex(b).to_numpy()
    sa = star.reindex(a, fill_value=False).to_numpy(dtype=bool)
    sb = star.reindex(b, fill_value=False).to_numpy(dtype=bool)
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
    run = {"gen": gen, "file": _item_name(item), "n_sat": int(m["n_satellites"]),
           "n_conj": int(m["n_conjunctions"]), "window_h": m["screen"].get("window_hours"),
           "meta_max_age_d": m.get("max_epoch_age_days"),
           "cat_max_age_d": float(age.max()), "cat_median_age_d": float(age.median()),
           "starlink_leo1": int((star & (reg == 0)).sum()), "starlink": int(star.sum())}
    return run, ev


def _pmap(fn, items, workers):
    """Ordered map; in-process when workers <= 1 (tests, debugging)."""
    if workers <= 1:
        return [fn(i) for i in items]
    with Pool(workers) as p:
        return p.map(fn, items, chunksize=4)               # map preserves order


def load_archive(items: list, workers: int = 4):
    if not items:
        sys.exit("no archived *.json.gz snapshots found")
    res = _pmap(load_one, items, workers)
    runs = pd.DataFrame([r for r, _ in res]).sort_values("gen").reset_index(drop=True)
    k_of = dict(zip(runs["gen"], runs.index))
    ev = pd.concat([e.assign(k=k_of[r["gen"]]) for r, e in res if len(e)], ignore_index=True)
    return runs, ev


# ── metrics ───────────────────────────────────────────────────────────────────

def compute(runs: pd.DataFrame, ev: pd.DataFrame,
            min_sats: int = FULL_CATALOG_MIN) -> tuple[dict, dict]:
    full = runs[runs["n_sat"] >= min_sats]
    runs_f = full.copy()
    ev = ev[ev["k"].isin(full.index)]
    out = {"archive": {
        "snapshots": len(runs), "full_catalog_runs": len(full),
        "excluded_capped_runs": int((runs["n_sat"] < min_sats).sum()),
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


# ── metrics figures (dataviz reference palette, light surface) ──────────────
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
            f"reused identical elements (Δmiss ≈ 0) and are excluded.")
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


# ── trends mode (the Starlink write-up; was scripts/analyze_archive.py) ──────
BAND_KM = 25
WEEK = pd.Timedelta(days=7)


def band_labels(alt_km) -> np.ndarray:
    """25 km altitude-band label per value: 462.0 → "450-475" (lower edge in)."""
    lo = (np.floor(np.asarray(alt_km, dtype=float) / BAND_KM) * BAND_KM).astype(int)
    return np.array([f"{x}-{x + BAND_KM}" for x in lo], dtype=object)


def band_counts(alt_km) -> pd.Series:
    """Count per band, ordered by altitude (an explicit order: value_counts'
    tie order drifted across pandas versions and reshuffled the CSV)."""
    c = pd.Series(band_labels(alt_km), dtype=object).value_counts()
    return c.iloc[np.argsort([int(b.split("-")[0]) for b in c.index], kind="stable")]


def trend_frames(d: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One snapshot → (objects[norad, name, alt, inc, starlink], conjunctions
    [a, b, a_name, b_name]). Names come from the snapshot's own element table
    (the same source the metrics mode classifies by), falling back to an
    archived a_name/b_name for an id the table lacks."""
    el = elements_frame(d)
    sats = pd.DataFrame({"norad": el.index.to_numpy(),
                         "name": el["OBJECT_NAME"].astype(str).to_numpy(),
                         "alt": mean_altitude(el["MEAN_MOTION"].astype(float)),
                         "inc": el["INCLINATION"].astype(float).to_numpy()})
    sats["starlink"] = is_starlink(sats["name"])
    conj = pd.DataFrame(d["conjunctions"]) if d["conjunctions"] else \
        pd.DataFrame(columns=["a", "b"])
    names = sats.set_index("norad")["name"]
    for side in "ab":
        conj[side] = conj[side].astype(int)
        col, looked_up = f"{side}_name", conj[side].map(names)
        conj[col] = looked_up.fillna(conj[col]) if col in conj else looked_up
    return sats, conj


def trend_rows(ts: str, sats: pd.DataFrame, conj: pd.DataFrame) -> list[tuple]:
    """Tidy (generated_at, measure, group, value) rows for one snapshot:
    catalog size, Starlink count, Starlinks by altitude band, conjunctions by
    pair class, conjunctions by the pair's mean-altitude band."""
    rows = [(ts, "catalog", "all", len(sats)),
            (ts, "catalog", "starlink", int(sats["starlink"].sum())),
            (ts, "conj_family", "total", len(conj))]
    rows += [(ts, "starlink_by_band", b, int(v))
             for b, v in band_counts(sats.loc[sats["starlink"], "alt"]).items()]
    k = is_starlink(conj["a_name"]).astype(int) + is_starlink(conj["b_name"]).astype(int)
    rows += [(ts, "conj_family", f, int((k == 2 - i).sum())) for i, f in enumerate(CLASSES)]
    alt = sats.set_index("norad")["alt"]
    mean_alt = ((conj["a"].map(alt) + conj["b"].map(alt)) / 2.0).dropna()
    rows += [(ts, "conj_by_band", b, int(v)) for b, v in band_counts(mean_alt).items()]
    return rows


def trend_one(item):
    """Worker: one snapshot → (generated_at, object count, rows, objects frame)."""
    d = read_snapshot(item)
    sats, conj = trend_frames(d)
    ts = d["meta"]["generated_at"]
    return ts, len(sats), trend_rows(ts, sats, conj), sats[["norad", "alt", "inc", "starlink"]]


def load_trends(items: list, workers: int = 4, min_sats: int = FULL_CATALOG_MIN):
    """→ (tidy frame, skipped dates, first objects frame, last objects frame)."""
    if not items:
        sys.exit("no archived *.json.gz snapshots found")
    res = _pmap(trend_one, items, workers)
    kept = [r for r in res if r[1] >= min_sats]
    skipped = [r[0][:10] for r in res if r[1] < min_sats]
    rows = [row for r in kept for row in r[2]]
    df = pd.DataFrame(rows, columns=["generated_at", "measure", "group", "value"])
    return df, skipped, kept[0][3], kept[-1][3]


def _between(s, lo, hi):
    return s.between(lo, hi, inclusive="left")


def track_objects(first: pd.DataFrame, last: pd.DataFrame) -> dict:
    """Per-object view of the shift — lowering vs replacement (decay + new
    launches): where the first run's 525–575 km Starlinks are now, and where
    the last run's 450–475 km Starlinks came from."""
    f = first[first["starlink"]].set_index("norad")
    l = last[last["starlink"]].set_index("norad")                  # noqa: E741
    now = f.index[_between(f["alt"], 525, 575)].map(l["alt"]).to_series()
    band = l[_between(l["alt"], 450, 475)]
    then = band.index.map(f["alt"]).to_series()
    mix = lambda d: {float(k): int(v) for k, v in d["inc"].round().value_counts().items()}  # noqa: E731
    return {
        "from_525_575": {"n": len(now), "at_450_500": int(_between(now, 450, 500).sum()),
                         "at_500_525": int(_between(now, 500, 525).sum()),
                         "below_450": int((now < 450).sum()),
                         "still_525_575": int(_between(now, 525, 575).sum()),
                         "gone": int(now.isna().sum())},
        "into_450_475": {"n": len(band), "already": int(_between(then, 450, 475).sum()),
                         "came_down": int((then >= 475).sum()), "came_up": int((then < 450).sum()),
                         "new": int(then.isna().sum())},
        "inc_mix_450_475": [mix(f[_between(f["alt"], 450, 475)]), mix(band)],
    }


def trend_pivot(df: pd.DataFrame) -> pd.DataFrame:
    """Tidy rows → one row per run, (measure, group) columns, + the merged
    525–575 km band."""
    df = df.assign(t=pd.to_datetime(df["generated_at"], utc=True, format="ISO8601"))
    p = df.pivot_table(index="t", columns=["measure", "group"], values="value", aggfunc="sum")
    p[("starlink_by_band", "525-575")] = p["starlink_by_band"].reindex(
        columns=["525-550", "550-575"]).sum(axis=1)
    return p


def crowding_exponent(n, c) -> tuple[float, float]:
    """Log-log slope and r of conjunctions vs population: c ∝ n^slope."""
    ln, lc = np.log(np.asarray(n, float)), np.log(np.asarray(c, float))
    return float(np.polyfit(ln, lc, 1)[0]), float(np.corrcoef(ln, lc)[0, 1])


def summarize_trends(p: pd.DataFrame, n_items: int, skipped: list) -> None:
    print(f"archive: {n_items} snapshots; analysed {len(p)} ({p.index[0]:%Y-%m-%d %H:%M} → "
          f"{p.index[-1]:%Y-%m-%d %H:%M} UTC); skipped {len(skipped)} under --min-sats"
          + (f" ({skipped[0]} … {skipped[-1]})" if skipped else ""))
    slot = (p.index - pd.Timedelta(hours=5, minutes=17)).floor("8h")  # 05:17/13:17/21:17 cron
    want = len(pd.date_range(slot.min(), slot.max(), freq="8h"))
    print(f"cron coverage: {slot.nunique()}/{want} 8 h slots have a snapshot "
          f"({100 * (1 - slot.nunique() / want):.1f}% empty)")
    a, z = p[p.index < p.index[0] + WEEK], p[p.index > p.index[-1] - WEEK]
    print(f"first 7 days ({len(a)} runs) → last 7 days ({len(z)} runs), medians:")
    for m, g in [("catalog", "all"), ("catalog", "starlink"), *[("starlink_by_band", b) for b in
                 ("450-475", "475-500", "525-575")], ("conj_family", "total"),
                 *[("conj_family", f) for f in CLASSES], ("conj_by_band", "450-475"),
                 ("conj_by_band", "475-500")]:
        if (m, g) not in p:                            # e.g. a band empty in every run
            continue
        lo, hi = a[(m, g)].median(), z[(m, g)].median()
        print(f"  {m:17s} {g:18s} {lo:8,.0f} → {hi:8,.0f}  ×{hi / lo:.2f}")
    share = lambda d: d[("conj_family", CLASSES[0])].median() / d[("conj_family", "total")].median()  # noqa: E731
    fam = p["conj_family"].fillna(0)
    any_sl = (fam["total"] - fam["other–other"]) / fam["total"]
    print(f"  Starlink–Starlink share {share(a):.0%} → {share(z):.0%}; "
          f"≥1 Starlink in {any_sl.min():.0%}–{any_sl.max():.0%} of each run's events; "
          f"last-week run range {z[('conj_family', 'total')].min():,.0f}–"
          f"{z[('conj_family', 'total')].max():,.0f}")
    n, c = p.get(("starlink_by_band", "450-475")), p.get(("conj_by_band", "450-475"))
    if n is None or c is None or n.nunique() < 2:
        print("crowding: n/a (450–475 km population never varies)")
        return
    slope, r = crowding_exponent(n, c)
    print(f"crowding: conj(450–475) ∝ Starlinks(450–475)^{slope:.2f} "
          f"(log-log fit, {len(p)} runs, r = {r:.2f})")


def print_tracking(tr: dict) -> None:
    a, b = tr["from_525_575"], tr["into_450_475"]
    print(f"  {a['n']} Starlinks at 525–575 km on the first snapshot, now: "
          f"{a['at_450_500']} at 450–500, {a['at_500_525']} at 500–525, {a['below_450']} below 450, "
          f"{a['still_525_575']} still at 525–575, {a['gone']} gone from the catalog")
    print(f"  {b['n']} Starlinks at 450–475 km on the last snapshot were: {b['already']} already "
          f"there, {b['came_down']} higher (came down), {b['came_up']} lower (raising), "
          f"{b['new']} not yet in the catalog")
    print(f"  450–475 km inclination mix, first → last: {tr['inc_mix_450_475'][0]}"
          f" → {tr['inc_mix_450_475'][1]}")


# trends figures — the write-up's look (larger type than the metrics figures)
_K = lambda v, _=None: f"{v / 1000:g}K" if v >= 1000 else f"{v:g}"  # noqa: E731


def _t_style(ax):
    ax.set_facecolor(SURF)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK2, length=0)
    ax.yaxis.set_major_formatter(_K)


def _t_header(fig, title, subtitle, legend=()):
    from matplotlib.lines import Line2D
    fig.text(0.035, 0.955, title, fontsize=19, weight="semibold", color=INK, va="top")
    fig.text(0.035, 0.895, subtitle, fontsize=13.5, color=INK2, va="top")
    if legend:
        fig.legend([Line2D([], [], color=c, lw=3) for _, c in legend], [t for t, _ in legend],
                   loc="upper left", bbox_to_anchor=(0.025, 0.86), ncol=len(legend), fontsize=13,
                   frameon=False, labelcolor=INK2, handlelength=1.4, columnspacing=1.4)


def _t_line(ax, s, text, color, lw=2.2):
    ax.plot(s.index, s, color=color, lw=lw, solid_capstyle="round")
    ax.plot(s.index[-1], s.iloc[-1], "o", ms=8, color=color, mec=SURF, mew=2, zorder=5)
    ax.annotate(f"{text}  {s.iloc[-1]:,.0f}", (s.index[-1], s.iloc[-1]), xytext=(10, 0),
                textcoords="offset points", va="center", fontsize=13.5, color=INK)


def render_trends(p: pd.DataFrame, img_dir: str) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 14,
                         "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"]})
    os.makedirs(img_dir, exist_ok=True)
    fmt = mdates.DateFormatter("%b %-d")
    early, late = p.index < p.index[0] + WEEK, p.index > p.index[-1] - WEEK
    grew = lambda s: s[late].median() / s[early].median()  # noqa: E731
    paths = []

    def save(fig, name):
        path = os.path.join(img_dir, name)
        fig.savefig(path, facecolor=SURF, metadata={"Software": None})  # reproducible bytes
        paths.append(path)

    # 1 — Starlink population by altitude band: the two bands that moved, one for context
    bands = [("450-475", SERIES[0]), ("525-575", SERIES[1]), ("475-500", MUTED)]
    fig, ax = plt.subplots(figsize=(8, 5), dpi=200, facecolor=SURF)
    fig.subplots_adjust(left=0.08, right=0.76, top=0.76, bottom=0.08)
    _t_header(fig, "Starlinks are leaving the ~550 km shells",
              f"Starlink satellites by mean altitude, every snapshot, "
              f"{p.index[0]:%b %-d} – {p.index[-1]:%b %-d, %Y}",
              [(f"{b.replace('-', '–')} km", c) for b, c in bands])
    for b, col in bands:
        _t_line(ax, p[("starlink_by_band", b)].fillna(0), f"{b.replace('-', '–')} km", col)
    _t_style(ax)
    ax.set_ylim(0, 7000)
    ax.xaxis.set_major_formatter(fmt)
    save(fig, "starlink-by-altitude.png")

    # 2 — conjunctions by pair class; catalog size on its own axis beneath (no dual axis)
    fams = list(zip(CLASSES, SERIES))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(8, 6.4), dpi=200, facecolor=SURF, sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1], "hspace": 0.38})
    fig.subplots_adjust(left=0.08, right=0.74, top=0.78, bottom=0.06)
    _t_header(fig, f"Starlink–Starlink conjunctions rose {grew(p[('conj_family', CLASSES[0])]):.1f}×",
              "Conjunctions per 24 h screen (thin: each run; bold: 7-day median)", fams)
    for f, col in fams:
        s = p[("conj_family", f)]
        ax.plot(s.index, s, color=col, lw=0.9, alpha=0.35)
        _t_line(ax, s.rolling("7D", center=True).median(), f, col)
    _t_style(ax)
    ax.set_ylim(0, 16500)
    ax.xaxis.set_major_formatter(fmt)                              # shared with ax2
    cat = p[("catalog", "all")]
    ax2.plot(cat.index, cat, color=INK2, lw=2.2)
    _t_style(ax2)
    ax2.set_ylim(0, 20000)
    ax2.set_yticks([0, 10000, 20000])
    ax2.set_title(f"Objects in the catalog, first vs last week: {cat[early].median():,.0f} to "
                  f"{cat[late].median():,.0f} (+{100 * (grew(cat) - 1):.1f}%)",
                  loc="left", fontsize=13.5, color=INK2, pad=8)
    save(fig, "conjunctions-by-family.png")

    # 3 — crowding in the band that filled: conjunctions vs population, darker = later
    n, c = p[("starlink_by_band", "450-475")], p[("conj_by_band", "450-475")]
    k = float((c * n**2).sum() / (n**4).sum())                     # least squares c = k·n²
    fig, ax = plt.subplots(figsize=(8, 5.4), dpi=200, facecolor=SURF)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.82, bottom=0.14)
    _t_header(fig, f"{grew(n):.1f}× the satellites, {grew(c):.1f}× the conjunctions",
              "450–475 km: one dot per snapshot (lighter = July, darker = October)")
    _t_style(ax)
    xs = np.linspace(0, n.max() * 1.04, 200)
    ax.plot(xs, k * xs**2, color=INK2, lw=1.6, zorder=2)
    ax.annotate("best fit of k·n²", (xs[95], k * xs[95] ** 2), xytext=(-6, 10),
                textcoords="offset points", ha="right", fontsize=13.5, color=INK2)
    ramp = LinearSegmentedColormap.from_list("seq", ["#86b6ef", "#2a78d6", "#104281"])
    ax.scatter(n, c, s=46, c=(n.index - n.index[0]) / (n.index[-1] - n.index[0]), cmap=ramp,
               edgecolor=SURF, linewidth=1.2, zorder=3)
    ax.set_xlim(0, n.max() * 1.06)
    ax.set_ylim(0, c.max() * 1.1)
    ax.xaxis.set_major_formatter(_K)
    ax.set_xlabel("Starlink satellites at 450–475 km", color=INK2, fontsize=13.5)
    ax.set_ylabel("Conjunctions at 450–475 km", color=INK2, fontsize=13.5)
    save(fig, "band-crowding.png")
    plt.close("all")
    return paths


# ── CLI ───────────────────────────────────────────────────────────────────────

def _source(args) -> list:
    if (args.snap_dir is None) == (args.ref is None):
        sys.exit("give a snapshot directory OR --ref (e.g. --ref origin/data), not both")
    return list_snapshots(args.snap_dir, args.ref, args.until)


def run_metrics(args) -> None:
    runs, ev = load_archive(_source(args), args.workers)
    metrics, series = compute(runs, ev, args.min_sats)
    os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=1, sort_keys=True)
        f.write("\n")
    print(json.dumps(metrics, indent=1, sort_keys=True))
    if not args.no_figures:
        for p in render(metrics, series, args.img_dir):
            print("wrote", os.path.relpath(p, _ROOT))
    print("wrote", os.path.relpath(args.json, _ROOT))


def run_trends(args) -> None:
    items = _source(args)
    df, skipped, first, last = load_trends(items, args.workers, args.min_sats)
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "archive_trends.csv")
    df.to_csv(csv_path, index=False)
    p = trend_pivot(df)
    summarize_trends(p, len(items), skipped)
    print_tracking(track_objects(first, last))
    if not args.no_figures:
        for path in render_trends(p, os.path.join(args.out, "img")):
            print("wrote", os.path.relpath(path, _ROOT))
    print(f"wrote {os.path.relpath(csv_path, _ROOT)} ({len(df):,} rows)")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("snap_dir", nargs="?", help="directory of archived *.json.gz snapshots")
    common.add_argument("--ref", help="git ref holding snapshots/ (e.g. origin/data), "
                                      "instead of a directory")
    common.add_argument("--until", metavar="UTC",
                        help="only snapshots generated at or before this time (YYYY-MM-DD = "
                             "end of that day). The Starlink write-up is pinned to "
                             "--until 2026-10-29T01:00")
    common.add_argument("--min-sats", type=int, default=FULL_CATALOG_MIN,
                        help="skip runs with fewer objects (the capped Jul 8–14 runs)")
    common.add_argument("--workers", type=int, default=4)
    common.add_argument("--no-figures", action="store_true")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    m = sub.add_parser("metrics", parents=[common], help="11.1 metrics JSON + docs/img figures")
    m.add_argument("--json", default=os.path.join(_ROOT, "docs", "findings", "archive_metrics.json"))
    m.add_argument("--img-dir", default=os.path.join(_ROOT, "docs", "img"))
    m.set_defaults(func=run_metrics)
    t = sub.add_parser("trends", parents=[common],
                       help="Starlink write-up: archive_trends.csv + docs/findings/img figures")
    t.add_argument("--out", default=os.path.join(_ROOT, "docs", "findings"),
                   help="output dir (archive_trends.csv here, figures under img/)")
    t.set_defaults(func=run_trends)
    return ap


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("metrics", "trends", "-h", "--help"):
        argv.insert(0, "metrics")                      # bare DIR / --ref → metrics (11.1 CLI)
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
