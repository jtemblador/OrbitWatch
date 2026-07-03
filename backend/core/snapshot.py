"""
snapshot.py — build the static-site data file (Phase 9.2).

PURE serialization: a fetched catalog frame + screen events → one compact dict the
static site reads (no API, no per-visit fetch). Three parts:

  meta         — freshness + screen parameters
  satellites   — per-object **OMM elements** the browser feeds straight to
                 satellite.js `json2satrec()` (the library's preferred init) +
                 a display `OBJECT_TYPE` for the layer toggles
  conjunctions — the pre-computed close-approach list (the heavy screen, offline)

Why OMM, not TLE: `json2satrec` is satellite.js's recommended path, and OMM's
integer `NORAD_CAT_ID` sidesteps the 5-digit TLE / Alpha-5 cap. The fields are
exactly what `GPFetcher._parse_json` already holds — a direct column → key map.

No I/O and no network here, so the builder is unit-testable offline; the live
fetch+screen lives in `scripts/build_snapshot.py`.
"""

from __future__ import annotations

import math

import pandas as pd

SCHEMA_VERSION = 1

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


def build_snapshot(
    catalog_df: pd.DataFrame,
    events: list[dict],
    screen_params: dict,
    generated_at: str,
    source: str = "CelesTrak",
) -> dict:
    """Assemble the static-site snapshot dict from a catalog frame + screen events.

    Args:
        catalog_df: parsed GP frame (GPFetcher schema) — the displayed satellites.
        events:     run_screen output over that same catalog.
        screen_params: {window_hours, mode, step_sec, [threshold_km]} — for meta.
        generated_at:  ISO timestamp string (stamped by the runner).
        source:        provenance string (e.g. "CelesTrak active").
    """
    sats = [_satellite(rec) for rec in catalog_df.to_dict("records")]
    conj = [_conjunction(e) for e in events]
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
