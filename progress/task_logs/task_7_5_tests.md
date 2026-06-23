# Task 7.5 — Tests (screening-volume logic + scale/perf regression)

**Date:** Jun 23, 2026
**Status:** DONE — closes Phase 7
**Tests:** 432 passing, 1 skipped (was 429) — +3 (`TestScaleRegression`)

---

## Goal

The last Phase-7 item: a **consolidation pass** that closes the testing gaps
7.2/7.3/7.4 couldn't close from inside their own scope. Those tasks each shipped
tests test-first, so the per-stage coverage was already strong. What was missing
were the **cross-task regression locks** — invariants that span the 7.2 ellipsoid
wiring and the 7.3 batched fine stage, enforced at *constellation scale* rather
than only on the ~8 deterministic crosser windows. The aim: tests that fail
loudly if a future "optimization" silently changes screening results.

---

## Approach

Three locks, all deterministic + offline (a synthetic dense shell, no network),
added as `TestScaleRegression` in `tests/test_conjunctions.py`:

1. **Oracle-vs-batch equivalence at shell scale (the keystone).** The 7.3
   headline — *the batched Newton fine stage is byte-identical to the scipy
   oracle on dense catalogs* — was only asserted on ~8 crosser windows
   (`TestFineFilterBatch`). This runs coarse→medium over a **300-sat shell**,
   gets its **3,331 real windows**, and refines a stride-sampled **257** of them
   *both* ways (`fine_filter_batch` vs the per-window `fine_filter` oracle),
   asserting agreement to **<10 m / <50 ms / <1 mm/s**.
2. **Screen determinism at scale.** A full shell screen run twice must be
   byte-identical (events, order, every float). The count/equivalence regression
   signal is only trustworthy if the screen is deterministic — this locks that
   the batched stage (NaN masking, einsum reductions, chunk boundaries) and the
   sort introduce no nondeterminism.
3. **No-skip gross-threshold wiring (7.2).** A spy on `orbitcore.medium_filter`
   confirms `run_screen(volumes=…)` hands the C++ medium filter the **largest
   semi-axis (51 km)** — *not* the tight radial axis (would skip in-track-loose
   events) and *not* the box corner `sqrt(R²+T²+N²)≈67.4 km` (would over-screen).
   Unit tests prove `circumscribing_radius()` in isolation; this proves
   `run_screen` actually passes it down.

**Why a spy instead of an in-track SGP4 fixture.** The plan floated a real
in-track-dominated encounter to exercise no-skip end-to-end. Building one with
SGP4 is fragile (the crosser fixture is *radial*-dominated by construction — SFS
deliberately excludes it). The spy locks the same guarantee deterministically at
the integration boundary, and the pure-geometry no-skip is already covered by
`test_every_interior_point_within_circumscribing_radius` (surface sampling). No
gap left open.

**Why compare only genuine crossings (rel speed > 1 km/s).** A co-moving
same-plane pair has an **ambiguous TCA** — a near-flat distance objective with
many near-equal minima — that *both* solvers place arbitrarily, so it isn't a
meaningful equivalence case (and the SFS path suppresses it anyway). The filter
is a safety net: in practice all 257 sampled windows are genuine crossings, so
it isn't load-bearing, but it keeps the test correct if a future shell change
introduces co-movers.

---

## Implementation

| File | Change |
|------|--------|
| `tests/test_conjunctions.py` | `_shell_satrecs(n)` helper (deterministic shell → propagator → satrecs+meta, both outliving the temp parquet); `TestScaleRegression` (3 tests) |

No source changes, **no `.so` rebuild**.

---

## Validation

- **Full suite:** 432 passing, 1 skipped — offline/deterministic.
- **Flakiness:** `TestScaleRegression` run 3× back-to-back, clean each time —
  the determinism claim holds.
- **Mutation-checked (the tests genuinely bite, not vacuous):**
  - Keystone TCA tolerance — inject a **+100 ms** TCA error into the batch
    result → **all 257** compared windows fail the 50 ms assertion.
  - Gross-threshold wiring — make `circumscribing_radius()` return the **radial
    axis** (the realistic regression) → captured gross becomes **0.4 km**, the
    `== 51.0` assertion fails.
- **Guard margins (version-drift-robust):** rows 3,331 (guard >50, 66×),
  compared 257 (guard >30, 8.5×), determinism events 1,698 (guard >5, 340×).

---

## Test coverage

| Test (`TestScaleRegression`) | Covers |
|------|--------|
| `test_batch_matches_oracle_at_shell_scale` | 7.3 batched Newton fine stage == scipy oracle across 257 real shell windows (<10 m / <50 ms / <1 mm/s) |
| `test_screen_is_deterministic_at_scale` | A full shell screen is byte-identical run-to-run (events, order, floats) |
| `test_medium_gross_threshold_is_largest_semi_axis` | 7.2 no-skip wiring: `run_screen` passes the largest semi-axis (not radial, not box corner) to `medium_filter` |

---

## Lessons learned

- **Comparing two TCA solvers is only meaningful where the TCA is well-defined.**
  On a co-moving pair (|Δv|≈0) the distance objective is near-flat and the
  minimum is ambiguous; the scipy oracle and the batched Newton solve can land on
  different times without either being "wrong." Gate any solver-vs-solver
  equivalence test on a real crossing (rel speed clearly > 0). This is why
  `TestFineFilterBatch.test_co_moving_pair_is_finite_not_nan` only checks
  finiteness, never agreement.
- **A synthetic shell needs density to produce *natural* windows.** At 120 sats a
  single 53° shell is too sparse — cross-plane approaches all miss by >51 km, so
  only a *seeded* crosser closes. ~300 sats + a 100 km pad over 3 h yields
  thousands of real windows. The pad sizes the *window set*; the equivalence
  being locked is independent of miss magnitude.
- **C++ satrecs outlive the temp parquet.** `_shell_satrecs` loads via a
  propagator inside a `TemporaryDirectory`, then returns the satrec list — the
  in-memory Satrec objects (and the plain meta list) hold no reference to the
  file, so they stay valid after cleanup. Lets the helper return a clean
  `(satrecs, meta, start)` tuple instead of forcing all screening inside the
  `with` block.
- **A regression test is only worth its runtime if it bites.** Mutation-checking
  (inject the exact error the test guards, confirm it fails) is the cheap proof
  that a green test isn't vacuous — done here for both numeric tolerance and
  wiring.

---

## Remaining risks / deferred

- **Wall-clock perf is guarded loosely, by design.** The scale guard is
  *count/equivalence-based*, not a millisecond benchmark (those flake on loaded
  machines). `TestDenseShellScale` keeps its loose `< 30 s` pathological-
  regression bound; precise perf numbers live in the 7.1/7.3 profiling logs.
- **Deferred Phase-7 perf items unchanged** (not 7.5's scope): scaling_tracker #3
  (C++ coarse→medium memory fusion), radial coarse-pad tightening, #7 (fine-stage
  result-dict streaming). Full catalog stays batch-only.

---

## Function reference

```python
# tests/test_conjunctions.py
_shell_satrecs(n) -> (satrecs, meta, start_utc)
    # Deterministic dense single shell (no network) → propagator → index-aligned
    # satrecs + meta; both outlive the temp parquet. Drives the scale regressions.
```
