# Task 10.0 — Measurement gate: path filter vs time filter (profile-first)

**Date:** Jul 8, 2026
**Status:** DONE — **the gate killed Phase 10 as originally specced and re-scoped it**
**Tests:** 556 passing + 4 skipped (unchanged; the gate is measurement, not features.
`profile_screening.py` gained `--source active / --mode sfs / --start`, covered by the
existing `TestProfileHarness` signature guard.)

---

## Goal

The roadmap scheduled Phase 10 as the smart-sieve **path filter** (Hoots-Crawford-
Roehrich Filter II): a geometric orbit-to-orbit pre-cut between `coarse_filter` and
`medium_filter`, targeting the 49% coarse survival measured in 7.1. Planning surfaced a
geometric doubt: *the path filter compares orbit radii at the mutual node line, and two
near-circular co-altitude orbits genuinely intersect there* — so it cannot cut the pair
class a megaconstellation is made of. 10.0 spent one day measuring, on the real
catalogs, before committing days of C++ to the wrong stage. (7.1's own lesson —
"profile before optimizing; the assumed bottleneck was the wrong one" — applied to
Phase 10 itself.)

## Approach

A throwaway **NumPy prototype** of the conservative no-skip path bound + a **time-filter
work estimate**, swept over every coarse-surviving pair of three real catalogs, plus
real-cascade profiles at the production operating point. Preserved (it doubles as the
Stage-1 validation oracle) in `progress/week10_planning/path_gate.py` +
`gate_sanity.py` (6 hand-built orbit-pair cases, all PASS).

**The conservative drop test** (both relative nodes, vectorized over pair blocks):

- `d(P1,P2) ≥ dist(P2, plane 1) = r₂·sin I_R·|sin u₂|` (P1 lies in plane 1), and
  symmetrically — so any approach within `D_eff` requires **both** points inside
  angular windows `|sin u| ≤ D_eff/(r_p·sin I_R)` around the mutual node line
  (`cos I_R = cos i₁cos i₂ + sin i₁sin i₂cos ΔΩ`; node direction `k = ĥ₁×ĥ₂`).
- `d(P1,P2) ≥ | |r₁| − |r₂| |` (reverse triangle inequality) — so within those
  windows, interval-evaluate `r(ν) = p/(1+e·cos ν)` and drop the pair only if the
  radius-interval **gap exceeds `D_eff` at BOTH nodes**. Opposite-node combinations
  are excluded by the node-axis projection (`d ≈ r₁+r₂ ≫ D_eff`).
- **Margins (realistic mode):** `D_eff = gross + 10 km (mean-vs-osculating J2
  short-period) + 1 km/day drift`; node windows widened by relative nodal-precession
  drift `|Ω̇₁−Ω̇₂|·T·(1 + 2·sin i/sin I_R)` + 1% secular-advance model error; ν
  intervals widened by `|ω̇|·T`. Elements are secularly advanced from each sat's
  epoch to screen start (epochs are per-sat and ~11 days old in the Starlink cache —
  skipping this step invalidates the geometry entirely).
- Near-coplanar pairs degrade naturally: the arcsin clips to π/2, the radius interval
  becomes the whole orbit `[r_p, r_a]`, and the test reduces to the coarse test (keep).

**Time-filter ceiling** (Filter III estimate): each object transits its node window
`2δu` twice per rev, so with independent phases a pair needs distance evaluation in
only `≈ 2·(δu₁/π)(δu₂/π)` of scan steps. Summed over survivors = the predicted
medium pair-step work vs. a full scan.

## Results (the deliverable)

**Correctness first — the bound is safe.** Event-level no-skip verified against the
real C++ coarse+medium cascade + fine oracle:

| Catalog / config | strict violations (dropped but medium-flagged) | TRUE violations (fine miss ≤ gross) |
|---|---|---|
| Dense shell 300 @ 51 km/24 h/60 s | 0 | 0 |
| Dense shell 800 @ 51 km/24 h/60 s | 0 | 0 |
| Starlink 10,544 @ 51 km/3 h/60 s | 19 | **0** |
| Active CI-slice 4,821 @ 51 km/24 h/30 s | 4,227 | **0** |

⚠ **The no-skip contract is EVENT-level, not flag-level.** `medium_filter` flags on a
conservative interval bound (`d_sampled − v̂·dt/2 − curv < threshold`), so fast pairs
sampled hundreds of km apart flag routinely without any true sub-threshold approach.
A geometric filter that drops such a pair changes zero events. Flag-level equivalence
is unachievable *and wrong as a spec* — 10.1 must state the event-level contract.

**The gate table** (path drop % is of coarse survivors; realistic = margins on):

| | Starlink 10,544 | Active CI-slice 4,821 | Active full 15,708 |
|---|---|---|---|
| Coarse survivors | 49.3% (27.4 M) | 25.2% (2.93 M) | 39.0% (48.1 M) |
| Path drops — idealized | 0.5% | 5.7% | 2.3% |
| **Path drops — realistic** | **0.002%** | **1.9%** | **0.4%** |
| **Time-filter ceiling** (medium pair-step work left) | **0.31%** (~320×) | **1.86%** (~54×) | **1.12%** (~89×) |

**Production profiles** (local, `--source active --mode sfs --step 30 --hours 24`,
start 2026-07-08T12:00Z; CI runner is slower than this machine):

