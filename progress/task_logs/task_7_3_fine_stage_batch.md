# Task 7.3 — Performance pass: batched fine stage + cap/threadpool

**Date:** Jun 23, 2026
**Status:** DONE
**Tests:** 421 passing, 1 skipped (was 409) — +12 (batched-fine cross-validation +
ground-truth anchor, chunk/edge/decay/co-moving, screening cap, lock wiring)

---

## Goal

7.1 profiled the screening cascade and found the **fine stage is 82–87% of wall
time** (one Python `scipy.minimize_scalar` per window, 1.43 M windows for the full
Starlink catalog), and that a no-`MAX_SATS` full-catalog request screens
synchronously for 258 s — hanging the server. 7.3 makes the fine stage fast and
keeps the API responsive, **without changing any results**.

Scope (confirmed with Jose): pure-Python, **no `.so` rebuild** — (A) thread-pool
the screen + a screening cap, (B) batch the fine stage. The C++ coarse→medium
memory fusion (`scaling_tracker #3`) and the radial coarse-pad tightening stayed
deferred (the cap neutralizes #3's urgency).

---

## Approach

- **Batched fine stage, Newton on the relative range-rate.** TCA is the instant
  the range stops shrinking — where `g(t) = Δr·Δv = 0`. With `g'(t) = |Δv|² +
  Δr·Δa ≈ |Δv|²` (gravity-gradient `Δa` is ~1e-4 of `|Δv|²` near an encounter),
  each step is `t ← t − (Δr·Δv)/|Δv|²` seconds — the **standard operational TCA
  solve**. It starts at the medium-filter sample (within one step of the TCA) and
  converges quadratically in ~3 steps. `propagate_batch` returns `Δr` AND `Δv`,
  so the derivative is free, and **all windows step together** through one
  `propagate_batch` crossing per iteration (GIL released in C++), vectorized in
  NumPy. This kills both the per-call Python↔C++ boundary and the per-window scipy
  machinery, and does *fewer* propagations than scipy (~6 evals vs ~15–50).
- **Why this beats just "batch scipy":** scipy is sequential per window (each eval
  picks the next), so it can't be vectorized across windows. Reformulating the
  minimum as a *root-find on range-rate* makes every window take the same fixed
  step count → embarrassingly parallel → one batched propagation per step.
- **Kept `fine_filter` (scipy) as the validation oracle** — the batch is
  cross-validated against it (and against a brute-force grid), not a replacement
  for the proof.
- **Chunked** by `_FINE_CHUNK = 20000` windows/pass to bound peak memory.
- **Cap + threadpool + lock:** `/api/conjunctions` runs the screen via
  `run_in_threadpool` (medium_filter + propagate_batch release the GIL → the
  worker thread runs the C++ scan while the event loop stays free); a `413`
  screening cap (`ORBITWATCH_MAX_SCREEN_SATS`, default 1500) refuses an oversized
  synchronous screen up front.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/conjunctions.py` | `fine_filter_batch` (the batched fine stage) + `_minimize_tca` (range-rate Newton, vectorized) + `_states`/`_pair_states` helpers; `run_screen` fine loop rewired to call `fine_filter_batch` once instead of per-window `fine_filter`; `_jd_to_utc` factored out (computed lazily only for surviving events); `fine_filter` kept as oracle |
| `backend/routers/satellites.py` | `_max_screen_sats()` + `413` cap; `run_in_threadpool` for the screen; `_propagator_lock` (asyncio) guarding **all** shared-cache propagation (positions / position / track / screen) |
| `backend/core/propagator.py` | `catalog_size()` — cheap cached count for the cap |
| `tests/test_conjunctions.py` | `TestFineFilterBatch` (9: oracle + brute-force cross-validation, RTN-consistency, empty, alignment, chunk-transparency, co-moving, decay→None, edge-widen); `test_decayed_window_is_isolated` rewired to the None seam |
| `tests/test_api.py` | cap 413 + at-limit 200; `test_propagation_endpoints_run_under_the_lock` |

---

## Validation

- **Same-machine A/B** (old scipy vs new Newton, both measured now, 24 h / 50 km):

  | N sats | windows | t_fine OLD | t_fine NEW | fine speedup | total speedup |
  |-------:|--------:|-----------:|-----------:|:------------:|:-------------:|
  | 300 | 14,743 | 3.62 s | 0.97 s | **3.7×** | 2.6× |
  | 500 | 44,155 | 10.30 s | 2.77 s | **3.7×** | 2.6× |
  | 800 | 120,381 | 28.56 s | 7.71 s | **3.7×** | 2.7× |

  (Comparing to 7.1's *documented* numbers understated it to ~2.3× — this machine
  runs ~1.7× slower than 7.1's, evident in the untouched coarse/medium stages, so
  a same-machine A/B is the honest figure.)
- **Event-count parity at every scale** — OLD and NEW produce **byte-identical**
  event sets: 1,588 / 4,872 / 14,271 at 300/500/800, and **124,810** on the full
  10,544-sat catalog (t_fine 223 → 117 s clean). The faster method changes the
  *speed*, not the *answers*.
- **Cross-validation, two anchors:** the batch matches the scipy oracle across all
  crosser windows (<10 m / <50 ms / <1 mm·s⁻¹), AND matches a 0.01 s brute-force
  grid directly (<10 m / <50 ms) — so it never inherits an optimizer error.
- **Adversarial review** (6-agent workflow, 5 dimensions + skeptic verification):
  4 dimensions clean; **1 confirmed bug** — the first lock only serialized
  screen-vs-screen, leaving a **screen-vs-position race** on the shared,
  `sgp4()`-mutated Satrec cache (the frontend polls `/api/positions` during a
  screen; `medium_filter` releases the GIL on the worker thread). Fixed by
  broadening to one `_propagator_lock` over all propagation endpoints.
- **Full suite:** 421 passing, 1 skipped — offline/deterministic.

---

## Test coverage

| Test (file) | Covers |
|------|--------|
| `TestFineFilterBatch.test_matches_oracle_across_all_windows` (test_conjunctions) | batch vs scipy oracle, every crosser window, <10 m/<50 ms/<1 mm·s⁻¹ |
| `…test_matches_brute_force_ground_truth` | batch vs an independent 0.01 s grid (first-principles anchor, not via scipy) |
| `…test_returned_states_are_rtn_consistent` | `‖pos_a−pos_b‖ == miss` (feeds teme_to_rtn) |
| `…test_chunking_is_transparent` | `_FINE_CHUNK=1` gives bit-identical results to one pass (alignment across chunks) |
| `…test_co_moving_pair_is_finite_not_nan` | `|Δv|≈0` → no divide-by-zero, finite ~0 miss |
| `…test_decayed_window_returns_none` | heavy-drag sat at far-future window → `None` (the per-window drop seam) |
| `…test_edge_window_widens_and_recovers` | TCA a full step outside the bracket → one widen recovers it |
| `…test_empty_rows_returns_empty` / `…test_result_aligns_with_rows` | empty input; `results[k]↔rows[k]` contract |
| `TestConjunctions.test_oversized_catalog_returns_413` / `…cap_allows…` (test_api) | cap rejects over-limit, runs at/under |
| `…test_propagation_endpoints_run_under_the_lock` | positions/position/track/conjunctions all acquire `_propagator_lock` |

---

## Lessons learned

- **Reformulate, don't just batch.** The win came from changing the *math*, not the
  language: a generic optimizer (scipy) is sequential per window and can't
  vectorize across windows. Recasting "minimum distance" as "root of range-rate"
  made every window a fixed 5-step Newton iteration → one batched propagation per
  step. The physics (`d(d²)/dt = 2 Δr·Δv`) was the unlock.
- **The language was never the lever.** The SGP4 math was already in C++; the cost
  was the per-call boundary + scipy's Python overhead, both of which batching
  removes from *Python*. A full C++ rewrite would buy a further ~1.2–1.5× (and
  multicore), but at much higher cost/risk — deferred, with the profile to justify
  it if ever needed.
- **Benchmark on one machine.** Comparing to 7.1's stale numbers understated the
  speedup ~1.6×; the untouched coarse/medium stages being ~1.7× slower today
  exposed the machine drift. A stash-and-rerun A/B gave the honest 3.7×.
- **Threadpooling shared mutable state is a trap.** Moving the screen off the event
  loop opened a data race the *adversarial review caught*: `sgp4()` mutates each
  Satrec's `t`/`error`, and the position endpoints share the same cache. One lock
  over all propagation closes it. Lesson: when you add concurrency, audit every
  other reader of the shared state, not just the thing you moved.
- **`np.where` evaluates both branches** — `g/gprime` warned on decayed (NaN)
  windows even though the result was discarded. `np.divide(out=…, where=…)` skips
  the bad division cleanly.

---

## Remaining risks / deferred (tracked)

- **`scaling_tracker #7` (new):** `fine_filter_batch` materializes a dict per
  non-decayed window before `run_screen`'s cut (old loop kept only survivors) →
  +0.7 GB RSS at full catalog. Memory-only, profile-gated; fix = stream the cut
  per chunk. The cap keeps the interactive case small.
- **`scaling_tracker #3` (C++ coarse→medium fusion)** and the **radial coarse-pad**
  tightening — still deferred; the cap removes #3's urgency for the interactive
  path (full catalog stays batch-only).
- **Lock tradeoff:** position polling pauses during a (capped, few-second) screen.
  Full concurrent responsiveness would need per-request Satrec isolation
  (thread-local copies) — out of 7.3 scope, noted.
- **Concurrency is correct-by-construction, not race-tested** — a real concurrent
  race test would be flaky; the suite stays deterministic, so only the lock
  *wiring* is asserted.

---

## Function reference

```python
# conjunctions.py — the batched fine stage (Phase 7.3)
fine_filter_batch(satrecs, rows, step_sec) -> list
#   rows = medium_filter output [(i, j, jd_flag, d_flag), ...]; returns a list
#   aligned to rows: a geometry dict (jd_tca, miss_km, rel_speed_km_s, pos/vel
#   TEME for both) or None if the pair decayed across its window. Refines all
#   windows together via range-rate Newton + propagate_batch, chunked by
#   _FINE_CHUNK. fine_filter (scipy) is retained as the validation oracle.

# satellites.py — API
GET /api/conjunctions   # screen runs in run_in_threadpool under _propagator_lock;
#   413 when catalog_size() > ORBITWATCH_MAX_SCREEN_SATS (default 1500).
# _propagator_lock also guards /positions, /positions/{id}, /positions/{id}/track.

# propagator.py
SatellitePropagator.catalog_size() -> int   # cached count, for the cap
```
