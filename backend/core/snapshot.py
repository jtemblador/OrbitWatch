"""
snapshot.py — build the static-site data file (Phase 9.2).

PURE serialization: a fetched catalog frame + screen events → one compact dict the
static site reads (no API, no per-visit fetch). Three parts:

  meta         — freshness + screen parameters
  satellites   — per-object **OMM elements** the browser feeds straight to
                 satellite.js `json2satrec()` (the library's preferred init) +
                 a display `OBJECT_TYPE` for the layer toggles
  conjunctions — the pre-computed close-approach list (the heavy screen, offline),
                 each optionally carrying `hist` — what THIS SAME encounter was
                 predicted to be on previous runs (9.10; see
                 `carry_forward_history`)

Why OMM, not TLE: `json2satrec` is satellite.js's recommended path, and OMM's
integer `NORAD_CAT_ID` sidesteps the 5-digit TLE / Alpha-5 cap. The fields are
exactly what `GPFetcher._parse_json` already holds — a direct column → key map.

No I/O and no network here, so the builder is unit-testable offline; the live
fetch+screen lives in `scripts/build_snapshot.py`.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

SCHEMA_VERSION = 1

# 9.10 prediction-evolution trail. Prior points only — with the live value the
# sparkline shows at most 4, the deepest track measured across the 41-day archive.
HISTORY_MAX_POINTS = 3
# Two events of the same pair are the SAME encounter only if their TCAs agree to
# this. Co-orbiting satellites (Starlink shells) conjunct repeatedly and
# `_dedupe_to_unique_pairs` keeps only the closest encounter per pair per window,
# so as the window slides the same pair names a different encounter — matching on
# the pair alone would splice unrelated approaches into one trail.
TCA_MATCH_WINDOW_S = 900.0

# df column -> json2satrec OMM key. SGP4-essential + identity fields (EPOCH is
# handled separately as a naive-UTC ISO string).
_OMM_MAP = {
    "object_name": "OBJECT_NAME",
    "object_id": "OBJECT_ID",
    "norad_cat_id": "NORAD_CAT_ID",
    "classification": "CLASSIFICATION_TYPE",
    "mean_motion": "MEAN_MOTION",
    "eccentricity": "ECCENTRICITY",
    "inclination": "INCLINATION",
    "ra_of_asc_node": "RA_OF_ASC_NODE",
    "arg_of_pericenter": "ARG_OF_PERICENTER",
    "mean_anomaly": "MEAN_ANOMALY",
    "bstar": "BSTAR",
    "mean_motion_dot": "MEAN_MOTION_DOT",
    "mean_motion_ddot": "MEAN_MOTION_DDOT",
    "ephemeris_type": "EPHEMERIS_TYPE",
    "element_set_no": "ELEMENT_SET_NO",
    "rev_at_epoch": "REV_AT_EPOCH",
}
_INT_KEYS = {"NORAD_CAT_ID", "EPHEMERIS_TYPE", "ELEMENT_SET_NO", "REV_AT_EPOCH"}
_STR_KEYS = {"OBJECT_NAME", "OBJECT_ID", "CLASSIFICATION_TYPE"}


def _num(v, default: float = 0.0) -> float:
    """JSON-safe number (NaN / inf / None → default — JSON has no NaN)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _epoch_iso(v) -> str:
    """OMM EPOCH as a naive-UTC ISO string with microseconds (e.g.
    '2026-03-26T05:19:34.116960', no offset). satellite.js json2satrec appends
    'Z' and parses via Date, but strict OMM parsers require the fractional-second
    field — always emitting it keeps the snapshot compatible with both."""
    ts = pd.Timestamp(v)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _satellite(row) -> dict:
    """One catalog row (df record or Series) → an OMM object for json2satrec."""
    omm: dict = {"CCSDS_OMM_VERS": "3.0", "EPOCH": _epoch_iso(row["epoch"])}
    for col, key in _OMM_MAP.items():
        v = row.get(col)
        if key in _STR_KEYS:
            omm[key] = "" if _is_missing(v) else str(v)
        elif key in _INT_KEYS:
            omm[key] = int(_num(v))
        else:
            omm[key] = _num(v)
    # Display-only — satellite.js ignores unknown OMM keys; this drives the
    # frontend's group/type layer toggles (9.3).
    omm["OBJECT_TYPE"] = row.get("object_type") or "UNKNOWN"
    return omm


def _conjunction(e: dict) -> dict:
    """One run_screen event → a compact conjunction record (rounded)."""
    return {
        "a": int(e["sat1_norad_id"]), "b": int(e["sat2_norad_id"]),
        "a_name": e["sat1_name"], "b_name": e["sat2_name"],
        "a_type": e["sat1_object_type"], "b_type": e["sat2_object_type"],
        "tca": e["tca"],
        "miss_km": round(_num(e["miss_distance_km"]), 3),
        "rel_speed_km_s": round(_num(e["relative_speed_km_s"]), 3),
        "rtn_km": [round(_num(e["r_km"]), 3), round(_num(e["t_km"]), 3),
                   round(_num(e["n_km"]), 3)],
        "regime": e.get("screening_regime"),
    }


def _pair_key(a, b) -> tuple:
    """Unordered satellite pair — the snapshot does not promise a/b ordering."""
    a, b = int(a), int(b)
    return (a, b) if a < b else (b, a)


def _epoch_seconds(v):
    """ISO-8601 → int epoch seconds, or None if unparseable.

    `hist` stamps are epoch seconds rather than ISO strings for two reasons:
    they are compact, and they sidestep the 9.3 Safari trap entirely (WebKit's
    `Date` rejects a non-3-digit fractional-second field that V8 accepts, which
    silently yields NaN timestamps and is invisible in local dev).
    """
    if not isinstance(v, str):
        return None
    try:
        ts = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive stamp would be read as LOCAL time by .timestamp() — hours of silent
    # drift, and invisible in a UTC-configured CI runner.
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp())


