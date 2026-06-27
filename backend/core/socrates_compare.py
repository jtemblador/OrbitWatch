"""
SOCRATES comparison — does our SGP4 screener reproduce CelesTrak SOCRATES?
(Phase 8.2, the credibility anchor.)

Two layers:

  match_events(socrates_df, our_events)   — PURE. Pairs our screen output to
      SOCRATES rows by {object pair} + TCA proximity, computes per-event TCA and
      miss-distance deltas, and a summary (reproduction rate + deltas segmented by
      element age DSE). No network — the reusable core (Phase 9's "matched
      SOCRATES?" badge reuses this).

  compare_against_socrates(socrates_df, gp_fetcher)  — ORCHESTRATOR. Fetches
      current GP for every SOCRATES-flagged object (incl. debris secondaries),
      builds satrecs, screens the same window in legacy-Euclidean 5 km mode,
      matches, and verifies the epoch match via DSE.

Honest framing: SOCRATES uses STK/CAT on SGP4 — the SAME method — so with
epoch-matched GP the deltas should be tiny (that IS the validation). We compare
in 5 km Euclidean mode (matching SOCRATES's spherical screen), NOT our SFS
ellipsoid default, which would suppress events SOCRATES lists.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.core.conjunctions import run_screen
from backend.core.propagator import build_satrecs_and_meta

# SOCRATES screens within 5 km at TCA, 7 days forward (see socrates_fetcher).
SOCRATES_THRESHOLD_KM = 5.0

# Pairing tolerance: a SOCRATES event and one of ours are "the same encounter" if
# the same object pair closes within this of the SOCRATES TCA. Generous on
# purpose — it only pairs events; the reported tca_delta (expected ~seconds) is
# the actual agreement, not this window.
DEFAULT_MATCH_WINDOW_S = 600.0   # 10 min

# Epoch is "matched" if our element's age at the SOCRATES TCA equals SOCRATES's
# reported DSE within this (days). ~0.05 d = 1.2 h absorbs rounding; a larger gap
# means CelesTrak already rolled the epoch (expect TCA/miss to drift — the finding).
DEFAULT_EPOCH_EPS_DAYS = 0.05

# Each distinct object needs its own CelesTrak GP fetch (gp.php has no multi-id),
# so the orchestrator is for SMALL slices (a primary, top-N). Guard against
# accidentally passing a whole constellation (e.g. by_name("STARLINK") → thousands
# of objects → thousands of sequential requests). Raise deliberately if needed.
DEFAULT_MAX_OBJECTS = 200


def _to_utc(ts) -> datetime:
    """A pandas Timestamp / datetime → tz-aware UTC datetime."""
    dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _stats(values: list) -> dict:
    """Magnitude stats (median / p95 / max of |value|) for a delta list."""
    if not values:
        return {"n": 0}
    a = np.abs(np.asarray(values, dtype=float))
    return {
        "n": len(values),
        "median_abs": float(np.median(a)),
        "p95_abs": float(np.percentile(a, 95)),
        "max_abs": float(a.max()),
    }


def _dse_bucket(dse_max: float) -> str:
    if dse_max < 1.0:
        return "<1d"
    if dse_max <= 3.0:
        return "1-3d"
    return ">3d"


def match_events(
    socrates_df: pd.DataFrame,
    our_events: list[dict],
    match_window_s: float = DEFAULT_MATCH_WINDOW_S,
) -> tuple[list[dict], dict]:
    """Match our screen output to SOCRATES rows and score the agreement.

    Args:
        socrates_df: SOCRATES slice (socrates_fetcher schema) — the reference.
        our_events:  run_screen output (ConjunctionEvent dicts) on the same set.
        match_window_s: max |TCA difference| to call two events the same encounter.

    Returns:
        (results, summary) where results[k] describes SOCRATES row k:
            {norad_id_1/2, name_1/2, socrates_tca, socrates_miss_km,
             dse_1/2, dse_max, matched, ours_tca, ours_miss_km,
             tca_delta_s, miss_delta_km}
        and summary aggregates reproduction rate + |TCA|/|miss| deltas, overall
        and bucketed by element age (DSE).
    """
    # Index our events by unordered pair (medium_filter gives positional ids).
    # run_screen's tca is a tz-aware UTC ISO string (conjunctions._jd_to_utc);
    # normalize defensively so the matcher can't break on a naive value.
    by_pair: dict = defaultdict(list)
    for idx, ev in enumerate(our_events):
        key = frozenset((ev["sat1_norad_id"], ev["sat2_norad_id"]))
        t = _to_utc(datetime.fromisoformat(ev["tca"]))
        by_pair[key].append((idx, t, ev))

    consumed: set = set()
    results: list[dict] = []
    for _, srow in socrates_df.iterrows():
        id1, id2 = int(srow["norad_id_1"]), int(srow["norad_id_2"])
        s_tca = _to_utc(srow["tca"])
        s_miss = float(srow["range_km"])

        # Closest unconsumed our-event for this pair within the window.
        best = None
        for idx, t, ev in by_pair.get(frozenset((id1, id2)), []):
            if idx in consumed:
                continue
            dt = (t - s_tca).total_seconds()
            if abs(dt) <= match_window_s and (best is None or abs(dt) < abs(best[1])):
                best = (idx, dt, t, ev)

        rec = {
            "norad_id_1": id1, "norad_id_2": id2,
            "name_1": srow["name_1"], "name_2": srow["name_2"],
            "socrates_tca": s_tca, "socrates_miss_km": s_miss,
            "dse_1": float(srow["dse_1"]), "dse_2": float(srow["dse_2"]),
            "dse_max": max(float(srow["dse_1"]), float(srow["dse_2"])),
            "matched": best is not None,
            "ours_tca": None, "ours_miss_km": None,
            "tca_delta_s": None, "miss_delta_km": None,
        }
        if best is not None:
            idx, dt, t, ev = best
            consumed.add(idx)
            rec["ours_tca"] = t
            rec["ours_miss_km"] = float(ev["miss_distance_km"])
            rec["tca_delta_s"] = dt
            rec["miss_delta_km"] = float(ev["miss_distance_km"]) - s_miss
        results.append(rec)

    n_extra = sum(1 for i in range(len(our_events)) if i not in consumed)
    return results, _summarize(results, n_extra, len(our_events))


def _summarize(results: list[dict], n_extra: int, n_ours: int) -> dict:
    matched = [r for r in results if r["matched"]]
    n = len(results)
    summary = {
        "n_socrates": n,
        "n_matched": len(matched),
        "reproduction_rate": (len(matched) / n) if n else 0.0,
        "n_extra": n_extra,          # our events not in SOCRATES (other crossings)
        "n_ours": n_ours,
        "tca_delta_s": _stats([r["tca_delta_s"] for r in matched]),
        "miss_delta_km": _stats([r["miss_delta_km"] for r in matched]),
        "by_dse": {},
    }
    # Degradation-with-age: the headline Phase-8 finding. Emit all three buckets
    # (even empty) so the report has a stable shape to iterate.
    buckets: dict = defaultdict(list)
    for r in results:
        buckets[_dse_bucket(r["dse_max"])].append(r)
    for label in ("<1d", "1-3d", ">3d"):
        rows = buckets.get(label, [])
        m = [r for r in rows if r["matched"]]
        summary["by_dse"][label] = {
            "n_socrates": len(rows),
            "n_matched": len(m),
            "reproduction_rate": (len(m) / len(rows)) if rows else 0.0,
            "tca_delta_s": _stats([r["tca_delta_s"] for r in m]),
            "miss_delta_km": _stats([r["miss_delta_km"] for r in m]),
        }
    return summary


def compare_against_socrates(
    socrates_df: pd.DataFrame,
    gp_fetcher,
    step_sec: float = 30.0,
    threshold_km: float = SOCRATES_THRESHOLD_KM,
    match_window_s: float = DEFAULT_MATCH_WINDOW_S,
    epoch_eps_days: float = DEFAULT_EPOCH_EPS_DAYS,
    window_pad_hours: float = 1.0,
    max_objects: int = DEFAULT_MAX_OBJECTS,
) -> dict:
    """Run our screener on the SOCRATES-flagged objects and compare.

    Fetches current GP for every distinct object (incl. debris secondaries),
    builds satrecs, screens the window spanning the slice's TCAs in legacy
    5 km Euclidean mode, matches, and flags each event's epoch agreement (our
    element age at TCA vs SOCRATES's DSE).

    Intended for SMALL slices (one primary via by_catnr, or top_n) — each object
    is a separate CelesTrak fetch, so a slice over `max_objects` distinct objects
    raises (narrow the slice, or raise max_objects deliberately).

    Returns: {"results": [...], "summary": {...}, "missing_objects": [...]}.
    """
    ids = pd.unique(socrates_df[["norad_id_1", "norad_id_2"]].values.ravel())
    if len(ids) > max_objects:
        raise ValueError(
            f"SOCRATES slice spans {len(ids)} distinct objects (> max_objects="
            f"{max_objects}); each needs its own CelesTrak GP fetch. Narrow the "
            "slice (top_n / by_catnr / between) or raise max_objects deliberately.")

    frames, missing = [], []
    for raw in ids:
        nid = int(raw)
        try:
            df_i = gp_fetcher.fetch_by_catnr(nid)
        except Exception:
            df_i = None
        if df_i is None or df_i.empty:
            missing.append(nid)
        else:
            frames.append(df_i)

    if not frames:
        raise RuntimeError(
            "No GP data fetched for any SOCRATES object — cannot compare.")

    gp_df = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset="norad_cat_id").reset_index(drop=True)
    satrecs, meta = build_satrecs_and_meta(gp_df)

    # Screen the window that spans the slice's TCAs (not "now + 7 d": SOCRATES's
    # window starts at its run time, which may already be in the past).
    tcas = pd.to_datetime(socrates_df["tca"], utc=True)
    start = _to_utc(tcas.min()) - timedelta(hours=window_pad_hours)
    end = _to_utc(tcas.max()) + timedelta(hours=window_pad_hours)
    duration_hours = (end - start).total_seconds() / 3600.0

    our_events = run_screen(
        satrecs, meta, start, duration_hours,
        threshold_km=threshold_km, step_sec=step_sec)

    # SOCRATES rows referencing an object we couldn't fetch can't be reproduced —
    # drop them so they don't count as misses against us.
    if missing:
        socrates_df = socrates_df[
            ~(socrates_df["norad_id_1"].isin(missing)
              | socrates_df["norad_id_2"].isin(missing))]

    results, summary = match_events(socrates_df, our_events, match_window_s)
    summary["n_missing_objects"] = len(missing)

    _attach_epoch_ok(results, gp_df, epoch_eps_days)
    summary["n_epoch_ok"] = sum(1 for r in results if r["epoch_ok"])

    return {"results": results, "summary": summary, "missing_objects": missing}


def _attach_epoch_ok(results: list[dict], gp_df: pd.DataFrame,
                     epoch_eps_days: float) -> None:
    """Flag each event: did we screen the same epoch SOCRATES used?

    Our element's age at the SOCRATES TCA should equal SOCRATES's reported DSE
    (per object). A larger gap means CelesTrak rolled the epoch since the SOCRATES
    run — expect TCA/miss to drift (epoch drift, not a method difference).
    """
    epoch_by_id = {
        int(r["norad_cat_id"]): _to_utc(r["epoch"])
        for _, r in gp_df.iterrows()
    }
    for rec in results:
        ok = True
        for nid, dse in ((rec["norad_id_1"], rec["dse_1"]),
                         (rec["norad_id_2"], rec["dse_2"])):
            epoch = epoch_by_id.get(nid)
            if epoch is None:
                ok = False
                continue
            our_age_days = (rec["socrates_tca"] - epoch).total_seconds() / 86400.0
            if abs(our_age_days - dse) > epoch_eps_days:
                ok = False
        rec["epoch_ok"] = ok
