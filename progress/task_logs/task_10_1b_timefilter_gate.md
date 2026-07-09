# Task 10.1b — Time-filter gate: validated no-skip before any C++

**Date:** Jul 8, 2026
**Status:** DONE — **GO.** The time filter's construction is proven no-skip on
**1,403,999 real events across 4 catalogs (0 uncovered)**; the C++ build (10.2)
now has an executable spec and an independent oracle.
**Tests:** 563 passing + 4 skipped (unchanged — the gate is prototype-side; the
only pipeline change is 3 read-only satrec accessors, covered by the suite).

---

## Goal

The 10.0 gate showed the smart sieve's real lever is the **time filter**
(H-C-R Filter III): conjunctions can only happen while both sats transit small
angular windows around their mutual node line, so the medium scan can skip
98–99% of its pair-step work. 10.0 *estimated* that ceiling but never emitted a
real window or proved no events get skipped. Per the 10.1b plan, this task
validates the time filter **in the prototype first** — a hard go/no-go gate —
so a no-skip failure costs a day of Python, not a `.so` rewrite.

## The result (the deliverable)

Every real event (fine-refined miss ≤ gross) checked at its **TCA** against the
predicted windows:

| Catalog | Events | Uncovered @0.25° | Mutation (0°) | Coverage → medium cut |
|---|---|---|---|---|
| Active head-800 (worst-case, 15% coplanar) | 1,401 | 0 | 1 | ~6× |
| **CI slice 4,821 (24 h/30 s — production)** | **197,523** | **0** | 59 | **2.47% → ~41×** |
| Full Starlink 10,544 (3 h) | 253,392 | 0 | 256 | 0.77% → ~130× |
| Full active 15,708 (6 h) | 951,683 | 0 | 351 | 0.60% → ~168× |
| 5 km Euclidean mode (CI slice, review round) | 1,681 | 0 | 56 | (SOCRATES-validation mode holds) |

Shipping margin 0.5° = 2× validated headroom, at ~zero coverage cost (coverage
is pinned to the near-coplanar floor: 2.0% of CI-slice pairs, 0.35% full-active).

**Honest framing:** medium goes from ~50% of the cascade to near-zero → Stage 1
total ≈ 1.5–2× at the CI point. The 41–168× matters because it makes the
*full-catalog* medium scan trivial; the cap-lift still needs Stage 2 (fine).

## The construction that survived (see `stage1b_timefilter_spec.md` for full spec)

Per sat: **3 propagations** (window start/mid/end) → osculating elements
(rv2coe) → **equinoctial chord rates** (λ = ω+M and Ω differenced over the
window) → **per-sat measured curvature margin** (midpoint second difference,
×1.5). Per pair, at time t: node geometry recomputed from advanced (i, Ω);
active iff both sats within `arcsin(D_eff/(r_p·sinI_R)) + 0.5° + curv` of the
node line (mod π); near-coplanar / failed-anchor → always active. Exactness of
the underlying predicate verified separately: |r·ĥ_other| ≤ gross at the TCA of
8,206 events via actual propagated states — 0 exceptions.

## The four failure modes (the real content — each found by the gate, measured, cured)

1. **Validate at TCA, not at `jd_flag`.** The medium flag is a *sampled* step,
   ~1 step (~2° of motion) from the true crossing — even the *exact* osculating
   predicate "fails" 85 k times at jd_flag. The correctness contract is
   TCA-coverage; the C++ pads windows ±1 step **in time** (exact), never angular.
2. **Anchor at screen start, not epoch.** Advancing mean elements from epoch
   with linear rates diverges with SGP4's drag-secular t² terms: **35° of
   along-track error at a 21.8-day epoch age** (and binding the satrec's own
   `mdot/argpdot/nodedot` did NOT fix it — the *local* rate at a stale epoch age
   differs from the at-epoch rate; that hypothesis was tested and disproved).
3. **Equinoctial λ, not (ω, M).** For near-circular orbits the osculating
   (ω, M) split is ill-conditioned — J2's forced eccentricity-vector wobble
   (~1e-3) is as large as e, so the perigee direction swings arbitrarily
   between anchors and the M-unwrap can pick the wrong branch. Observed on an
   ordinary 573×588 km sat (e=0.0011): margin-independent misses. Chord the
   sum λ = ω+M; keep the split only inside the equation-of-center, where any
   branch error is suppressed to ≤ 2e.
