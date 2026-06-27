# Task 8.2 — SOCRATES comparison logic (the validation core)

**Date:** Jun 24, 2026
**Status:** DONE
**Tests:** 472 passing, 3 skipped (was 458) — +14 (`test_socrates_compare.py`),
the propagator `build_satrecs_and_meta` extract kept its tests green.

---

## Goal

Prove our SGP4 screener reproduces CelesTrak SOCRATES: run *our* pipeline on the
SOCRATES-flagged objects over the same window, match events, and report — per
event and in aggregate — whether we reproduce them and how close our TCA & miss
are, with the **epoch match verified via `DSE`** and agreement **segmented by
element age**.

---

## Approach

- **Two layers.** `match_events(socrates_df, our_events)` is **pure** — pairs our
  screen output to SOCRATES rows by `{object pair}` + TCA proximity, computes per-
  event TCA/miss deltas, and a summary (reproduction rate + |Δ| stats bucketed by
  `DSE`). No network → the reusable, testable core (Phase 9's "matched SOCRATES?"
  badge reuses it). `compare_against_socrates(...)` is the **orchestrator** —
  fetch GP → build satrecs → screen → match → verify epoch.
- **5 km legacy-Euclidean, not SFS.** Matches SOCRATES's spherical screen; the SFS
  ellipsoid default would *suppress* events SOCRATES lists.
- **Epoch verified, not assumed (`DSE`).** Each event's `epoch_ok` = our element's
  age at the SOCRATES TCA equals SOCRATES's reported `DSE` (both objects) within ε.
  A gap means CelesTrak rolled the epoch since the SOCRATES run → expect drift.
- **Window from the slice's TCA range**, not "now + 7 d" — SOCRATES's window starts
  at its (past) run time.
- **Propagator seam extracted.** `build_satrecs_and_meta(df)` (+ shared
  `_row_screening_meta`) builds index-aligned satrecs/meta from a fetched GP frame
  without a propagator instance; `get_all_satrecs` delegates the meta build and
  keeps its Satrec cache.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/socrates_compare.py` | **new** — `match_events` (pure), `compare_against_socrates` (orchestrator), `_attach_epoch_ok`, `_summarize`, helpers; `max_objects` cap |
| `backend/core/propagator.py` | extracted `build_satrecs_and_meta(df)` + `_row_screening_meta(row, now)`; `get_all_satrecs` delegates meta-building |
| `tests/test_socrates_compare.py` | **new** — 14 tests (+1 skipped live) |

---

## Validation

- **Live ISS (real data):** 9 SOCRATES conjunctions → **3/9 reproduced**; matched
  events agree **TCA <5 s, miss <0.32 km** (same-method SGP4 confirmed). `epoch_ok`
  = 0 (current `gp.php` rolled the epochs SOCRATES used; their `DSE` 2.5–5.3 d) —
  misses cluster at higher `DSE`. The epoch-drift degradation, surfaced honestly.
- **Offline orchestrator wiring:** synthetic GP (real `sgp4init`) + mocked screen →
  missing objects dropped, window derived, `epoch_ok=True` end-to-end when our
  0.5 d age matches the reported `DSE` 0.5.
- **Mutation-checked:** the match window gates matching, and `epoch_ok` genuinely
  tracks `DSE` (matched→True, drifted→False) — neither is hardcoded.

---

## Test coverage

| Class | Covers |
|------|--------|
| `TestMatchEvents` (8) | match+deltas, pair-order-independent, outside-window→missed, closest-TCA-wins, extra+missed counted, empty events, DSE segmentation, stable 3-bucket shape |
| `TestOrchestrator` (3) | oversized-slice cap (pre-fetch), all-missing→RuntimeError, wiring (missing-drop + window + epoch_ok) |
| `TestHelpers` (3) | DSE bucket boundaries, magnitude stats, empty stats |
| `TestLiveCompare` (1, skipped) | opt-in live ISS comparison |

---

## Lessons learned

- **The DSE epoch-check works — and tells the honest truth.** On live data it
  reports `epoch_ok=0`: current CelesTrak GP no longer carries the epochs SOCRATES
  screened from. Despite that, where the drift is small we still reproduce TCA to
  seconds and miss to sub-km — and the reproduction *rate* degrades with `DSE`,
  which is the Phase-8 finding. Tightening it needs the `gp_history` backstop (the
  exact older snapshot) — a Phase-8.3 lever.
- **"Extra" ≠ false positive.** Screening a primary + its secondaries also finds
  secondary-vs-secondary crossings (not in a single-primary slice). Labeled "other
  crossings"; never counted against reproduction.
- **Object 1/2 positional (8.1 carry-over):** the matcher keys on a `frozenset`
  pair so object order never matters.
- **Per-object fetch is a foot-gun without a cap.** `gp.php` has no multi-id, so a
  broad slice (`by_name("STARLINK")` → thousands of objects) would fire thousands
  of sequential requests. `max_objects` (default 200) raises before any fetch.

---

## Remaining risks / deferred

- **10-min match window** could pair two same-pair encounters <10 min apart (rare
  in LEO; configurable).
- **Per-object sequential fetch** — capped at 200 objects; batching deferred.
- **`build_satrecs_and_meta` doesn't guard per-object `sgp4init` failure** —
  consistent with `get_all_satrecs`; valid GP rarely fails init.

---

## Function reference

```python
# backend/core/socrates_compare.py
match_events(socrates_df, our_events, match_window_s=600.0) -> (results, summary)
    # PURE: pair+TCA matching; per-event TCA/miss deltas; reproduction rate +
    # |Δ| stats bucketed by DSE. Reusable (Phase 9 badge).
compare_against_socrates(socrates_df, gp_fetcher, *, step_sec=30, threshold_km=5,
    match_window_s=600, epoch_eps_days=0.05, window_pad_hours=1, max_objects=200)
    -> {"results": [...], "summary": {...}, "missing_objects": [...]}
    # ORCHESTRATOR: fetch GP per object → build satrecs → 5 km screen → match →
    # epoch_ok via DSE. For small slices (by_catnr / top_n).

# backend/core/propagator.py
build_satrecs_and_meta(df) -> (satrecs, meta)   # ad-hoc GP frame → screener input
```
