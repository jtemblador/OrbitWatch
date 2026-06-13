# Task 6.7 — ConjunctionScreener + /api/conjunctions Endpoint + Schemas

**Date:** Jun 13, 2026
**Status:** DONE
**Tests:** 356 passing, 1 skipped (was 338) — 18 new (8 screener + 5 propagator seam + 5 endpoint)

---

## Goal

Wire the four existing pipeline stages (`coarse_filter` → `medium_filter` → `fine_filter` →
`teme_to_rtn`) into one orchestrator and expose it as CDM-like JSON over a new endpoint. After
this task the whole cascade runs end-to-end from a single HTTP call and is proven deterministically.

```
GET /api/conjunctions?time=&duration_hours=&threshold_km=&step_sec=
  -> { count, screening_start, duration_hours, threshold_km, events: [ConjunctionEvent] }
```

---

## Approach

- **Pure screening core (`run_screen`) + thin `ConjunctionScreener` wrapper.** The orchestration
  logic is a module-level pure function taking index-aligned `(satrecs, meta)`; the class only
  pulls those from a propagator. This made the screener **deterministically testable without a
  parquet/temp-file fixture** — tests drive `run_screen` directly with the ISS-crosser pair. Keeping
  it deterministic now (vs. wiring a live Starlink shell) was a deliberate call — scale comes in
  Phase 7/6.9.
- **Index alignment is THE contract.** `medium_filter` returns positional `(i, j)`, not NORAD IDs.
  So `satrecs`, the periapsis/apoapsis arrays, and `meta` are all built from one ordered pass in the
  new `propagator.get_all_satrecs()`, and a result index maps straight back to identity via
  `meta[i]`. A `len(satrecs) != len(meta)` guard rejects misalignment up front (a mismatch would
  silently attach the wrong satellites to a real event).
- **One threshold for both medium-gross and final report.** Safe because 6.3's velocity-aware
  no-skip bound guarantees no sub-threshold approach is dropped at that threshold; `fine_filter`
  refines and we keep events with `miss < threshold`. No threshold inflation needed.
- **`pad_km` defaults to `threshold_km`** for the coarse cut: `miss_distance ≥ radial separation`,
  so a band gap larger than the threshold can never yield a sub-threshold miss — a smaller pad could
  drop real conjunctions.
