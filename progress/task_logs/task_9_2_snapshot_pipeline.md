# Task 9.2 — Snapshot build pipeline (the static-site data file)

**Date:** Jul 3, 2026
**Status:** DONE
**Tests:** 563 passing, 4 skipped (was 541) — +22 (`test_snapshot.py` 18, `is_screenable` 4).

---

## Goal

Build `snapshot.json` — the single file the deployed static site reads so it makes
**zero API calls per visit** — and the offline script that produces it. The file
bundles: per-object **orbital elements** (so the browser propagates positions
itself), the **pre-computed conjunction list** (the heavy screen, done offline),
and **freshness/screen metadata**. This is the data contract every downstream
phase (9.3 frontend, 9.4 deploy, 9.5 CI) depends on.

---

## Approach + key decisions

- **Ship OMM, not TLE.** satellite.js's **`json2satrec(omm)`** is its *preferred*
  init (confirmed in its docs). OMM's integer `NORAD_CAT_ID` sidesteps the 5-digit
  TLE / Alpha-5 cap, and the fields are exactly what `GPFetcher._parse_json`
  already holds — a direct column → key map. **Cross-validated:** our shipped OMM
  → the reference `python-sgp4` OMM loader propagates to within **0.105 m** of our
  C++ SGP4 engine, so `json2satrec` (same Vallado math) will get correct positions.
- **Pure builder + thin runner** (mirrors `validate_socrates`): `snapshot.py`'s
  `build_snapshot(...)` is dict-in/dict-out (unit-tested offline); the live
  fetch+screen lives in `scripts/build_snapshot.py`.
- **Screening scope — screen only where the SFS handbook applies** (the reviewer
  finding). The deployed default is `--group active`, which includes MEO/GEO/HEO,
  but our SFS Table-3 volumes only cover LEO + the deep-space (GEO) band; MEO/HEO
  would hit a wrong-size LEO **fallback** bubble → missed/mislabeled conjunctions.
  Decision (Jose): keep the industry-standard handbook bubbles, **display all
  ~11k, screen only handbook-covered orbits.** New `is_screenable()` in
  `screening_volumes.py` is the single source of truth; the runner screens only
  those, displays the rest, and records the scope in `meta`.
- **JSON must be browser-parseable** — `json.dumps` emits a bare `NaN` token that
  JS `JSON.parse` rejects. Guarded per-value (`_num`), on `max_epoch_age_days`,
  and with `allow_nan=False` as a fail-loud backstop.
- **`EPOCH` always emits microseconds** — `json2satrec` accepts a fraction-less
  epoch, but strict OMM parsers require the fractional field; always emitting it
  keeps the snapshot compatible with both (surfaced by the cross-val test).

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/snapshot.py` | **new** — `SCHEMA_VERSION`, pure `build_snapshot(df, events, screen_params, generated_at)` → `{meta, satellites[] (OMM+OBJECT_TYPE), conjunctions[]}`; `_num`/`_epoch_iso`/`_satellite`/`_conjunction` |
| `scripts/build_snapshot.py` | **new** — runner: fetch → LEO/GEO filter (SFS) → `build_satrecs_and_meta` → `run_screen` (SFS/Euclidean) → `build_snapshot` → write + gzip size report; CLI flags |
| `backend/core/screening_volumes.py` | + `is_screenable(perigee, ecc, period)` — True iff a genuine SFS volume exists (LEO 1-4 + GEO band); False for MEO/HEO |
| `tests/test_snapshot.py` | **new** — 18 tests |
| `tests/test_screening_volumes.py` | + `TestIsScreenable` (4) |

---

## Validation

- **OMM cross-check:** shipped OMM → `python-sgp4` OMM loader vs our C++ engine =
  **0.105 m** @ +6 h (sub-meter — just the float difference between two SGP4 ports).
- **Screening scope (mixed synthetic catalog):** ISS-LEO ✅ screened, GEO ✅ screened
  (handbook deep-space band), GPS-MEO ❌ display-only, Molniya-HEO ❌ display-only.
- **Size:** cached LEO shell (300 sats) → 0.15 MB raw / **0.02 MB gzipped** →
  extrapolates to **~0.7 MB gzipped at 11k**, well under the ~5 MB budget.
- **JSON-safety:** whole snapshot serializes with `allow_nan=False` and parses
  under a NaN-rejecting reader; all-NaN age → `None`, not NaN.
- **563 passing** / 4 skipped.

---

## Snapshot schema (v1)

```jsonc
{ "meta": { "schema_version":1, "generated_at":"…Z", "source":"CelesTrak active",
            "n_satellites":N, "n_conjunctions":M,
            "screen": {"mode":"SFS","window_hours":72,"step_sec":30,
                       "n_screened":K, "regime_scope":"…LEO + GEO; MEO/HEO not screened"},
            "max_epoch_age_days":X },
  "satellites": [ { OMM fields for json2satrec … , "OBJECT_TYPE":"PAYLOAD" } ],
  "conjunctions": [ {"a":id,"b":id,"a_name":…,"tca":…,"miss_km":…,
                     "rel_speed_km_s":…,"rtn_km":[r,t,n],"regime":…} ] }
```

`satellites[]` = ALL displayed objects; `conjunctions[]` = the screened subset →
every conjunction `a`/`b` is guaranteed present in `satellites[]`.

---

## Lessons learned

- **satellite.js prefers OMM (`json2satrec`)** — no TLE reconstruction, no Alpha-5
  cap. Our cached OMM maps straight through; the browser and python-sgp4 agree
  with our C++ engine sub-meter.
- **The SFS handbook is LEO+GEO, not "LEO-only".** Its deep-space band (1300-1800
  min period) covers GEO with a real volume; only MEO (GNSS, ~720 min) and HEO
  (ecc ≥ 0.25) lack a volume. `is_screenable` encodes exactly this so we screen
  only where the standard applies and don't overclaim.
- **`json.dumps` allows NaN by default** → invalid JSON that silently breaks the
  browser. `allow_nan=False` + per-value guards is the fix.
- **Two SGP4 ports agree sub-meter but differ in strictness** — python-sgp4's OMM
  loader enforces the Alpha-5 cap and requires a fractional `EPOCH`; the reference
  cross-check is stricter than the browser target, which is useful (it caught the
  epoch-format hardening).

---

## Deferred

- **Where the live `snapshot.json` is committed / history-bloat handling** — a
  9.4/9.5 decision; 9.2 only produces it (verification snapshots went to scratch).
- **MEO/HEO screening** — would need those regimes' own SFS volumes (out of scope;
  Phase 10-ish). Displayed, honestly not screened.
- **Full `active` (~11k) live run** — proven in CI (9.5) or VPN-off; local
  verification used the cached LEO shell.
