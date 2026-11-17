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

from . import maneuvers as _mnv

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

# 11.0c persistent / formation flag. A pair present in this many CONSECUTIVE
# runs (~3 days at the 3x/day cron) at a relative speed below this is a
# co-moving formation or co-located slot (TerraSAR-X/TanDEM-X; GEO co-location
# clusters like Optus D3/MEV-1), not a crossing. It stays in the snapshot —
# labeled, not dropped: the 7.2 co-located suppression rule is deliberately
# NOT loosened (cross-launch suppression still needs < 50 m AND < 5 m/s).
PERSISTENT_MIN_RUNS = 9
PERSISTENT_MAX_REL_SPEED_KM_S = 0.01

# 10.10 slim archive record (see `archive_record`). The data branch keeps every
# run's screen output — the one thing that can't be reproduced later — but not
# the full OMM, which Space-Track gp_history returns by (NORAD_CAT_ID, EPOCH).
# Name/type/mean elements stay so population analytics (e.g. which altitude band
# a constellation sits in) still run straight off the archive. ~50% of the full
# record gzipped (measured on the live 16.8k snapshot).
ARCHIVE_FORMAT = "slim-v1"
ARCHIVE_ELEMENT_KEYS = ("NORAD_CAT_ID", "EPOCH", "OBJECT_NAME", "OBJECT_TYPE",
                        "MEAN_MOTION", "ECCENTRICITY", "INCLINATION")
