# Task 10.2 — The C++ time sieve (identical events, medium scan 4.4–6.2× faster)

**Date:** Jul 9, 2026
**Status:** DONE (default **off**; production flips on at 10.6 after 10.3's formal
re-validation + the CI A/B)
**Tests:** **568 passing + 4 skipped** (+5 `TestTimeSieve`), full suite green.

---

## Goal

Build the 10.1b-validated time filter into `screen_pairs`: precompute, per pair,
the short time intervals around its mutual-node crossings and make the medium
scan evaluate the pair **only inside them** — with **byte-identical events** to
the unsieved path as the acceptance gate and `time_filter_gate.py` as the
independent oracle.

## What shipped

`orbitcore.screen_pairs(..., time_filter=True)` → `run_screen(..., sieve=True)`
(requires `fused=True`) → profiler `--sieve`. Default off everywhere.

**The pipeline inside** (all GIL-released, all in `screening.cpp`):
1. **Anchors** — `build_anchors`: 3 propagations per used sat (window
   start/mid/end) → rv2coe → equinoctial chord rates + measured curvature
   margin. **Cross-validated against the Python oracle to 2×10⁻¹¹** on the real
   CI catalog, and locked forever by `test_cpp_anchors_match_python_oracle`
   (via the `_sieve_anchors` debug binding, kept as the permanent regression
   hook).
2. **Interval builder** — `build_sieve_index`: per pair, node geometry at
   start/mid/end (smallest sin I_R → widest window, conservative); angular
   window `asin(D_eff/(r_p·sinI_R)) + 0.5° + curv + node-curvature margin`;
   crossings enumerated from a **linear phase model anchored on the exact
   osculating u at t₀**, widened by the equation-of-center bound `2·EoC_max/g`
   (no per-crossing Newton — ~10× cheaper to build; very eccentric sats
   degrade to whole-window); ±1 step pad; per-sat windows intersected
   two-pointer; CSR step ranges + per-step activation/deactivation index.
   Conservative fallbacks → whole-window: failed anchors, near-coplanar
   (`D_eff ≥ r_p·sinI_R`), degenerate rates, e ≥ 0.9, window wrap
   (`2·dt_half ≥ π/g`), >64 intervals.
3. **Activation-list scan** — `run_medium_scan(…, sieve)`: time-major loop
   unchanged (one shared `eval_pair` lambda = one source of truth for the
   no-skip flag logic); pairs activate at a range's first step (fresh window
   state — flagging starts at step 2, which the ±1-step pad exists for),
   evaluate while active, flush+deactivate after the last step (swap-remove
   with a position map, O(1)). v1 still propagates **all** used sats every
   step — deliberately: skipping propagation is the one bug class that fails
   *silently* (NaN → closed window); the per-sat refcount is the flagged,
   measured follow-up.

## The results (the acceptance gate)

Events byte-identical, sieve on vs off, on every catalog:

| Catalog | Events (off == on) | Medium scan | Whole cascade |
|---|---|---|---|
| **CI slice** 4,821 (SFS 24 h/30 s) | 366 == 366 | 107 → 20.7 s (**5.2×**) | 168 → ~80 s (**2.1×**), RSS 1.31 GB |
| **Full Starlink** 10,544 (3 h) | **253,392 == 253,392** | 110.9 → 25.3 s (4.4×) | 176 → 88 s |
| **Full active** 15,708 (SFS 6 h) | 3,816 == 3,816 | 382.6 → 62.0 s (**6.2×**) | 661 → 331 s |

Plus the offline suite locks: shell (Euclidean >1000 events + SFS), the
crosser, and a **two-shell fixture** proving the sieve drops spurious windows.

**Honest accounting:** the cascade total lands at ~2× — exactly the 10.0
prediction — because the fine stage now dominates (~53 of ~80 s at the CI
point). That is Stage 2's job (10.4). The remaining medium cost is mostly the
v1 unconditional propagation floor (~15 s), not pair evaluations.

## Findings (things learned building it)

- **On a single shell the sieved ROW set is byte-identical, not just events**
  (26,591 == 26,591 at 24 h): every medium flag really does sit near a node
  crossing there, because flag-run width and window width both scale with
  1/sin I_R. Spurious windows — the thing the sieve visibly skips — are a
  *cross-shell* phenomenon (hence the two-shell test fixture).
- **`run_screen`'s event sort needed a total order** (miss, sat1, sat2, tca):
  byte-equal miss distances DO occur on symmetric geometry, and the old
  miss-only sort leaked the scan's internal row order into the output — the
  sieved and unsieved paths disagreed on tie order while being set-identical.
  Real catalogs have no exact ties, so production output is unchanged.
- **Review catch — node-path nonlinearity was the one unbounded error
  source**: the builder advances the node angle linearly, but u_node is a
  nonlinear function of the two precessing planes (1/sin I_R-amplified) — and
  the oracle never exercised this, since it recomputes geometry exactly per
  time. Fix: the already-computed midpoint node evaluation measures the actual
  chord deviation (half-chord mismatch / 2, ×1.5) and widens the window —
  the same measured-curvature discipline as the anchors. Cost ~2 s at the CI
  point; pathological swings degrade to whole-window via the wrap check.

## Structure (the two file splits, committed separately before the sieve)

- `screening.cpp`/`screening.h` — the pure-C++ engine (coarse cut, medium
  scan, anchors, sieve): no pybind11, Stage-2/OpenMP-ready.
- `bindings.cpp` (37-line module entry) + `bind_satrec.cpp` /
  `bind_propagation.cpp` / `bind_screening.cpp` — one topic per file,
  mirroring the pipeline (record → propagate → screen). Both splits verified
  byte-identical (suite + real-catalog spot checks) before any sieve code.

## Residual risks (tracked → 10.3/10.6)

- Three-sample node-curvature measurement could theoretically alias an
  extreme >π/day node swing — requires huge relative precession AND tiny
  sin I_R simultaneously (physically anti-correlated; absent across all
  15,708 active sats). 10.3's re-validation on fresh catalogs is the check.
- Sieve margin kwargs (`sieve_u_margin_deg`, `sieve_osc_margin_km`) exist on
  `screen_pairs` but aren't plumbed through `run_screen` (defaults = spec
  values); plumb when a caller needs them.
- Row order in the sieved path differs from the full scan (activation-list
  order); invisible downstream since the event sort is now a total order.

## Function reference

```python
orbitcore.screen_pairs(satrecs, peri, apo, pad, jd0, jd1, step, thr,
                       time_filter=False, sieve_u_margin_deg=0.5,
                       sieve_osc_margin_km=10.0) -> (n_pairs, rows)
orbitcore._sieve_anchors(satrecs, jd0, jd1) -> dict   # oracle-lock hook
run_screen(..., fused=True, sieve=True)               # identical events, ~5x medium
# profiler: python scripts/profile_screening.py --source active --mode sfs \
#           --sizes 5000 --hours 24 --step 30 --start <now> --fused --sieve
```
