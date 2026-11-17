"""
SOCRATES agreement monitor (Phase 11.2b) — every cron rebuild scores how far
our freshly screened events agree with CelesTrak's latest SOCRATES run, and the
snapshot records it in `meta.socrates`. 8.2/8.3 validated the screener once;
this turns that into a trend the archive keeps.

What it is NOT: accuracy against truth. SOCRATES runs the same SGP4 method on
its OWN element sets (fetched up to ~8 h before ours), so on current elements
the two screens disagree mostly by epoch drift — 8.3 measured 20–33 %
reproduction on the current feed vs 89–100 % on epoch-matched elements. The
number this produces is "how much do two same-method screens on slightly
different elements overlap", and a sudden drop is what it's for: a schema
change, a broken engine, or a SOCRATES outage shows up as a step, not noise.

The two screens differ by construction, so both directions restrict to the
slice where they SHOULD agree on identical elements:

  population  both objects in our screened catalog (SOCRATES is payloads vs the
              full catalog; we screen active payloads only — debris rows drop)
  window      the overlap of our 24 h window and SOCRATES's 7-day run, trimmed
              by the match tolerance at both ends so an encounter whose TCA
              sits right on an edge can't count as a miss

  SOCRATES → ours  reference = SOCRATES's closest encounter per pair (we keep
              one event per pair per window, `_dedupe_to_unique_pairs`), only
              where range <= 0.4 km (the smallest SFS semi-axis: such a miss is
              inside EVERY handbook ellipsoid whatever its RTN orientation, so
              our SFS screen must report it — a 3 km SOCRATES row may be
              outside our 0.4 km radial axis legitimately) and rel. speed
              >= 0.5 km/s (slower pairs can be dropped by our 7.2 co-located
              suppression on purpose).
  ours → SOCRATES  reference = our events with miss < 5 km (SOCRATES's
              threshold); candidates = every SOCRATES row in the window.

Matching reuses 8.2's `match_events` (pair + TCA within 10 min, one-to-one),
so the DSE buckets and delta stats mean exactly what they do in the report.
Pure: no I/O, no network — build_snapshot.py does the fetch and owns the
"never fatal" contract.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend.core.socrates_compare import (
    DEFAULT_MATCH_WINDOW_S,
    SOCRATES_THRESHOLD_KM,
    _to_utc,
    match_events,
)

MONITOR_VERSION = 1

# Smallest semi-axis of every SFS Table-3 volume we screen with (LEO 1–4 radial
# = 0.4 km; deep space is 10 km). A miss at or below it is inside any of them.
IN_VOLUME_KM = 0.4

# Mirrors conjunctions._V_REL_FLOOR_KM_S: below this, our co-located suppression
# may drop a same-launch pair by design, so it isn't a fair reference.
SLOW_PAIR_KM_S = 0.5

_DSE_LABELS = ("<1d", "1-3d", ">3d")


def _iso(dt) -> str:
    return _to_utc(dt).isoformat().replace("+00:00", "Z")


def _r(v, nd):
    """Round a finite float, else None (the snapshot is dumped allow_nan=False)."""
    if v is None:
        return None
    v = float(v)
    return round(v, nd) if np.isfinite(v) else None


def _direction(summary: dict, results: list[dict], candidate_pairs: set) -> dict:
    """Compact one match_events summary for the snapshot."""
    n = summary["n_socrates"]          # match_events calls the reference "socrates"
    by = {}
    for label in _DSE_LABELS:
        b = summary["by_dse"][label]
        by[label] = {"n": b["n_socrates"], "n_matched": b["n_matched"],
                     "rate": _r(b["reproduction_rate"], 4) if b["n_socrates"] else None}
    n_pair = sum(1 for r in results
                 if frozenset((r["norad_id_1"], r["norad_id_2"])) in candidate_pairs)
    return {
        "n": n,
        "n_matched": summary["n_matched"],
        "rate": _r(summary["reproduction_rate"], 4) if n else None,
        # The same pair flagged at all (any TCA) — separates "the other screen
        # never flagged this pair" from "flagged it, but a different pass won".
        "n_pair_flagged": n_pair,
        "median_abs_dtca_s": _r(summary["tca_delta_s"].get("median_abs"), 3),
        "median_abs_dmiss_km": _r(summary["miss_delta_km"].get("median_abs"), 4),
        "by_dse": by,
    }


def agreement_block(
    socrates_df: pd.DataFrame,
    our_events: list[dict],
    screened_ids,
    epochs: dict,
    start_utc: datetime,
    hours: float,
    match_window_s: float = DEFAULT_MATCH_WINDOW_S,
    threshold_km: float = SOCRATES_THRESHOLD_KM,
    in_volume_km: float = IN_VOLUME_KM,
    slow_km_s: float = SLOW_PAIR_KM_S,
) -> dict:
    """Score our screen against one SOCRATES run → the `meta.socrates` block.

    Args:
        socrates_df:  socrates_fetcher schema (the full run, any size).
        our_events:   run_screen output for this rebuild (raw, unrounded).
        screened_ids: NORAD ids we screened (the population).
        epochs:       NORAD id → element epoch (tz-aware), for our side's DSE.
        start_utc, hours: our screening window.

    Returns a JSON-safe dict (no NaN). `status` is "ok", or "no_overlap" when
    the SOCRATES run does not cover any of our window (a stale run).
    """
    start = _to_utc(start_utc)
    end = start + timedelta(hours=hours)
    soc = socrates_df
    tcas = pd.to_datetime(soc["tca"], utc=True)
    block = {
        "version": MONITOR_VERSION,
        "socrates_rows": int(len(soc)),
        "socrates_first_tca": _iso(tcas.min()) if len(soc) else None,
        "socrates_last_tca": _iso(tcas.max()) if len(soc) else None,
        "match_window_s": match_window_s,
        "threshold_km": threshold_km,
        "in_volume_km": in_volume_km,
        "slow_km_s": slow_km_s,
    }
    if not len(soc):
        block["status"] = "no_overlap"
        return block

    # Candidate pool = the raw overlap; denominators = the overlap trimmed by the
    # match tolerance at each end (an edge TCA's partner may sit just outside).
    pool_lo, pool_hi = max(start, _to_utc(tcas.min())), min(end, _to_utc(tcas.max()))
    edge = timedelta(seconds=match_window_s)
    lo, hi = pool_lo + edge, pool_hi - edge
    if hi <= lo:
        block["status"] = "no_overlap"
        return block
    block["overlap"] = {"start": _iso(lo), "end": _iso(hi),
                       "hours": round((hi - lo).total_seconds() / 3600.0, 2)}

    ids = {int(i) for i in screened_ids}
    in_pop = soc["norad_id_1"].isin(ids) & soc["norad_id_2"].isin(ids)
    soc = soc.assign(tca=tcas)
    pool_soc = soc[in_pop & (tcas >= pool_lo) & (tcas <= pool_hi)]
    win_soc = pool_soc[(pool_soc["tca"] >= lo) & (pool_soc["tca"] <= hi)]

    def _ev_tca(e):
        return _to_utc(datetime.fromisoformat(e["tca"]))

    pool_ours = [e for e in our_events if pool_lo <= _ev_tca(e) <= pool_hi]
    win_ours = [e for e in pool_ours
                if lo <= _ev_tca(e) <= hi and float(e["miss_distance_km"]) < threshold_km]

    # --- SOCRATES → ours -----------------------------------------------------
    pk = [frozenset(p) for p in zip(win_soc["norad_id_1"], win_soc["norad_id_2"])]
    closest = (win_soc.assign(_pk=pk).sort_values("range_km", kind="mergesort")
               .drop_duplicates("_pk").drop(columns="_pk"))
    ref_soc = closest[(closest["range_km"] <= in_volume_km)
                      & (closest["rel_speed_km_s"] >= slow_km_s)].reset_index(drop=True)
    res_s, sum_s = match_events(ref_soc, pool_ours, match_window_s)
    our_pairs = {frozenset((e["sat1_norad_id"], e["sat2_norad_id"])) for e in pool_ours}

    # --- ours → SOCRATES -----------------------------------------------------
    def _dse(nid, tca):
        ep = epochs.get(int(nid))
        return float("nan") if ep is None else \
            (tca - _to_utc(ep)).total_seconds() / 86400.0

    rows = []
    for e in win_ours:
        t = _ev_tca(e)
        rows.append({
            "norad_id_1": int(e["sat1_norad_id"]), "norad_id_2": int(e["sat2_norad_id"]),
            "name_1": e.get("sat1_name"), "name_2": e.get("sat2_name"),
            "dse_1": _dse(e["sat1_norad_id"], t), "dse_2": _dse(e["sat2_norad_id"], t),
            "tca": t, "range_km": float(e["miss_distance_km"]),
        })
    ref_ours = pd.DataFrame(rows, columns=["norad_id_1", "norad_id_2", "name_1", "name_2",
                                           "dse_1", "dse_2", "tca", "range_km"])
    soc_as_events = [
        {"sat1_norad_id": int(a), "sat2_norad_id": int(b),
         "tca": _to_utc(t).isoformat(), "miss_distance_km": float(m)}
        for a, b, t, m in zip(pool_soc["norad_id_1"], pool_soc["norad_id_2"],
                              pool_soc["tca"], pool_soc["range_km"])]
    res_o, sum_o = match_events(ref_ours, soc_as_events, match_window_s)
    soc_pairs = {frozenset((e["sat1_norad_id"], e["sat2_norad_id"])) for e in soc_as_events}

    block["status"] = "ok"
    block["n_socrates_in_scope"] = int(len(win_soc))      # rows, before per-pair collapse
    block["n_ours_in_scope"] = len(win_ours)
    block["socrates_to_ours"] = _direction(sum_s, res_s, our_pairs)
    block["ours_to_socrates"] = _direction(sum_o, res_o, soc_pairs)
    return block


__all__ = ["agreement_block", "IN_VOLUME_KM", "SLOW_PAIR_KM_S", "MONITOR_VERSION"]