_ARCHIVE_DROPPED_CONJ_KEYS = {"a_name", "b_name", "a_type", "b_type"}

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
    """One run_screen event → a compact conjunction record (rounded).

    `beyond_horizon` (11.0b, from conjunctions.annotate_horizon) is emitted
    only when true — absent otherwise, like `hist`, so it costs nothing on the
    events it doesn't apply to and an older frontend never sees a new key."""
    rec = {
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
    if e.get("beyond_horizon") is True:
        rec["beyond_horizon"] = True
    return rec


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
    if prev_epoch is None or not prev_conjunctions or max_points <= 0:
        return [dict(c) for c in conjunctions]   # ([-0:] would keep everything)

    # pair -> (tca_epoch, miss_km, prior trail). A plain dict, not a list of
    # candidates: run_screen emits exactly one event per pair per window.
    index: dict = {}
    for prev_rec in prev_conjunctions:
        try:
            key = _pair_key(prev_rec["a"], prev_rec["b"])
            miss = float(prev_rec["miss_km"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue   # OverflowError: json reads 1e999 as inf, int(inf) raises
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


def _prev_runs(prev_rec) -> int:
    """How many consecutive runs a previous record's pair had been present.

    `runs` when it carries a valid one; otherwise (a pre-11.0 snapshot, or a
    first-appearance record, which omits the key) 1 + the length of its `hist`
    trail. Every trail point is a run the pair was linked in, so this is a
    sound LOWER bound — it lets the counter bootstrap off a pre-11.0 chain
    instead of restarting from 1 on the deploy that introduces it."""
    runs = prev_rec.get("runs")
    if isinstance(runs, int) and not isinstance(runs, bool) and runs >= 1:
        return runs
    return 1 + len(_clean_trail(prev_rec.get("hist")))


def carry_forward_runs(conjunctions, prev_conjunctions,
                       min_runs: int = PERSISTENT_MIN_RUNS,
                       max_rel_speed_km_s: float = PERSISTENT_MAX_REL_SPEED_KM_S
                       ) -> list:
    """Count consecutive appearances per PAIR and flag persistent formations (11.0c).

    `runs` = previous run's count + 1 when the same unordered pair is in the
    previous snapshot, else the pair is new (key absent ≡ 1). `slow_runs` =
    the same streak counted only over runs where the pair was ALSO slower than
    max_rel_speed_km_s (reset by any fast run). `persistent` = slow_runs ≥
    min_runs. Review fix: gating only the CURRENT run let a pair that crossed
    fast for 8 runs and then had one slow encounter be flagged a formation.
    A pre-11.0 / pre-fix previous record has no `slow_runs`; it bootstraps from
    its `runs`/`hist` count only if that record was itself slow.

    Linked on the pair ALONE — deliberately not `hist`'s (pair, TCA ± 900 s).
    A co-moving pair has no well-defined TCA: its range is nearly flat, so the
    window's minimum hops between orbits run to run (TerraSAR-X/TanDEM-X: TCA
    moved by hours between consecutive snapshots). Measured over the last 30
    archived runs, of 351 events at v_rel < 0.01 km/s, 217 recur by pair but
    only 81 would link by (pair, TCA) — the TCA key would never reach 9. The
    speed gate is what keeps this from flagging the recurring Starlink
    crossings a pair-only link also counts.

    Pure; returns new records. Malformed previous records are skipped.
    """
    if not prev_conjunctions:
        return [dict(c) for c in conjunctions]
    prev_runs: dict = {}
    prev_slow: dict = {}
    for p in prev_conjunctions:
        try:
            key = _pair_key(p["a"], p["b"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        prev_runs[key] = max(prev_runs.get(key, 0), _prev_runs(p))
        prev_slow[key] = max(prev_slow.get(key, 0),
                             _prev_slow_runs(p, max_rel_speed_km_s))
    out = []
    for c in conjunctions:
        rec = dict(c)
        key = _pair_key(rec["a"], rec["b"])
        before = prev_runs.get(key)
        if before:
            rec["runs"] = before + 1
            if _is_slow(rec, max_rel_speed_km_s):
                slow = prev_slow.get(key, 0) + 1
                # Always written on a linked slow run (even 1): its ABSENCE is
                # how _prev_slow_runs recognizes a pre-fix record to bootstrap.
                rec["slow_runs"] = slow
                if slow >= min_runs:
                    rec["persistent"] = True
        out.append(rec)
    return out


def _is_slow(rec, max_rel_speed_km_s: float) -> bool:
    v = rec.get("rel_speed_km_s")
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and v < max_rel_speed_km_s)


def _prev_slow_runs(prev_rec, max_rel_speed_km_s: float) -> int:
    """The previous record's slow streak (see carry_forward_runs): its valid
    `slow_runs`; else, if that record was itself slow, its run count (bootstrap
    from a pre-fix snapshot); else 0."""
    if not _is_slow(prev_rec, max_rel_speed_km_s):
        return 0
    slow = prev_rec.get("slow_runs")
    if isinstance(slow, int) and not isinstance(slow, bool) and slow >= 1:
        return slow
    return _prev_runs(prev_rec)   # pre-fix record (or first appearance → 1)


def _element_ages(conjunctions, catalog_df: pd.DataFrame, generated_at) -> list:
    """Attach `age_a_d` / `age_b_d` — each object's element age in days at
    `generated_at` (11.0a), rounded to 0.01 d (~15 min).

    Measured from EPOCH at generation time, not the frozen epoch_age_days
    column (same reason as meta.max_epoch_age_days). Omitted per side when the
    object or the timestamp is unknown — never a fabricated 0."""
    gen = _epoch_seconds(generated_at)
    if gen is None or not len(catalog_df) or "epoch" not in catalog_df.columns:
        return conjunctions
    epochs = pd.to_datetime(catalog_df["epoch"], utc=True)
    age_by_id = {}
    for nid, t in zip(catalog_df["norad_cat_id"], epochs):
        if pd.isna(t) or _is_missing(nid):
            continue
        age = (gen - t.timestamp()) / 86400.0
        if math.isfinite(age):
            age_by_id[int(nid)] = round(age, 2)
    for c in conjunctions:
        for side in ("a", "b"):
            age = age_by_id.get(c[side])
            if age is not None:
                c[f"age_{side}_d"] = age
    return conjunctions


# 11.2a: an object absent from this run's catalog keeps its maneuver state (a
# transient gap must not cost its warm-up) — unless its last element set is
# this old, so decayed / dropped objects don't accumulate in the state forever.
MANEUVER_STATE_RETAIN_D = 30.0


def apply_maneuvers(conjunctions, catalog_df: pd.DataFrame, generated_at,
                    prev_state) -> tuple[list, dict | None, dict]:
    """Advance the per-object maneuver detector by this run's catalog and label
    the conjunctions (11.2a; see backend/core/maneuvers.py).

    Annotation only — `mnv_h` / `mnv_who` are added to COPIES of the records;
    the screened events (what the CI A/B gate compares) are untouched.

    Returns (conjunctions, maneuver_state, info). Never raises: a missing or
    malformed `prev_state` is a cold start (the detector re-arms after 3
    element-set steps per object), and any failure in here costs the label,
    never the snapshot — `info["error"]` says why and the state is None.
    """
    info: dict = {"tag_max_h": _mnv.TAG_MAX_H}
    try:
        states = _mnv.decode_state(prev_state)
        info["carried"] = len(states)
        info["cold_start"] = not states
        states, confirmed = _mnv.update_states(states, _mnv.catalog_rows(catalog_df))
        gen = _mnv.epoch_us(generated_at)
        if gen is not None:
            present = set()
            if len(catalog_df) and "norad_cat_id" in catalog_df.columns:
                present = {int(x) for x in catalog_df["norad_cat_id"].tolist()
                           if not _is_missing(x)}
            keep_us = MANEUVER_STATE_RETAIN_D * 86400e6
            states = {k: v for k, v in states.items()
                      if k in present or gen - v.ep_us <= keep_us}
            # A last-maneuver epoch older than the tag window can never tag
            # again (a newer maneuver would overwrite it), so dropping it is
            # lossless for the output — and it is most of the state's bytes
            # (94% of Starlinks maneuver at least monthly).
            tag_us = _mnv.TAG_MAX_H * 3.6e9
            for st in states.values():
                if st.last_mnv_us is not None and gen - st.last_mnv_us > tag_us:
                    st.last_mnv_us = None
        out = _mnv.annotate_conjunctions(conjunctions, states, generated_at)
        info.update(objects=len(states),
                    armed=sum(st.nq >= _mnv.PARAMS["WARMUP"] for st in states.values()),
                    confirmed=len(confirmed),
                    tagged_events=sum(1 for c in out if "mnv_h" in c))
        return out, _mnv.encode_state(states), info
    except Exception as exc:   # noqa: BLE001 — a label must never cost the refresh
        info["error"] = f"{exc!r:.200}"
        return conjunctions, None, info


def build_snapshot(
    catalog_df: pd.DataFrame,
    events: list[dict],
    screen_params: dict,
    generated_at: str,
    source: str = "CelesTrak",
    prev: dict | None = None,
    run: dict | None = None,
    socrates: dict | None = None,
    prev_maneuver_state=None,
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
        prev_maneuver_state: the previous run's `maneuver_state` (11.2a) — the
                       sidecar file build_snapshot.py writes; falls back to a
                       `maneuver_state` key in `prev`. Missing / malformed →
                       the maneuver detector cold-starts.
        run:           per-run telemetry (screen time, catalog source, CI run id)
                       → `meta.run`. CI logs expire after 90 days; the archive
                       doesn't. Omitted → no `run` key.
        socrates:      the 11.2b SOCRATES agreement block
                       (socrates_monitor.agreement_block, or a status-only
                       "unavailable" stub) → `meta.socrates`. Omitted → no
                       key, so pre-11.2b readers see the schema they know.
    """
    sats = [_satellite(rec) for rec in catalog_df.to_dict("records")]
    conj = [_conjunction(e) for e in events]
    if prev:
        conj = carry_forward_history(
            conj, prev.get("conjunctions"),
            (prev.get("meta") or {}).get("generated_at"))
        conj = carry_forward_runs(conj, prev.get("conjunctions"))
    conj = _element_ages(conj, catalog_df, generated_at)
    if prev_maneuver_state is None and isinstance(prev, dict):
        prev_maneuver_state = prev.get("maneuver_state")
    conj, mnv_state, mnv_info = apply_maneuvers(conj, catalog_df, generated_at,
                                                prev_maneuver_state)
    # NaN would serialize to a bare `NaN` token that JS JSON.parse rejects — guard it.
    # Measured at GENERATION time from the epochs: the epoch_age_days column is
    # frozen when the cache was parsed, so an older cache would understate it.
    # (Falls back to the column when generated_at isn't a timestamp.)
    max_age = None
    if len(catalog_df):
        gen = _epoch_seconds(generated_at)
        m = None
        if gen is not None and "epoch" in catalog_df.columns:
            epochs = pd.to_datetime(catalog_df["epoch"], utc=True)
            m = (gen - epochs.map(lambda t: t.timestamp())).max() / 86400.0
        elif "epoch_age_days" in catalog_df.columns:
            m = catalog_df["epoch_age_days"].max()
        if isinstance(m, (int, float)) and math.isfinite(float(m)):
            max_age = round(float(m), 2)
    meta = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": source,
        "n_satellites": len(sats),
        "n_conjunctions": len(conj),
        "screen": screen_params,
        "max_epoch_age_days": max_age,
    }
    meta["maneuvers"] = mnv_info
    if run:
        meta["run"] = run
    if socrates:
        meta["socrates"] = socrates
    snap = {"meta": meta, "satellites": sats, "conjunctions": conj}
    if mnv_state is not None:
        # Read only by the NEXT run. build_snapshot.py moves it out of the
        # published snapshot.json into a sidecar file (~0.7 MB gz the browser
        # would otherwise download on every visit for nothing); kept out of
        # the archive record too — see archive_record.
        snap["maneuver_state"] = mnv_state
    return snap


def archive_record(snap: dict) -> dict:
    """The slim record the data branch keeps for one published snapshot (10.10).

    Keeps `meta` (tagged with the archive format) and every conjunction —
    screen output is not reproducible after the fact — but replaces the full
    per-satellite OMM with columnar `elements` (ARCHIVE_ELEMENT_KEYS), and drops
    the conjunction rows' names/types, which `elements` already carries by NORAD
    id. Pure; never mutates the published snapshot.

    The 11.2a `maneuver_state` is deliberately NOT archived: it is a pure
    function of the archived `elements` sequence (`maneuvers.replay`
    reproduces it), so it would only add bytes to every record. The
    per-event `mnv_h` / `mnv_who` labels ARE kept, with the conjunctions.
    """
    sats = snap.get("satellites") or []
    meta = dict(snap.get("meta") or {})
    meta["archive"] = {
        "format": ARCHIVE_FORMAT,
        "dropped": "full OMM (re-fetch from Space-Track gp_history by "
                   "NORAD_CAT_ID + EPOCH); conjunction names/types (see elements)",
    }
    return {
        "meta": meta,
        "conjunctions": [{k: v for k, v in c.items()
                          if k not in _ARCHIVE_DROPPED_CONJ_KEYS}
                         for c in snap.get("conjunctions") or []],
        "elements": {k: [s.get(k) for s in sats] for k in ARCHIVE_ELEMENT_KEYS},
    }