def _clean_trail(trail) -> list:
    """Validate an inherited `hist` down to its individual points.

    Checking only `isinstance(trail, list)` is not enough: the trail is COPIED
    FORWARD every run, so one malformed point would ride the chain for
    `HISTORY_MAX_POINTS` runs and reach the browser each time. The realistic
    source is not corruption (a truncated download fails JSON parsing) but
    schema drift — a future change to the point shape meeting an older snapshot.
    """
    if not isinstance(trail, list):
        return []
    clean = []
    for pt in trail:
        if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
            continue
        when, miss = pt
        if not (isinstance(when, (int, float)) and isinstance(miss, (int, float))):
            continue
        if math.isfinite(when) and math.isfinite(miss):
            clean.append([int(when), float(miss)])
    return clean


def carry_forward_history(conjunctions, prev_conjunctions, prev_generated_at,
                          max_points: int = HISTORY_MAX_POINTS) -> list:
    """Attach each conjunction's `hist` trail from the PREVIOUS snapshot (9.10).

    The site re-screens every ~8 h on refreshed elements, so the same encounter
    is re-predicted several times before its TCA. This walks one run forward:
    every event that also appears in `prev_conjunctions` inherits that run's
    trail plus the previous prediction itself.

    Why a chain and not an archive scan: reading only the previous snapshot is
    O(current events) per run and constant in the archive's size, so the cron
    stays ~10 min and the (147 MB and growing) `data` branch is never fetched.
    If the chain ever breaks — a failed run, a snapshot rebuilt from scratch —
    `hist` is simply absent and refills over the next ~3 runs.

    Pure: takes and returns compact snapshot records (see `_conjunction`), never
    mutating the input. Inputs come off the network (the live snapshot.json), so
    malformed records are skipped rather than allowed to fail the build.

    Args:
        conjunctions:      this run's compact records.
        prev_conjunctions: the previous snapshot's `conjunctions` (may be None).
        prev_generated_at: the previous snapshot's `meta.generated_at`.
        max_points:        cap on retained prior points.

    Returns:
        New records; matched ones gain `hist` = [[epoch_s, miss_km], ...],
        oldest first. Unmatched records have NO `hist` key — absent rather than
        null or [], so the ~69% with no trail cost zero bytes.
    """
    prev_epoch = _epoch_seconds(prev_generated_at)
    if prev_epoch is None or not prev_conjunctions:
        return [dict(c) for c in conjunctions]

    # pair -> (tca_epoch, miss_km, prior trail). A plain dict, not a list of
    # candidates: run_screen emits exactly one event per pair per window.
    index: dict = {}
    for prev_rec in prev_conjunctions:
        try:
            key = _pair_key(prev_rec["a"], prev_rec["b"])
            miss = float(prev_rec["miss_km"])
        except (KeyError, TypeError, ValueError):
            continue
        tca = _epoch_seconds(prev_rec.get("tca"))
        if tca is None or not math.isfinite(miss):
            continue
        index[key] = (tca, round(miss, 3), _clean_trail(prev_rec.get("hist")))

    out = []
    for c in conjunctions:
        rec = dict(c)
        hit = index.get(_pair_key(rec["a"], rec["b"]))
        if hit:
            prev_tca, prev_miss, prev_trail = hit
            # Parse the TCA only for pairs that actually recur — ~83% of events
            # have no counterpart in the previous run, and this parse is the
            # loop's dominant cost (measured 4.7x on a 10k-event snapshot).
            tca = _epoch_seconds(rec.get("tca"))
            if tca is not None and abs(tca - prev_tca) <= TCA_MATCH_WINDOW_S:
                # Stamp the previous RUN's time, not its TCA: the sparkline's
                # x-axis is "when was this predicted", and lead time is derived
                # downstream as (tca - run time).
                rec["hist"] = (prev_trail + [[prev_epoch, prev_miss]])[-max_points:]
        out.append(rec)
    return out


def build_snapshot(
    catalog_df: pd.DataFrame,
    events: list[dict],
    screen_params: dict,
    generated_at: str,
    source: str = "CelesTrak",
    prev: dict | None = None,
) -> dict:
    """Assemble the static-site snapshot dict from a catalog frame + screen events.

    Args:
        catalog_df: parsed GP frame (GPFetcher schema) — the displayed satellites.
        events:     run_screen output over that same catalog.
        screen_params: {window_hours, mode, step_sec, [threshold_km]} — for meta.
        generated_at:  ISO timestamp string (stamped by the runner).
        source:        provenance string (e.g. "CelesTrak active").
        prev:          the previous snapshot dict, to carry conjunction history
                       forward (9.10). Omit — or pass one that is missing or
                       malformed — and no `hist` is emitted at all.
    """
    sats = [_satellite(rec) for rec in catalog_df.to_dict("records")]
    conj = [_conjunction(e) for e in events]
    if prev:
        conj = carry_forward_history(
            conj, prev.get("conjunctions"),
            (prev.get("meta") or {}).get("generated_at"))
    # NaN would serialize to a bare `NaN` token that JS JSON.parse rejects — guard it.
    max_age = None
    if len(catalog_df) and "epoch_age_days" in catalog_df.columns:
        m = catalog_df["epoch_age_days"].max()
        if isinstance(m, (int, float)) and math.isfinite(float(m)):
            max_age = round(float(m), 2)
    return {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "source": source,
            "n_satellites": len(sats),
            "n_conjunctions": len(conj),
            "screen": screen_params,
            "max_epoch_age_days": max_age,
        },
        "satellites": sats,
        "conjunctions": conj,
    }
