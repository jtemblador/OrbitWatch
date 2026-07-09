# Task 10.1a — Fused C++ coarse+medium stage (survivor pairs never enter Python)

**Date:** Jul 8, 2026
**Status:** DONE
**Tests:** **562 passing + 4 skipped** (+6: `TestFusedStage`), full suite green.

---

## Goal

The first, low-risk half of the re-scoped Phase 10 (Stage 1). The 10.0 gate showed
the coarse→Python→medium round-trip materializes the whole survivor-pair list as
Python tuples — measured 48.1 M pairs on the full active catalog. `screen_pairs`
fuses the coarse altitude-band cut and the time-stepped medium scan into ONE
GIL-released C++ call, so the pair list lives only in C++ and never crosses into
Python. **Byte-identical results** to `coarse_filter` + `medium_filter`; it changes
memory, never the screen.

## Approach — a shared scan, two entry points

The validated 6.3 medium scan was extracted verbatim into a pure-C++ helper
`run_medium_scan(sats, P, jd_start, jd_end, step_sec, threshold_km)`, called by BOTH:

- `medium_filter` — pairs handed in from Python (unchanged public behavior), and
- `screen_pairs` — pairs built internally from the coarse cut (identical
  `coarse_filter` logic inlined) and fed straight into the scan.

One source of truth for the no-skip screening logic; the refactor's safety net is
that `medium_filter`'s 143 existing tests still pass byte-identical, proving I didn't
perturb the validated path. Also factored out `extract_satrecs` (the None-checked
pointer cast shared by both bindings).

`screen_pairs` returns `(n_pairs, rows)` — the survivor **count** comes back for the
survivor-reduction profile (Stage 1b) WITHOUT ever materializing the pairs in Python.

## Implementation

| File | Change |
|------|--------|
| `orbitcore/src/bindings.cpp` | New anonymous-namespace helpers `MediumResult`, `extract_satrecs`, `run_medium_scan` (the extracted 6.3 scan, GIL held by caller). `medium_filter` refactored to call them (behavior unchanged). New `screen_pairs` binding: validate → `extract_satrecs` → `{ gil_release: inline coarse cut → `run_medium_scan` }` → return `(n_pairs, rows)`. |
| `backend/core/conjunctions.py` | `run_screen(..., fused=False)`: when True, one `screen_pairs` call replaces the coarse+medium two-call sequence; jd-window computation hoisted above the branch; timings fold `t_coarse` into `t_medium` and take `n_pairs` from the return. Default False keeps the exact Phase-6/7 path (the 7.2 spy test still sees `medium_filter`). |
| `scripts/profile_screening.py` | `--fused` flag threads through `_profile_one` → `run_screen`. |
| `tests/test_conjunctions.py` | `TestFusedStage` (6): exposed; rows byte-identical to coarse+medium at shell scale (+ `n_pairs == len(coarse)`); run_screen events identical fused/non-fused (Euclidean non-vacuous >5, SFS structural); timings shape; empty-survivor early return; input validation (length/pad/window/step/None-satrec). |

## Validation

- **Byte-identical rows:** `screen_pairs` == `medium_filter(coarse_filter(...))` exactly —
  400-sat Starlink slice (79,800 pairs / 4,388 rows) and the 300-sat synthetic shell
  (thousands of windows). Same `(i,j,jd,d)` tuples, same order.
- **Byte-identical events through `run_screen`:** fused vs non-fused identical dict-for-dict
  (order included) — synthetic shell (Euclidean, >5 events) AND **a real active slice
  (n=2,338 → 78 SFS events, identical)**, so the SFS report-cut/suppression/de-dupe path
  is confirmed on real data, not just structurally.
- **`medium_filter` unchanged:** its 143 `test_sgp4_cpp` + `test_conjunctions` tests pass
  post-refactor — the extraction is provably behavior-preserving.
- Full suite **562 + 4 skipped**.

## The memory win — measured, and honestly bounded

`screen_pairs` eliminates the Python survivor-pair list. Direct measurement of what
that list costs (active, screenable, gross 51 km):

| operating point | coarse pairs | Python pair list (removed) | C++ vector (fused holds) |
|---|---|---|---|
| CI slice (4,821) | 2.93 M | ~0.35 GB | 0.023 GB |
| full active (15,708) | 48.1 M | ~5.4 GB | 0.38 GB |

Peak-RSS, whole SFS screen (24 h / 30 s), fused vs the 10.0 non-fused baseline
(per-process high-water mark; **times are CPU-contended here from a concurrent run —
memory is the clean signal, and 1a doesn't change the algorithm's time**):

| N (screenable) | non-fused peak (10.0) | fused peak | Δ |
|---|---|---|---|
| 4,821 (CI point) | 1.71 GB | **1.34 GB** | −0.38 GB |
| 9,795 (10k point) | 8.71 GB | **6.55 GB** | −2.16 GB |

**The honest finding (corrects the 10.1 plan):** the pair-list materialization is a real,
zero-risk chunk to remove, but it is **not** the dominant share of peak RSS. At 10k the
Python pair list is ~2 GB of the 8.7 GB; the larger chunk is the fine stage's per-window
result dicts (`scaling_tracker #7`: ~5.36 M dicts at 10k). So **1a alone does not get the
full 16k catalog under the 16 GB CI runner** — the fine-stage memory (Stage 2 / #7 streaming)
and/or the time filter (1b, fewer windows into fine) are also required. 1a's true role: a
clean memory reduction proportional to the survivor count, and the **fused primitive Stage 1b
builds the time filter into**.

## Lessons learned

- **Measure the thing you're optimizing before claiming the win.** The plan assumed the
  pair list was the bulk of the 8.7 GB; direct `sys.getsizeof` + peak-RSS showed it's ~2 GB
  at 10k, with the fine-stage dicts the bigger term. Same 10.0 lesson, one level in.
- **Extract-and-share beats duplicate** for the validated scan: `medium_filter`'s own test
  suite becomes the proof that the refactor is behavior-preserving.
- Offline SFS equivalence is only structurally testable on the synthetic shell (0 SFS events
  by construction — radially-dominated crossings, 7.2); the non-vacuous SFS proof needs real
  data (the 78-event active slice), so it lives in a manual cross-check, not the offline suite.

## Function reference

```python
# C++ (orbitcore):
screen_pairs(satrecs, periapsis_km, apoapsis_km, pad_km,
             jd_start, jd_end, step_sec, threshold_km) -> (n_pairs, rows)
#   Fused coarse+medium. rows identical to medium_filter(coarse_filter(...)).
#   n_pairs = coarse survivor count (the pairs themselves never return to Python).

# Python:
run_screen(..., fused=False)   # fused=True -> screen_pairs; byte-identical events
```
