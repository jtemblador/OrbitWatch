# Task 6.10 — Round Out / Consolidate Tests (Phase 6 close)

**Date:** Jun 21, 2026
**Status:** DONE
**Tests:** 377 passing, 1 skipped (was 374) — +3, all in `tests/test_conjunctions.py`

---

## Goal

Final Phase-6 task: confirm every stage of the conjunction cascade is tested, close the few real
gaps, and add a single deterministic end-to-end integration test anchored to an independently
validated value. Because the pipeline was built test-first (6.1–6.9), this was a *consolidation*
pass, not a write-from-scratch one.

---

## Approach

**Audited first (look before building).** Every per-stage unit test the 6.10 plan called for already
existed — so 6.10 added only the genuine gaps, avoiding duplication (1plan simplicity rule).

The one substantive deliverable: a deterministic full-pipeline test that reproduces 6.6's
brute-force-validated encounter *through the whole cascade*. Subtlety found during build — the
crossing **repeats** (~once per orbit), so the global-closest event is the first window (~29.86 min),
not the 6.6-validated one (~122.72 min). The test locates the validated window specifically rather
than asserting on `ev[0]`.

---

## Coverage matrix (audit result)

| Pipeline stage | Covered by | File |
|---|---|---|
| `propagate_batch` vs single `sgp4` | `TestPropagateBatch`, `TestCrossValidation` | test_sgp4_cpp |
| `coarse_filter` (overlap / disjoint / empty) | `TestCoarseFilter` | test_sgp4_cpp |
| `medium_filter` (crossing, co-orbital, decayed-isolated, empty) | `TestMediumFilter` | test_sgp4_cpp |
| `teme_to_rtn` (orthonormality + hand case) | `TestTemeToRtn` | test_coordinate_transforms |
| `fine_filter` (synthetic crossing vs 0.01 s brute force) | `TestFineFilter` | test_conjunctions |
| `get_all_satrecs` (index alignment) | `TestGetAllSatrecs` | test_propagator |
| Screener integration | `TestConjunctionScreener`, `TestSeededScreenIntegration`, `TestDenseShellScale` | test_conjunctions |
| Endpoint integration | `TestConjunctions` | test_api |
| Dataset / seed / downloader | `TestSliceToShell`, `TestDemoSeed`, `TestCacheOnlyGroup`, `TestDownloader` | test_gp_fetcher, test_propagator |

---

## Added (the gaps)

| Test | What it locks in |
|------|------------------|
| `test_full_pipeline_deterministic` | Full cascade reproduces the 6.6 brute-force encounter (TCA ≈122.72 min, miss ≈6.60 km) within tolerance; every event RTN-consistent. The deterministic end-to-end anchor. |
| `test_empty_catalog_returns_no_events` | 0 sats → clean `[]`, not an error. |
| `test_fine_filter_failure_is_isolated` | A window failing `fine_filter` is dropped; the rest of the screen survives, no exception escapes (`run_screen`'s error-isolation branch). |

No production code changed — pure test consolidation.

---

## Validation

- New tests pass; **full suite 377 passing, 1 skipped on two consecutive runs** (deterministic,
  offline).
- The integration anchor is tied to an *independent* reference (6.6's 0.01 s brute force), not to the
  pipeline's own output — so it catches regressions in any stage that would shift TCA/miss.

---

## Lessons learned

- **Test-first across 6.1–6.9 made 6.10 nearly a no-op** — the audit is the work; resist padding
  with duplicate tests.
- **A repeating encounter breaks "assert on the closest event."** When geometry recurs, anchor a
  deterministic test to the *specific* validated window (locate by TCA), not `ev[0]`.

---

## Phase 6 — COMPLETE (6.0–6.10)

The conjunction-screening vertical slice is done end-to-end: C++ batch SGP4 → coarse → medium →
fine → RTN, served via `GET /api/conjunctions`, visible in the browser, validated on a real ~300-sat
Starlink shell (607 natural conjunctions). Next: Phase 7 (scale + live/auto-refresh data, roadmap 7.0).