| N (screenable) | pairs | windows | events | t_medium | t_fine | total | peak RSS |
|---|---|---|---|---|---|---|---|
| 4,821 (CI point) | 2.93 M | 967 k | 366 | 81 s | 74 s | 157 s | 1.7 GB |
| 9,795 | 16.5 M | 5.36 M | 3,008 | 368 s | **479 s** | 852 s | **8.7 GB** |
| 15,708 (extrapolated) | 48.1 M (measured) | ~15 M | — | ~18 min | ~23 min | ~40 min | **~25 GB → does not fit the 16 GB CI runner** |

(366 events vs. the live site's 367 — same screen, different fetch day.)

## Findings → the re-scope

1. **The path filter is dead** — 0.002–1.9% realistic cut. Near-circular co-altitude
   orbits (the megaconstellation pair class) intersect at their mutual nodes; no
   geometric test can drop them, and margins erase the marginal cases. The C++ effort
   the gate cost ~a day to avoid.
2. **The time filter is the real sieve** — 98–99% of medium pair-step work removable.
   Built fused in C++ (coarse + node geometry + transit-window intersection in one
   GIL-released stage emitting per-pair time windows), it also eliminates the 48 M-pair
   Python materialization (~8.7 GB of tuples — `scaling_tracker #3`).
3. **The fine stage is the actual cap blocker.** 47% of the cascade at the CI point,
   **56% at 10 k and growing** — a sieve alone caps the total win at ~2× and cannot
   lift the 5000 cap: post-sieve, fine is ~90% of a full-catalog screen (~23 min
   single-thread) and its per-window result dicts are the next memory wall
   (`scaling_tracker #7`). Cap-lift requires the Newton refinement in C++: GIL-free,
   OpenMP across windows (embarrassingly parallel), results streamed.

**Decision (Jose, Jul 8): full re-scope.** Phase 10 = **Stage 1** fused C++ time-sieve
(≈2× at the CI point + pair-list memory gone) then **Stage 2** C++ fine stage (lifts
the cap to the full ~16 k catalog, est. 5–8 min on the 4-vCPU runner). Each stage
gates on **byte-identical events**. Roadmap rewritten accordingly.

## Implementation notes (what exists after 10.0)

| File | Change |
|---|---|
| `scripts/profile_screening.py` | `--source active` (HEAD-slice + screenable filter — exactly `build_snapshot.py`'s production path, NOT the densest-shell slice), `--mode sfs` (the ellipsoid path CI runs; `--threshold` ignored), `--start` (ISO; the fixed 2026-06-01 instant is wrong for a freshly fetched catalog). Existing `TestProfileHarness` green (kwargs with defaults). |
| `progress/week10_planning/path_gate.py` | The gate prototype (bound + sweep + no-skip checker + time ceiling) — becomes the Stage-1 validation oracle in 10.5. |
| `progress/week10_planning/gate_sanity.py` | 6 hand-built geometry cases (crossing / apogee-side drop / near-coplanar keep / perigee-at-node keep / margins / coarse-disjoint), all PASS. |
| `backend/data/tle/active.parquet` | Full active fetch cached (15,928 objects, gitignored) — CelesTrak was reachable (no VPN block this session). |

## Lessons learned

- **Run the geometry gate before the C++.** The roadmap's Phase-10 spec (written
  Jun 24 from the SOCRATES research) assumed the path filter was the missing stage;
  one day of NumPy against real catalogs showed it delivers ≤1.9% while sitting next
  to a ~50–300× lever (the time filter) and a growing bottleneck it doesn't touch
  (fine). The gate reordered the whole phase.
- **Mean-element geometry must be evaluated at screen time.** Parquet elements are
  per-sat epoch snapshots; Starlink RAANs precess ~5°/day and cache epochs were ~11
  days old — geometry computed at epoch is ~55° stale. Secularly advance Ω/ω first.
- **A conservative filter's empirical check needs the right contract**: verify
  against *events* (fine-refined misses), not medium *flags* — medium over-flags by
  design (its bound subtracts `v̂·dt/2` ≈ hundreds of km for fast pairs).
- **`ru_maxrss`-style extrapolation says the 16 k screen can never run in CI as-is**
  (~25 GB vs 16 GB) — the 9.4 "38 min" measurement was almost certainly memory
  pressure, not just CPU. Memory, not time, is what the C++ stages must fix first.
- Background the long runs; `zsh` heredocs + `python -u`; a `cd` in one Bash call
  doesn't persist — absolute paths for background jobs.

## Function reference (prototype — not production API)

```python
# progress/week10_planning/path_gate.py
load_elements(df) -> dict            # element arrays + J2 secular rates (rad/day)
advance_to(d, jd_start) -> dict      # secular Ω/ω advance epoch -> screen start
pair_block(d, I, J, D_gross, T_days, realistic) -> {coarse, drop, frac, sinIR}
sweep(d, D_gross, T_days, realistic, flagged=None) -> counts + violations
noskip_check(df, D, hours, step_sec, jd_start, realistic) -> counts + TRUE violations
# CLI: python path_gate.py --parquet X.parquet [--screenable-only] [--max-sats N]
#      [--gross 51] [--hours 24] [--step 60] [--start ISO] [--noskip]
```
