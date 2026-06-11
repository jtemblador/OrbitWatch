# Task 6.3 — C++ Medium Filter (Time-Stepped Pair Screening)

**Date:** Jun 11, 2026
**Status:** DONE (6.4 bindings completed by construction — all three C++ functions bound inline)
**Tests:** 321 passing, 1 skipped (was 309) — 12 new in `tests/test_sgp4_cpp.py::TestMediumFilter`

---

## Goal

Stage 2 of the conjunction cascade: for every coarse-surviving pair, scan the screening window in
time steps and emit one row per close-approach window — `(i, j, jd_of_best_step, distance_km)` —
bracketing each candidate conjunction for the fine filter (6.6) to refine.

---

## The Two Decisions That Matter

### 1. Time-major loop (the 6-hours-vs-30-seconds decision)

Per-pair time stepping costs `pairs × steps × 2` SGP4 calls — ~14B propagations ≈ **6 hours** at
Phase 7 scale. Instead, at each timestep every satellite appearing in ≥1 pair is propagated
**once** (positions + velocities cached), then all pair distances are evaluated from the cache:
`N × steps` propagations + cheap distance checks.

Measured: **300 sats / 44,850 pairs / 24 h @ 60 s = 0.68 s**. 1,000 sats / 499,500 pairs / 6 h =
1.66 s. Phase-7 dense (6K sats, 5.4M pairs, 24 h) extrapolates to ~70 s.

### 2. Velocity-aware no-skip bound (closing the 450 km detection hole)

Crossing LEO pairs close at up to ~15 km/s; with 60 s steps an encounter can be **8 km at TCA yet
~520 km and ~200 km at the neighboring samples** (measured on the test fixture). A plain
`d < threshold` check silently misses exactly the most dangerous geometry. Flag interval
`[t_k, t_k+1]` when:

```
min(d_k, d_k+1)  −  v̂_rel · (Δt/2)  −  margin   <   threshold
```

- `v̂_rel` = larger endpoint relative speed of the pair (SGP4 returns velocities for free)
- `margin = 3.3e−4 · Δt² km` covers gravity-gradient curvature: relative accel between objects
  within flagged range ≲ 2.6e−3 km/s²; deviation from linear over Δt/2 ≈ ½·a·(Δt/2)² ≈ 1.2 km
  at 60 s.
- **Soundness:** any interior instant is within Δt/2 of an endpoint and |d′(t)| ≤ v̂ (+ curvature
  absorbed by margin), so if the true minimum is below threshold, its interval is flagged.
- **Adaptivity:** co-orbital neighbors (v_rel ≈ 0) get no inflation — a 940 km Starlink-shell
  neighbor stays quiet where a fixed gross threshold (~530 km) would spam-flag it.

### Review additions

- **Squared-distance pre-check (2.7× speedup):** no bound pair closes faster than ~22 km/s
  (`VMAX_REL = 25` with margin), so pairs beyond `threshold + 25·Δt/2 + margin` are rejected with
  **zero sqrts** (~96% of pair-steps in a dense shell). The precise per-pair bound runs only for
  survivors. Verified semantics-preserving: identical window output on the crosser fixture.
  1,000-sat scan: 4.45 s → 1.66 s.
- **Sub-step scan window now raises** `ValueError` instead of silently returning `[]` (one sample
  = zero intervals = nothing can ever flag).

---

## Interface

```python
rows = orbitcore.medium_filter(satrecs, pairs, jd_start, jd_end, step_sec, threshold_km)
# -> list[(i, j, jd, distance_km)]  — one row per contiguous flagged window per pair
```

- `pairs` from `coarse_filter` (or hand-picked); indices validated, order passes through.
- `jd_start/jd_end` absolute Julian Dates (UTC); per-sat tsince computed internally from each
  satrec's own epoch (`jdsatepoch + jdsatepochF`) — the per-sat-epoch gotcha lives in ONE place.
- Row `jd`/`d` are the window's best **sampled** point; true minimum lies within ±1 step (the
  fine-filter bracket). Pairs can repeat (crossing orbits re-encounter every node pass — the
  fixture produces 8 windows in 6 h).
- Decayed/failed satellites: NaN positions → contribute no flags, close any open window, never
  crash, never affect other pairs.
- **GIL released during the scan** — hot loop touches no Python objects after extraction, so a
  multi-second screen can't freeze the FastAPI process. (6.1 couldn't do this; here it's clean.)
- Errors: `ValueError` (window/step/threshold/pair-index/sub-step window), `TypeError` (non-Satrec
  or non-pair item, named by index; the None→nullptr check from 6.1 reused).

---

## Validation

- **Fast-crosser fixture** (ISS vs MA+180.2°/RAAN+180° clone; found by offline brute-force search
  via `propagate_batch`, then hardcoded): true miss **8.0 km at tsince 122.717 min, v_rel 12.0
  km/s**; sampled 60 s distances 521/200 km. Naive check misses it; the bound flags it and the
  window brackets the true TCA within one step. Ground truth re-verified in-test by 1 s
  brute-force sampling (independent of medium_filter — no circularity).
- Identical-TLE pair → exactly one window, d = 0.0 (merge + end-of-scan flush).
- Multi-window: 8 distinct windows over 6 h for the crosser (node-pass geometry).
- Quiet cases: 940 km co-orbital neighbor not flagged; 35 km neighbor flagged.
- Decayed-sat isolation; pair-order pass-through; empty pairs; 9 boundary-validation cases.
- Suite: 321 passed, 1 skipped, stable ×2.

---

## Performance Record

| Scale | Time |
|---|---|
| 300 sats / 44,850 pairs / 24 h @ 60 s (Phase 6 criterion) | **0.68 s** |
| 1,000 sats / 499,500 pairs / 6 h @ 60 s | 1.66 s |
| Phase-7 dense extrapolation (6K sats / 5.4M pairs / 24 h) | ~70 s |

Flagged for Phase 7: per-pair state ≈ 40 B → ~220 MB at 5.4M pairs (chunk pairs into blocks if
needed); scan tail under-covers up to one step when the span isn't a step multiple (documented).

---

## Lessons Learned

- **Algorithm choice dominated everything:** time-major vs pair-major is the difference between
  0.68 s and ~4 min at Phase 6 — and 70 s vs ~6 h at Phase 7. The 6.1/6.2 measurements (keep hot
  loops in C++, don't round-trip pairs) fed directly into this design.
- **The naive-sampling hole is real and large:** 8 km true miss, 520 km sampled. Any time-stepped
  screener without a velocity-aware (or equivalent) bound silently drops fast crossers — the
  highest-energy, most dangerous conjunctions.
- **Fixture-hunting trick:** `propagate_batch([sat]*K, times)` gives a full track in one call —
  used to brute-force search encounter geometries (offline, then hardcoded) without circularity.
- **Cheap conservative pre-checks pay:** squared-distance gate with a universal physics bound
  (25 km/s) cut 2.7× while provably preserving the no-skip guarantee.

---

## Function Reference

### `orbitcore.medium_filter(satrecs, pairs, jd_start, jd_end, step_sec, threshold_km)`
See interface above. Full docstring in the module (`help(orbitcore.medium_filter)`).