4. **Measured per-sat curvature margins, not a flat margin.** Actively
   reentering sats (143–217 km perigee; ndot up to 0.127 rev/day² — 300×
   typical; one bogus bstar = −0.19) need up to ~6° where normal sats need
   ~0.1°. A flat margin either misses them or erases the win. The midpoint
   second difference measures each sat's actual drift curvature **model-free**
   (for perigee < 220 km SGP4 sets isimp=1 → the drift is exactly quadratic →
   the bound is exact; ×1.5 covers higher-order tails elsewhere).

## Implementation

| File | Change |
|------|--------|
| `progress/week10_planning/time_filter_gate.py` *(new)* | The gate prototype = **executable spec + the 10.2/10.5 oracle**: `_osculating` (vectorized rv2coe), `anchor_at` (3-point chord anchor + curvature), `pair_active_at` (the membership predicate), `run_gate` (screen → `fine_filter_batch` ALL windows → check every event at TCA; npz event cache for fast margin re-sweeps), `coverage` (sampled realized win). CLI: `--parquet --max-sats --screenable-only --gross --hours --step --start --margins --cache`. |
| `progress/week10_planning/stage1b_timefilter_spec.md` *(new)* | The prose spec: construction, the four failure modes, results table, C++ integration notes (per-pair time intervals — NOT per-step membership checks, which would cost as much as the distance they replace; δt = δu/u̇_min conservative conversion; ±1-step pads). |
| `orbitcore/src/bindings.cpp` | `Satrec` gains read-only `mdot` / `argpdot` / `nodedot` (secular rates, rad/min — set by sgp4init). Used as branch-safe unwrap references; also how the rate-error hypothesis was disproved. Additive; suite green. |
| `backend/core/conjunctions.py` | Comment-only: the fused branch repeated the corrected-elsewhere "~8.7 GB pair list" claim (review catch — grep for every copy of a corrected claim). |

## Validation

- **No-skip:** exhaustive, not sampled — `fine_filter_batch` refines *all*
  flagged windows (967 k / 1.06 M / 4.35 M), every real event checked at its
  TCA. 0 uncovered at 0.25° on every catalog; ship 0.5°.
- **Mutation:** margins zeroed → 1 / 59 / 256 / 351 / 56 violations — the check
  provably bites on every catalog.
- **Reproducibility:** CI-slice sweep re-run from the event cache after the
  review edits — byte-identical counts.
- **Review round (with 10.1a):** oracle docstrings brought in line with v3 (doc
  drift in an executable spec = a 10.2 correctness hazard), dead v1 code
  reverted (git-verified byte-identical to 10.0), the 5 km-mode check added.

## Lessons learned

- **The gate paid for itself four times over.** Each failure mode would have
  been a C++ debugging session against silently-missing conjunctions; in Python
  each was a visible violation count with inspectable offenders.
- **Chase the outlier cluster.** Every breakthrough came from asking why
  violations concentrated (one sat, one pair class): sat 584 → conditioning;
  pair (1700, 4423) → reentry curvature. Aggregate counts alone would have
  suggested "widen the margin" — the wrong fix both times.
- **Disprove hypotheses cheaply before building on them.** The "satrec rates
  will fix it" theory cost one binding + one run to kill; the review earlier
  caught the same pattern in 10.1a's memory claim.
- **A validation can be wrong in a way that flatters the design** (checking at
  jd_flag produced false violations — the opposite failure is falsely passing;
  the exact-predicate-at-TCA cross-check pinned the contract before tuning).
- `ru` conditioning: osculating (ω, M) is untrustworthy below e ≈ 2·(J2 forced
  wobble) ≈ 0.002 — use equinoctial elements for anything phase-critical.

## Function reference (prototype — the 10.2 oracle, not production API)

```python
# progress/week10_planning/time_filter_gate.py
anchor_at(satrecs, jd0, jd1) -> dict        # 3-point chord anchor + curv margins
pair_active_at(d, I, J, t, jd0, D_eff, u_margin) -> bool[]   # THE predicate
run_gate(df, gross, hours, step_sec, jd0, margins_deg, cache) # exhaustive no-skip
coverage(df, d, ...) -> float               # realized pair-step fraction (sampled)
# CLI: python time_filter_gate.py --parquet X --screenable-only [--max-sats N]
#      [--margins 0,0.25,0.5] [--cache events.npz]   # margin 0 = mutation check
```
