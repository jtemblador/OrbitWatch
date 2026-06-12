# Task 6.6 — Python Fine Filter (Exact TCA + Miss Distance)

**Date:** Jun 11, 2026
**Status:** DONE
**Tests:** 338 passing, 1 skipped (was 329) — 9 new in `tests/test_conjunctions.py` (new file)

---

## Goal

Stage 3 of the cascade: inside each time window the C++ medium filter flagged, find the **exact**
Time of Closest Approach (TCA) and minimum miss distance. New module `backend/core/conjunctions.py`
(the 6.7 screener will grow here) with:

```python
fine_filter(satrec_a, satrec_b, jd_lo, jd_hi) -> dict
# jd_tca, tca_utc, miss_km, rel_speed_km_s, pos/vel TEME states for both sats
```

Returned states feed `teme_to_rtn()` directly (verified by integration test).

---

## Approach

- **`scipy.optimize.minimize_scalar(method="bounded")`** over a distance-vs-time objective. Each
  evaluation propagates both satellites through C++ `orbitcore.sgp4` — Python only chooses which
  times to try (~10–50 evaluations per window). C++-where-hot, Python-where-not, as planned.
- **Optimize over minutes-from-bracket-start, not raw Julian date.** A JD is ~2.46e6 where
  absolute tolerances and float spacing behave badly; the small minutes variable is
  well-conditioned. Tolerance 1e-5 min (0.6 ms) — JD float spacing (~47 µs) stays below it.
- **Edge-widening policy:** if the minimum lands within 1e-3 min of a bracket edge, widen that
  side once by the bracket width and re-run; still-on-edge after that → best-effort result,
  documented. Max two optimizer runs, no loop risk.
- **Failure isolation:** propagation failure inside the objective → `inf` (optimizer steers
  away); all-infinite bracket → `RuntimeError` with a clear message.
- **`tca_utc` via `invjday` + timedelta** — routing minutes+seconds through `timedelta` handles
  the `sec == 60.0` minute-rollover case `invjday` can produce.

---

## Validation

- **Ground truth at 0.01 s resolution** (brute force via `propagate_batch`, independent of the
  optimizer): TCA matches within 0.05 s, miss within 10 m, on the fast-crosser fixture.
- **The key insight test documents:** a 1 s sampling grid is NOT adequate truth — at 12 km/s
  closing speed it overshoots the true miss by several km. Measured: grid said 8.14 km, true
  continuous minimum is **6.60 km** (8.14² ≈ 6.60² + (12·0.4 s)² — geometry checks out). The
  optimizer beating every sampled grid is exactly why the fine filter exists.
- Week-plan criteria: refined miss ≤ medium-filter flagged distance (199.6 km sampled → 6.60 km
  refined); TCA inside bracket.
- Cross-module integration: RTN miss-vector norm == miss_km to 1e-9.
- `tca_utc` round-trips to `jd_tca` via the independent `utc_to_jd` within 1 ms.
- Edge bracket (placed entirely after the encounter) → widen recovers the true TCA outside the
  original bracket.
- Identical pair (flat, zero objective) → 0.0 cleanly; unpropagatable bracket → `RuntimeError`;
  reversed bracket → `ValueError`.

---

## Files

| File | Change |
|------|--------|
| `backend/core/conjunctions.py` | NEW — module docstring + `fine_filter` (~130 lines) |
| `tests/test_conjunctions.py` | NEW — 9 tests (8th test file) |

---

## Lessons Learned

- **Sampled grids systematically overstate miss distance for fast crossers** — d(t) ≈
  √(d_min² + (v_rel·Δt)²) means even 1 s of grid offset adds km-scale error at crossing speeds.
  Validation data (e.g. SOCRATES comparisons in Phase 8) must compare against *refined* minima,
  not sampled ones.
- **Optimize small variables, not big ones:** minutes-from-window-start instead of raw Julian
  dates — tolerance semantics stay sane and float spacing is a non-issue.
- `invjday` can return `sec = 60.0` at minute rollovers; `timedelta` arithmetic absorbs all carry
  cases for free.

---

## Pipeline status after 6.6

```
coarse_filter (C++) → medium_filter (C++) → fine_filter (Python) → teme_to_rtn (Python)
        done                done                  done                   done
```
All four computational stages exist and interoperate (verified by the RTN integration test).
Next: 6.7 wires them into a `ConjunctionScreener` + `/api/conjunctions` endpoint.
