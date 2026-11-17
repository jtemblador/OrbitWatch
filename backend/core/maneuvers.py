"""
maneuvers.py — causal maneuver detection from consecutive element sets (11.2a).

A LABEL, not a filter: an event whose participant maneuvered in the last
`TAG_MAX_H` hours gets `mnv_h` / `mnv_who`, telling the reader the prediction may
be stale. The prototype (progress/week11_planning/maneuver/spec.md) measured
tagged events re-predicting with a median |Δmiss| of 1.98 km vs 0.62 km
untagged — but the tag catches only ~13% of the > 5 km jumps, so an UNTAGGED
event is not thereby stable (most instability is element noise and age).

Detector (a faithful port of the prototype's `detector.py`, which the tests
replay as the oracle). Per object, causal, from MEAN_MOTION / INCLINATION /
EPOCH only (so it also runs off the slim archive's `elements`):

    a        = (μ / n²)^(1/3)                      n in rad/s, a in m
    expected = min(trend, 0) · Δt                  drag can only LOWER a
    res      = Δa − expected
    thr      = max(100 m, 6 · scale_a)             scale_a ∈ [5, 50] m
    RAISE    res > thr
    LOWER    res < −max(thr, 1.0 · |expected|)     storms can double decay
    PLANE    |Δi| > max(0.01°, 6 · scale_i)        scale_i ∈ [0.0005°, 0.003°]

Quiet steps update the trend (EWMA of Δa/Δt, Δt ≥ 0.1 d) and the noise scales;
flagged steps do not (sustained thrust stays flagged), except that 6
consecutive a-flags re-learn the trend (a regime change). Nothing flags before 3
quiet steps (warm-up). Sets < 3 min apart are merged. A flagged step is a
maneuver only once the NEXT set confirms the new level (one run of lag): this
removes ~24% of raw flags, which are Starlink's alternating operator-ephemeris /
18 SDS fits (±300–500 m), not burns.

Incremental: the whole per-object state is a few numbers (`ObjState`), carried
forward run to run inside the snapshot (`encode_state` / `decode_state`), so a
cron run is O(objects) — no archive scan. A missing/malformed state is a cold
start, never an error.

Detection floor: one element set per object per run (3x/day) → Δa below ~100–300
m (most few-cm/s collision-avoidance burns) is invisible. Pure phasing / RAAN /
eccentricity maneuvers are not detected. GEO station-keeping is unvalidated.

Pure: no I/O, no pandas required (accepts plain rows). CLI at the bottom
replays an archive directory (validation / optional seeding).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MU_KM3_S2 = 398600.4418

# Same names and values as the prototype's `P` (detector.py) — do not retune here
# without re-running the spec's FP / impact measurements.
PARAMS = dict(K=6.0, FLOOR_A_M=100.0, BETA=1.0, FLOOR_I_DEG=0.01, SCALE0_M=10.0,
              SCALE_MIN_M=5.0, SCALE_MAX_M=50.0, SCALE0_I=0.001, SCALE_MIN_I=0.0005,
              SCALE_MAX_I=0.003, A_TREND=0.1, A_SCALE=0.2, WARMUP=3, MIN_DT=0.05,
              RELEARN=6)

# Tag events whose participant's latest confirmed maneuver is at most this old.
TAG_MAX_H = 48.0
STATE_VERSION = 1

_FLAG_A, _FLAG_I = 1, 2
_US_PER_DAY = 86_400_000_000
_UNIX0 = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)


@dataclass
class ObjState:
    """Everything the detector remembers about one object between runs."""
    ep_us: int              # epoch of the last element set (µs since Unix epoch)
    n: float                # its MEAN_MOTION (rev/day) — a is recomputed from it
    i: float                # its INCLINATION (deg)
    trend: float = 0.0      # m/day, EWMA of quiet Δa/Δt
    sa: float = PARAMS["SCALE0_M"]   # m, robust Δa noise scale
    si: float = PARAMS["SCALE0_I"]   # deg, Δi noise scale
    nq: int = 0             # quiet steps seen (only "≥ WARMUP" and "0" matter)
    nc: int = 0             # consecutive flagged steps (trend re-learn)
    # The last set's flag awaiting confirmation by the next set:
    # (n_prev, i_prev, ep_prev_us, res_m, thr_m, di_deg, trend_at_step, flags)
    pend: tuple | None = None
    last_mnv_us: int | None = None   # epoch of the latest CONFIRMED maneuver set


def sma_m(n_rev_day: float) -> float:
    """Semi-major axis (m) from SGP4 mean motion (rev/day) — Kepler, as the
    prototype computes it (the per-object step, not the absolute value, matters)."""
    n = n_rev_day * 2 * math.pi / 86400
    return (MU_KM3_S2 / n ** 2) ** (1 / 3) * 1000.0


def _days(ep_us: int) -> float:
    # Same float as the prototype's `int64 ns / 86400e9` (int → float64 is
    # correctly rounded in both numpy and CPython).
    return (ep_us * 1000) / 86400e9


def epoch_us(v) -> int | None:
    """EPOCH (ISO string, naive = UTC; datetime; or pandas Timestamp) → integer
    µs since the Unix epoch, or None if unusable. Integer so the state's
    "same set?" test is exact and its JSON round trip is lossless."""
    if v is None:
        return None
    if hasattr(v, "value") and hasattr(v, "tz_localize"):        # pandas Timestamp
        try:
            if v != v:   # NaT
                return None
            ts = v.tz_convert("UTC") if v.tzinfo is not None else v
            return int(ts.value) // 1000
        except (TypeError, ValueError, AttributeError):
            return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return (v - _UNIX0) // _US
    return None



def step(st: ObjState | None, ep_us: int, n: float, inc: float,
         p: dict = PARAMS) -> tuple[ObjState, int | None]:
    """Feed one new element set of an object into its state.

    Returns (state, confirmed) where `confirmed` is the epoch (µs) of a
    previously flagged set that THIS set confirms as a maneuver, else None.
    `st` is mutated in place (a new one is created for a first sighting). A set
    no newer than the last one seen is ignored: the cron sees each set when it
    is first published, and a supplier serving an older set (e.g. a fallback
    source lagging) carries no new information.
    """
    if st is None:
        return ObjState(ep_us=ep_us, n=n, i=inc), None
    if ep_us <= st.ep_us:
        return st, None

    a, ep = sma_m(n), _days(ep_us)

    # Confirmation of the previous set's flag — uses this set whatever it is
    # (the prototype's shift(-1) includes merged sets).
    confirmed = None
    if st.pend is not None:
        n_p, i_p, ep_p, res, thr, di, trend_k, flags = st.pend
        a_prev, t_prev = sma_m(n_p), _days(ep_p)
        exp2 = min(trend_k, 0.0) * (ep - t_prev)
        res2 = (a - a_prev) - exp2
        keep_a = (_sign(res2) == _sign(res) and abs(res2) > 0.5 * abs(res)
                  and abs(res2) > thr)
        d2 = inc - i_p
        keep_i = _sign(d2) == _sign(di) and abs(d2) > 0.5 * abs(di)
        if (flags & _FLAG_A and keep_a) or (flags & _FLAG_I and keep_i):
            confirmed = st.ep_us
            st.last_mnv_us = confirmed
        st.pend = None

    la, le = sma_m(st.n), _days(st.ep_us)
    dt = ep - le
    if dt < p["MIN_DT"] / 24:      # a re-fit of the same arc: merge
        st.ep_us, st.n, st.i = ep_us, n, inc
        return st, confirmed

    da, di = a - la, inc - st.i
    exp = min(st.trend, 0.0) * dt
    res = da - exp
    thr = max(p["FLOOR_A_M"], p["K"] * st.sa)
    thri = max(p["FLOOR_I_DEG"], p["K"] * st.si)
    armed = st.nq >= p["WARMUP"]
    r_ = armed and res > thr
    l_ = armed and res < -max(thr, p["BETA"] * abs(exp))
    i_ = armed and abs(di) > thri
    rate = da / dt
    if r_ or l_ or i_:
        st.pend = (st.n, st.i, st.ep_us, res, thr, di, st.trend,
                   (_FLAG_A if (r_ or l_) else 0) | (_FLAG_I if i_ else 0))
        st.nc += 1
        if st.nc >= p["RELEARN"] and not i_:
            st.trend = rate
            st.nc = 0
    else:
        st.nc = 0
        if dt >= 0.1:
            st.trend = rate if st.nq == 0 else (1 - p["A_TREND"]) * st.trend + p["A_TREND"] * rate
        if st.nq > 0:
            st.sa = min(p["SCALE_MAX_M"], max(p["SCALE_MIN_M"],
                        (1 - p["A_SCALE"]) * st.sa + p["A_SCALE"] * abs(res)))
            st.si = min(p["SCALE_MAX_I"], max(p["SCALE_MIN_I"],
                        (1 - p["A_SCALE"]) * st.si + p["A_SCALE"] * abs(di)))
        # Only "0" and "≥ WARMUP" are ever tested, so cap it (fewer state bytes).
        st.nq = min(st.nq + 1, p["WARMUP"])
    st.ep_us, st.n, st.i = ep_us, n, inc
    return st, confirmed


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def update_states(states: dict, rows) -> tuple[dict, list]:
    """Advance every object's state by this run's element sets.

    Args:
        states: {norad_id: ObjState} from the previous run (mutated; also returned).
        rows:   iterable of (norad_id, epoch, mean_motion, inclination); epoch as
                accepted by `epoch_us`. Unusable rows are skipped.

    Returns (states, confirmations) — confirmations = [(norad_id, mnv_epoch_us)].
    Objects absent from this run keep their state (a transient catalog gap must
    not cost the warm-up).
    """
    confirmations = []
    for nid, ep, n, inc in rows:
        try:
            nid = int(nid)
            n, inc = float(n), float(inc)
        except (TypeError, ValueError, OverflowError):
            continue
        us = epoch_us(ep)
        if us is None or not (math.isfinite(n) and math.isfinite(inc)) or n <= 0:
            continue
        st, conf = step(states.get(nid), us, n, inc)
        states[nid] = st
        if conf is not None:
            confirmations.append((nid, conf))
    return states, confirmations


def hours_since_maneuver(states: dict, nid: int, gen_us: int) -> float | None:
    st = states.get(nid)
    if st is None or st.last_mnv_us is None:
        return None
    return max(0.0, (gen_us - st.last_mnv_us) / 3.6e9)


def annotate_conjunctions(conjunctions, states: dict, generated_at,
                          max_h: float = TAG_MAX_H) -> list:
    """Attach `mnv_h` (hours since the most recent confirmed maneuver of either
    participant, 0.1 h) and `mnv_who` ("a" | "b" | "ab": which participant(s)
    maneuvered within `max_h`). Both are ABSENT when neither did — like `hist`,
    an untagged event costs zero bytes and an older frontend sees no new key.
    Pure: returns new records."""
    gen = epoch_us(generated_at)
    out = []
    for c in conjunctions:
        rec = dict(c)
        if gen is not None:
            who, best = "", None
            for side in ("a", "b"):
                try:
                    h = hours_since_maneuver(states, int(rec[side]), gen)
                except (KeyError, TypeError, ValueError):
                    h = None
                if h is not None and h <= max_h:
                    who += side
                    best = h if best is None else min(best, h)
            if who:
                rec["mnv_h"] = round(best, 1)
                rec["mnv_who"] = who
        out.append(rec)
    return out


# --- (de)serialization: compact, columnar, lossless for the detector ---------

def encode_state(states: dict) -> dict:
    """{norad: ObjState} → the snapshot's `maneuver_state` (JSON-safe).

    Columnar like the slim archive's `elements`. Lossless — floats are written
    at full precision (Python's shortest round-trip repr), so a run that reads
    the state back continues bit-for-bit as if it had never stopped (locked by
    tests against the prototype). `pend`/`lm` are sparse: only flagged /
    recently-maneuvered objects carry them."""
    ids = sorted(states)
    cols = {k: [] for k in ("id", "ep", "n", "i", "tr", "sa", "si", "q", "c")}
    pend, lm = {}, {}
    for nid in ids:
        st = states[nid]
        for k, v in (("id", nid), ("ep", st.ep_us), ("n", st.n), ("i", st.i),
                     ("tr", st.trend), ("sa", st.sa), ("si", st.si),
                     ("q", st.nq), ("c", st.nc)):
            cols[k].append(v)
        if st.pend is not None:
            pend[str(nid)] = list(st.pend)
        if st.last_mnv_us is not None:
            lm[str(nid)] = st.last_mnv_us
    return {"v": STATE_VERSION, **cols, "pend": pend, "lm": lm}


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _flt(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def decode_state(obj) -> dict:
    """The snapshot's `maneuver_state` → {norad: ObjState}.

    Comes off the network (the live snapshot.json), so it is validated per
    row: a malformed row is dropped (that object cold-starts), an unknown
    version or wrong shape yields {} (everything cold-starts). Never raises."""
    if not isinstance(obj, dict) or obj.get("v") != STATE_VERSION:
        return {}
    keys = ("id", "ep", "n", "i", "tr", "sa", "si", "q", "c")
    cols = [obj.get(k) for k in keys]
    if not all(isinstance(c, list) for c in cols) or len({len(c) for c in cols}) != 1:
        return {}
    pend = obj.get("pend") if isinstance(obj.get("pend"), dict) else {}
    lm = obj.get("lm") if isinstance(obj.get("lm"), dict) else {}
    out = {}
    for nid, ep, n, i, tr, sa, si, q, c in zip(*cols):
        nid, ep, q, c = _int(nid), _int(ep), _int(q), _int(c)
        n, i, tr, sa, si = _flt(n), _flt(i), _flt(tr), _flt(sa), _flt(si)
        if None in (nid, ep, n, i, tr, sa, si, q, c) or n <= 0 or q < 0 or c < 0:
            continue
        st = ObjState(ep_us=ep, n=n, i=i, trend=tr, sa=sa, si=si, nq=q, nc=c)
        p = pend.get(str(nid))
        if isinstance(p, list) and len(p) == 8:
            vals = [_flt(x) for x in p[:2]] + [_int(p[2])] + [_flt(x) for x in p[3:7]] + [_int(p[7])]
            if None not in vals:
                st.pend = tuple(vals)
        last = _int(lm.get(str(nid)))
        if last is not None:
            st.last_mnv_us = last
        out[nid] = st
    return out


def catalog_rows(catalog_df):
    """(norad, epoch, mean_motion, inclination) rows from a GPFetcher frame."""
    if catalog_df is None or not len(catalog_df):
        return []
    need = ("norad_cat_id", "epoch", "mean_motion", "inclination")
    if any(c not in catalog_df.columns for c in need):
        return []
    return zip(*(catalog_df[c].tolist() for c in need))


def element_rows(snap: dict):
    """Rows from an archived snapshot: slim-v1 `elements` or full `satellites`."""
    el = snap.get("elements")
    if isinstance(el, dict):
        return zip(*(el.get(k) or [] for k in
                     ("NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "INCLINATION")))
    return ((s.get("NORAD_CAT_ID"), s.get("EPOCH"), s.get("MEAN_MOTION"),
             s.get("INCLINATION")) for s in snap.get("satellites") or [])


def replay(paths, states: dict | None = None, roundtrip: bool = False):
    """Replay archived snapshots (oldest first) through the detector — the
    exact per-run path production takes. Yields (generated_at, states,
    confirmations) per snapshot. `roundtrip=True` also pushes the state through
    encode → JSON → decode between runs, as the cron does via --prev."""
    import gzip
    import json
    states = {} if states is None else states
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as f:
                snap = json.load(f)
            if not isinstance(snap, dict):
                raise ValueError("not a snapshot object")
        except (OSError, ValueError, EOFError, RecursionError):
            continue   # one unreadable record is a gap, not a failed seed
        if roundtrip:
            states = decode_state(json.loads(json.dumps(encode_state(states))))
        states, conf = update_states(states, element_rows(snap))
        yield (snap.get("meta") or {}).get("generated_at"), states, conf


def _main(argv=None) -> int:
    """`python -m backend.core.maneuvers replay <dir> [--last N] [--state-out F]`:
    replay archived snapshots (slim or full, oldest first by file name), print
    per-run confirmation counts, and optionally write the final
    `maneuver_state` — the seed deploy.yml uses when the live chain is missing
    (first deploy, or a broken chain) instead of a multi-day cold warm-up."""
    import argparse
    import glob
    import json
    import os
    ap = argparse.ArgumentParser(description=_main.__doc__)
    ap.add_argument("cmd", choices=["replay"])
    ap.add_argument("archive_dir")
    ap.add_argument("--last", type=int, default=0,
                    help="replay only the newest N records (0 = all)")
    ap.add_argument("--state-out", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    paths = sorted(glob.glob(os.path.join(a.archive_dir, "*.json*")))
    if a.last > 0:
        paths = paths[-a.last:]
    total, states = 0, {}
    for gen, states, conf in replay(paths):
        total += len(conf)
        if not a.quiet:
            print(f"{gen}  objects={len(states):6d}  confirmed={len(conf):4d}")
    print(f"{len(paths)} snapshots, {len(states)} objects, {total} confirmed maneuvers")
    if a.state_out:
        with open(a.state_out, "w", encoding="utf-8") as f:
            json.dump(encode_state(states), f, separators=(",", ":"), allow_nan=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
