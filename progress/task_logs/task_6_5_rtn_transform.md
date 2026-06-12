# Task 6.5 — RTN Coordinate Transform

**Date:** Jun 11, 2026
**Status:** DONE
**Tests:** 329 passing, 1 skipped (was 321) — 8 new in `tests/test_coordinate_transforms.py::TestTemeToRtn` (+1 fix in `test_api.py`)

---

## Goal

`teme_to_rtn(pos_primary, vel_primary, pos_secondary)` → `(r_km, t_km, n_km)`: the secondary's
position relative to the primary in the primary's **Radial / In-Track / Cross-Track** frame. This
is the frame every operational conjunction report (CDM) uses — "0.2 km radial, 40 km in-track"
describes encounter geometry where raw XYZ doesn't — and it's the basis for Phase 7.2's
asymmetric screening volumes. ~20 lines of math, large credibility payoff.

---

## Approach

**Frame (Vallado's RSW, identical to CDM RTN), right-handed `R̂×T̂=N̂`:**
- `R̂ = r/|r|` — radial, outward
- `N̂ = (r×v)/|r×v|` — cross-track, along orbital angular momentum
- `T̂ = N̂×R̂` — in-track (~velocity direction; exactly v̂ only for circular orbits)

Project `(secondary − primary)` onto each axis. Pure `math` module, hand-rolled cross/dot —
consistent with the file's existing style (zero numpy in production; considered `_cross`/`_dot`
helpers, rejected to match `teme_to_ecef`'s inline idiom).

**Boundary validation:** `|r| ≈ 0` or `r ∥ v` (degenerate frame, impossible for a real orbiting
body) → `ValueError`.

**Scope decision (YAGNI):** position components only. CDMs also carry RTN *relative velocity*,
but 6.7's schema needs position RTN + scalar relative speed. Extension path: project Δv onto the
same basis (3 more dot products) if Phase 7+ wants it.

---

## Validation

- **Exact hand case:** `r=(7000,0,0), v=(0,7.5,0)` makes the basis the coordinate axes — offset
  `(1,2,3)` → RTN exactly `(1.0, 2.0, 3.0)` (1e-12).
- **Orthonormality invariant:** `R²+T²+N² = |Δr|²` on skewed synthetic states and real SGP4 states.
- **Geometry semantics:** pure-radial offset → R only; along-v̂ offset → T-dominant; real ISS vs
  MA-offset clone → |T| > 0.9·miss and > 10× |R|,|N|; retrograde velocity flips N's sign, R unchanged.
- **Independent cross-check:** from-scratch numpy implementation, 50 pseudo-random states, 1e-9
  agreement.
- Degenerate inputs raise.

---

## Bonus catch — a 6.0 escape (the real finding of this cycle)

The full suite suddenly took **27 s**: `TestRefreshMocked::test_rate_limited_skips_reload` only
mocked `reload_data`, **not** `fetcher.fetch` — the one refresh call site 6.0 missed. The moment
the session crossed the 2-hour cache-staleness line, that test started hitting live CelesTrak
(24 s network hang; the fetcher's cache-fallback meant no data was actually rewritten). Fixed by
wrapping it in `_offline_fetch_patch()`; assertion strengthened from `call_count <= 1` to
`assert_not_called()` (both calls now deterministically rate-limited). Suite: 27 s → **2.6 s**.

**Lesson:** "mocked the class" ≠ "mocked every call site." Time-dependent test behavior (cache
TTL crossings) can hide network dependencies for hours — the suite was offline-clean for exactly
as long as the cache was fresh. Also validating: the live-data churn this caused broke nothing,
because the catalog-coupled tests were hardened in the Week-6 prep session.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/coordinate_transforms.py` | `teme_to_rtn()` (~70 lines with docstring) |
| `tests/test_coordinate_transforms.py` | `TestTemeToRtn` — 8 tests |
| `tests/test_api.py` | 6.0-escape fix in `test_rate_limited_skips_reload` |

---

## Function Reference

### `teme_to_rtn(pos_primary, vel_primary, pos_secondary) -> (r_km, t_km, n_km)`
Both states TEME, same instant. Orthonormal ⇒ `r²+t²+n² = |Δr|²`. Raises `ValueError` on
degenerate primary state. Convention: Vallado RSW / CDM RTN, right-handed `R̂×T̂=N̂`.