- **Boundary validation at the endpoint:** `Query` bounds (`gt`/`le`) → 422; sub-step window
  (`medium_filter` `ValueError`) caught → 422; bad time → 422; decayed sat in a fine bracket →
  caught per-row in `run_screen` and skipped (one bad sat can't kill a screen).

No C++ changes — all four compute stages already existed and were bound (6.1–6.6). No rebuild.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/propagator.py` | NEW `get_all_satrecs() -> (satrecs, meta)` — index-aligned screening seam; reuses the per-sat Satrec cache; peri/apo from Parquet columns; recomputes `epoch_age_days` |
| `backend/core/conjunctions.py` | NEW `run_screen(...)` (pure cascade, sorted by miss) + `ConjunctionScreener` wrapper; added `teme_to_rtn` import |
| `backend/models/schemas.py` | NEW `ConjunctionEvent`, `ConjunctionResponse` |
| `backend/routers/satellites.py` | NEW `GET /api/conjunctions` |
| `tests/test_propagator.py` | NEW `TestGetAllSatrecs` (5) |
| `tests/test_conjunctions.py` | NEW `TestConjunctionScreener` (8) |
| `tests/test_api.py` | NEW `TestConjunctions` (5) |
| `progress/scaling_tracker.md` | +2 Phase-7 items (#3 coarse-pair boundary round-trip, #4 sync screen in async handler) |

---

## Validation

- **Deterministic crosser fixture** (ISS + clone at MA +180.2°, RAAN +180°): screen over 6 h finds
  **8 windows**, min miss **6.59 km**, v_rel **12.0 km/s**, events sorted ascending, RTN norm ==
  miss to 1e-9. Matches the documented 6.6 fixture exactly.
- **Cross-validation inherited:** the screener reuses `fine_filter`, whose miss/TCA were validated
  against an independent 0.01 s brute force in 6.6 (within 0.05 s / 10 m). No new propagation math
  was introduced, so the screener's numbers are anchored transitively.
- **Index→identity mapping:** a decoy at index 0 (disjoint altitude, coarse-cut) shifts the real
  pair to `(1, 2)`; events still carry the correct NORAD IDs — proves the mapping isn't hardcoded
  to `(0, 1)`.
- **`get_all_satrecs` alignment:** `meta[k]` matches `df.iloc[k]` (norad/name/peri/apo);
  `satrecs[k].satnum == meta[k].norad_id`; second call returns the same cached Satrec objects.
- **Endpoint contract:** schema-valid JSON (response_model), both schemas in `/openapi.json`,
  threshold below the true miss → `count: 0` (not an error), 7 invalid-param variants → 422.
- **Perf baseline:** 25 stations, 24 h @ 60 s = **134 ms**, 82 events (warm cache).

---

## Test coverage

| Test class | File | Covers |
|-----------|------|--------|
| `TestConjunctionScreener` (8) | test_conjunctions.py | finds crosser, sorted, RTN norm == miss, threshold excludes, disjoint-band coarse cut, propagator delegation, index→identity mapping, misaligned-input guard |
| `TestGetAllSatrecs` (5) | test_propagator.py | length match, df index alignment, satnum↔norad, field presence, satrec-cache reuse |
| `TestConjunctions` (5) | test_api.py | structure + sorted + echoed params, deterministic fields/values (crosser propagator), empty→count 0, 422 paths, OpenAPI schemas |

---

## Lessons learned

- **A pure orchestration core beats a propagator-coupled one for testing.** Passing index-aligned
  `(satrecs, meta)` into a free function let the whole cascade be tested with hand-built fixtures —
  no DataFrame/temp-parquet machinery — and the deterministic-time anchor (ISS epoch) is trivial to
  set.
- **When a downstream returns positional indices, the index→identity map needs its own test.** A
  2-element fixture can't catch an off-by-one; the decoy-at-index-0 test does.
- **Live `stations` screening surfaces ~82 events dominated by docked ISS modules** (Soyuz/Dragon/
  Progress/Nauka at ~0 km, v_rel ~0). These are *real* co-located objects, not a bug — the medium
  filter's adaptive bound correctly doesn't spam co-orbital neighbors, and they're genuinely within
  threshold. Phase 7's asymmetric RTN screening volumes (+ likely a small min-miss floor) are what
  filter these out. This is exactly why `teme_to_rtn` exists.

---

## Function reference

```python
# backend/core/propagator.py
SatellitePropagator.get_all_satrecs() -> tuple[list[Satrec], list[dict]]
    # index-aligned; meta[k] = {norad_id, name, object_type, epoch_age_days,
    #                           periapsis_km, apoapsis_km}

# backend/core/conjunctions.py
run_screen(satrecs, meta, start_utc, duration_hours, threshold_km,
           step_sec=60.0, pad_km=None) -> list[dict]
    # coarse -> medium -> fine -> RTN; events sorted ascending by miss.
    # pad_km defaults to threshold_km. Raises ValueError on length mismatch.

ConjunctionScreener(propagator).screen(start_utc, duration_hours, threshold_km,
                                       step_sec=60.0, pad_km=None) -> list[dict]
```

---

## Pipeline status after 6.7

```
coarse_filter (C++) -> medium_filter (C++) -> fine_filter (Py) -> teme_to_rtn (Py)
                         \__________ ConjunctionScreener / run_screen __________/
                                              |
                                   GET /api/conjunctions
```
All stages now reachable from one HTTP call. Next: 6.8 (draw one line + event list on the globe),
6.9 (deterministic dataset wiring — the crosser fixture is the natural seed).
