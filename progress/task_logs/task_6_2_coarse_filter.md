# Task 6.2 — C++ Coarse Filter (Altitude-Band Pair Screening)

**Date:** Jun 11, 2026
**Status:** DONE
**Tests:** 309 passing, 1 skipped (was 293) — 16 new in `tests/test_sgp4_cpp.py::TestCoarseFilter`

---

## Goal

Stage 1 of the conjunction cascade: reject satellite pairs whose orbital altitude bands
`[perigee, apogee]` can never intersect, before any propagation happens. With N satellites there
are N(N−1)/2 pairs (~18M at 6,000 sats); the coarse filter cuts that down with pure interval math
so the expensive time-stepped medium filter (6.3) only sees plausible pairs.

---

## Approach

```
orbitcore.coarse_filter(periapsis_km, apoapsis_km, pad_km) -> list[(i, j)]   # i < j, row-major
```

- **Plain double arrays in, not Satrecs** — unit-agnostic, trivially testable; the 6.7 screener
  computes bands from `Satrec.alta/altp × radiusearthkm` or the parquet `apoapsis`/`periapsis`
  columns (verified consistent to ~0.5 km — different Earth-radius constants, absorbed by pad).
- **Overlap test:** `peri_a ≤ apo_b + pad && peri_b ≤ apo_a + pad`. Touching bands count; a gap
  is bridged when `gap ≤ pad_km`.
- **Naive O(N²) scan** — measured **40 ms at 6,000 sats** (scan only). Sort-and-sweep
  O(N log N + K) is the upgrade path if Phase 4 (10k+) needs it — flagged, not built.
- **Boundary validation:** length mismatch / negative pad → `ValueError`. NaN bands pair with
  nothing (IEEE comparisons false) — documented behavior, neighbors unaffected.
- **pad_km is a required arg** — no hidden default; the screener chooses (guidance: medium-filter
  threshold + SGP4 element drift allowance).

---

## Implementation

| File | Change |
|------|--------|
| `orbitcore/src/bindings.cpp` | `coarse_filter` binding (~60 lines incl. docstring) |
| `tests/test_sgp4_cpp.py` | `TestCoarseFilter` — 16 tests |

Rebuild + copy `.so` to `backend/` (gitignored artifact).

---

## Validation

- Fixture geometry: ISS↔GPS disjoint; Molniya (500–39,000 km) pairs with GPS + GEO but **not** ISS
  at pad=0 — its perigee sits 76 km above ISS apogee; pad=76 pairs them, 75.9 doesn't (exact
  boundary).
- Property check vs an independent brute-force Python implementation on 60 pseudo-random sats.
- Real Phase 1 parquet (25 stations, bands 290–2,219 km), pad=50: filters some pairs, keeps some,
  and the overlap property holds for every survivor.
- Ordering contract: all-overlap set yields exactly N(N−1)/2 row-major (i<j) pairs, no self/dups.
- Suite: 309 passed, 1 skipped, stable.

---

## Performance — measured, with an architecture consequence

| Scenario (6,000 sats) | Time | Why |
|---|---|---|
| Sparse bands (0 survivors) | **40 ms** | pure O(N²) scan — as predicted |
| Dense bands (5.4M survivors) | **~2,050 ms** | **378 ns/pair C++→Python tuple conversion dominates** |

The scan is cheap; **materializing millions of survivor pairs as Python tuples is not**. Since the
survivors feed straight back into C++ (6.3 medium filter), the round-trip C++ → 5M Python tuples →
C++ is pure waste at scale.

**Architecture note for 6.3 (decide in its plan phase):** either (a) medium filter takes the band
arrays and runs the coarse cut internally (survivors never surface to Python), or (b) accept the
one-time ~2 s materialization per screen run at Phase 3 scale. The standalone `coarse_filter`
stays regardless — right contract for testing, inspection, and Phase-1/2 scale.

**Expectation-setting:** within a *single Starlink shell* (the 6.9 dataset) nearly all pairs share
one band → ~100% survival. That's correct behavior — the coarse filter pays off across a *mixed*
catalog (LEO→GEO, Phase 7 "active"), not within one shell.

---

## Test Coverage

| Test | Covers |
|---|---|
| disjoint / co-altitude / HEO-crossing | basic geometry incl. Molniya 76 km gap subtlety |
| pad_bridges_gap | exact gap≤pad boundary (76.0 vs 75.9) |
| touching_bands | `<=` semantics |
| pair_ordering_no_self_no_dup | output contract |
| empty / single / length-mismatch / negative-pad | boundaries |
| nan_band_matches_nothing | documented IEEE behavior |
| matches_brute_force_property | independent implementation agreement |
| real_stations_catalog | live-data integration + survivor property |
| satrec_bands_consistent_with_parquet | the two band sources agree |
| scan_performance_at_phase3_scale | 6,000-sat scan < 1 s (measured ~40 ms) |

---

## Lessons Learned

- **pybind11 return-value conversion can dominate the algorithm.** 5.4M `(i,j)` pairs → ~2 s of
  tuple building vs 40 ms of actual scanning. Measure the *boundary*, not just the loop. Feeds
  directly into 6.3's design.
- The "kills the vast majority of pairs" claim is **catalog-shape-dependent** — true for mixed
  altitude populations, false within a single constellation shell. Tests and docs now say so.
- Smoke-test expectations need checking too: my first smoke comment assumed Molniya pairs with
  ISS; the filter correctly said no (76 km gap). The code was right, the human was wrong.

---

## Function Reference

### `orbitcore.coarse_filter(periapsis_km, apoapsis_km, pad_km)`
- `periapsis_km` / `apoapsis_km`: per-sat altitude bands, km, same indexing
- `pad_km`: required margin ≥ 0 (drift + medium-threshold allowance); gap ≤ pad still pairs
- Returns `list[(i, j)]`, i<j, row-major; NaN bands match nothing
- Raises `ValueError` (length mismatch, negative pad)
