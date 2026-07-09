# Task 10.4 — The C++ fine stage (Stage 2: identical events, first-ever full-catalog 24 h screen)

**Date:** Jul 9, 2026
**Status:** DONE (default **off**, like the sieve; production flips at 10.6 after
10.5's property tests + the in-CI A/B)
**Tests:** **576 passing + 4 skipped** (+8 `TestCppFine`), 3× flake-clean.
**Wall clock:** measurement batch 42m 29s (per-step times below; stated up front
this time — ~30 min estimated, the overrun was R3's slow *baseline* side).

---

## Goal

Move the fine stage — the 7.3 batched Newton-on-range-rate TCA refinement, still
73–81% of the sieved cascade at every scale (10.3, measured three ways) — into
the C++ engine inside the same GIL-released `screen_pairs` call, OpenMP across
rows, so that (a) the wall time collapses and (b) the per-window Python result
dicts (`scaling_tracker #7`, the memory wall that made the full-catalog 24 h
screen extrapolate to ~25 GB) never exist at all. Gate: **byte-identical
events**, as every Phase-10 stage before it.

## The design — superset contract, not a bitwise port

The C++ does **not** produce reported numbers. `refine_rows` re-runs the exact
7.3 algorithm per medium row (5 Newton steps from the bracket midpoint,
best-sample tracking, one fresh edge-widen retry) and returns only rows that
can pass the report cut *with margin*:

- **Euclidean mode:** converged miss ≤ threshold + ε.
- **SFS mode:** the miss vector at the converged TCA, in the primary's RTN
  frame (Vallado RSW, replicating `teme_to_rtn`), inside the pair's ellipsoid
  with every semi-axis inflated by ε. Volume choice replicates Python's
  first-wins `max(vi, vj, key=circumscribing_radius)` — j's only if STRICTLY
  larger.
- **Propagation failures survive** — Python adjudicates them (the 10.2 lesson:
  failure paths decided in C++ are the silent-failure bug class).

Survivors keep their ORIGINAL `(i, j, jd_flag, d_flag)` values, so
`fine_filter_batch` re-refines them through the exact same validated Python
path — its per-row math is elementwise, so the subset run is bit-identical to
the full run. **Events are therefore byte-identical by construction**; the only
thing the C++ must get right is "never drop a row Python would keep", which ε
protects with measured headroom.

**ε is evidence, not a guess:** on 933,864 real CI-slice rows, C++ vs NumPy
converged TCAs agree **exactly** (|ΔTCA| = 0.0 — the trial-time sequences are
identical) and misses to **5.7×10⁻¹⁴ km** (last-ulp of the final norm). The
0.01 km default carries ~12 orders of magnitude of headroom; the superset held
in a 0 → 0.01 km margin sweep.

**Mid-build correction (measured, like 10.1a's):** the plan's fallback global
pre-cut at the gross threshold (51 km) left **21.4%** of rows surviving — the
SFS radial axis is 0.4 km, so a gross ball is the wrong shape, and 2.8 M
surviving dicts at full/24 h would have rebuilt the very wall this stage
removes. The per-pair *ellipsoid* pre-cut collapses survivors to **0.12–0.36%**
(1,117 rows at the CI point; 15,479 at full/6 h).

**OpenMP:** rows are embarrassingly parallel; each row copies both elsetrecs
(Vallado's `sgp4()` mutates the record — shared satrecs are NOT thread-safe);
the keep flag is indexed by row and compacted serially, so output is
deterministic for ANY thread count (locked: 1 == 3 == default). CMake links
OpenMP optionally — without it the pragma compiles away, same results.

## Results

**Identity (the gate), fresh catalogs, same start as 10.3:**

| A/B | Events (base == refine) | Total | Step time |
|---|---|---|---|
| R0 CI point, **classic vs full flip** | 312 == 312 | 233.5 → **30.9 s** | 4m 28s |
| R1 CI point, sieve-only vs +refine | 312 == 312 | 209.8 → 30.0 s | 4m 04s |
| R2 full Starlink EUC 51 km 3 h | **312,042 == 312,042** | 291.9 → 76.5 s | 6m 16s |
| R3 full active SFS 6 h | 4,445 == 4,445 | 947.0 → **104.0 s (9.1×)** | 17m 40s |

**Clean profiler rows (Stage-1+2) vs the 10.3 same-catalog baselines:**

| Row (active, SFS, 30 s step) | classic | Stage-1 | **Stage-1+2** | RSS | Step time |
|---|---|---|---|---|---|
| CI point 4,821 / 24 h | 146.8 s | 69.7 s | **32.1 s (4.6×)** | 1.72 → **0.50 GB** | 36s |
| 10k / 24 h | 874.9 s | 393.5 s | **118.1 s (7.4×)** | 8.69 → **1.89 GB** | 2m 05s |
| Full 15,709 / 6 h | — | 323.7 s | **121.0 s** | 5.27 → 3.21 GB | 2m 08s |
| **Full 15,709 / 24 h** | **~25 GB — impossible (10.0)** | didn't fit | **304.6 s** | **5.38 GB** | 5m 12s |

The last row is the phase's finish line: 48.1 M coarse pairs, **17.07 M medium
windows refined in C++**, 9,052 events — the screen 10.0 measured as
un-runnable now takes 5 minutes in a third of the CI runner's memory.
`scaling_tracker #7` is closed *by construction*: per-window dicts never
accumulate (Python peaks at the ~15k survivors).

Post-10.4 the **medium scan is ~88% of the remaining full-catalog cost**
(269.6 of 304.6 s) — the propagation-refcount follow-up is the known next
lever, needed only if we ever want more than the cap lift requires.

## Findings

- **A conservative cut must match the report cut's SHAPE, not just bound it.**
  The gross-ball pre-cut was safe but useless (21.4% survivors); the ellipsoid
  pre-cut with the same ε is safe and ~200× tighter. Measured before shipped.
- **Exact FP agreement was free:** writing the C++ as the literal per-row
  transcription of the NumPy algorithm (same update, same guards, same clamp)
  made the trial-time sequences bit-identical — the measured |ΔTCA| = 0 means
  the margin protects against a failure mode that doesn't currently exist.
- **Euclidean mode's survivors ARE the events** (312,125 vs 312,042 kept —
  ε fringe only): that report cut keeps a quarter of all windows, so Python
  still re-refines 312k rows there. Inherent to the cut, not a defect;
  production (SFS) survivors are 0.1–0.4%.
- ~20% background-load noise on the box during the R runs (baseline sides ran
  slower than 10.3's same rows) — same-run ratios and the clean profiler rows
  are the citable numbers.

## Files

- `orbitcore/include/screening.h` + `src/screening.cpp` — `refine_rows` /
  `refine_debug` + `RtnAxes` (pure C++, `#pragma omp parallel for`, private
  satrec copies per row).
- `orbitcore/src/bind_screening.cpp` — `screen_pairs(..., refine=,
  refine_margin_km=, refine_axes=, refine_threads=)`; refine=True returns
  `(n_pairs, survivor_rows, n_flagged, t_refine_s)`; `_refine_oracle` debug
  binding (the permanent oracle hook, twin of `_sieve_anchors`).
- `orbitcore/CMakeLists.txt` — optional OpenMP.
- `backend/core/conjunctions.py` — `run_screen(refine=True)` (requires fused);
  survivors feed the untouched fine/RTN/cut/suppression/dedupe path; timings
  gain `n_survivors`/`t_refine_cpp` (booked under t_fine; `n_windows` stays
  the pre-refine count).
- `scripts/profile_screening.py` / `scripts/ab_screen.py` — `--refine`.
- `tests/test_conjunctions.py` — `TestCppFine` (8): requires-fused, oracle
  lock (vs `fine_filter_batch`, tolerances 1e-6 s / 1e-9 km vs measured
  0 / 5.7e-14), superset lock, Euclidean + asymmetric-ellipsoid identity
  (custom volumes — SFS Table-3 excludes the synthetic shell, the 7.2 lesson),
  thread invariance, crosser anchor case, validation.

## Function reference

```python
orbitcore.screen_pairs(..., refine=True, refine_margin_km=0.01,
                       refine_axes=[(r,t,n)...], refine_threads=0)
    -> (n_pairs, survivor_rows, n_flagged, t_refine_s)
orbitcore._refine_oracle(satrecs, rows, step_sec) -> (jd_tca[], miss_km[])
run_screen(..., fused=True, sieve=True, refine=True)   # identical events
# The full 10.6 flip A/B:
python scripts/ab_screen.py --source active --mode sfs --max-sats 5000 \
    --hours 24 --step 30 --refine          # classic vs fused+sieve+refine
python scripts/profile_screening.py --source active --mode sfs --sizes "" \
    --full --hours 24 --step 30 --start <now> --fused --sieve --refine
```
