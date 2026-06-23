# Task 7.2 — Ellipsoidal screening volumes + co-located suppression

**Date:** Jun 22, 2026
**Status:** DONE
**Tests:** 409 passing, 1 skipped (was 386) — +23 (screening-volume math, SFS path, suppression, de-dupe, endpoint, screener seam, scale)

---

## Goal

Replace the single Euclidean miss threshold with the industry **per-regime RTN
screening volumes** (SFS Handbook, 18/19 SDS), suppress **co-located /
persistent-proximity** pairs (docked modules, parked formations) that sit inside
the volume without crossing, and **de-dupe to unique pairs** — so the screen
produces a realistic, industry-shaped close-approach list instead of the
docked-module-dominated, encounter-duplicated one.

---

## Approach

- **The volume is an ELLIPSOID, not a box** (caught on a fresh re-read of the SFS
  Handbook, p.10 — "ellipsoid and covariance screenings"). Report cut on the
  fine-stage RTN miss vector: `(r/R)² + (t/T)² + (n/N)² ≤ 1`. Semi-axes from
  HAC Table 3 by **perigee**, gated on **ecc < 0.25**; our data is **LEO 1**
  (R/T/N = 0.4 / 44 / 51 km). Radial is tight (well-determined), along/cross-track
  loose (timing-dominated) — *why a single Euclidean threshold is wrong*.
- **No-skip invariant:** the medium-filter gross threshold rises to the **largest
  semi-axis** (51 km for LEO 1), not the box-corner √(R²+T²+N²) ≈ 67 km — any
  point inside an ellipsoid has Euclidean norm ≤ its largest semi-axis.
- **Co-located suppression mirrors 19 SDS**, which itself skips Pc when the
  *"relative speed is too small"* (user-settable — Annex A). **Conservative-drop:**
  suppress when `v_rel < 0.5 km/s AND (miss < 0.05 km OR shared YYYY-DDD launch
  designator)` — drops docked modules + parked formations, never an unrelated slow
  approach.
- **De-dupe to unique pairs** (closest approach per unordered pair) so `count` =
  at-risk pairs, not window-crossings.
- **Backward compatible:** `run_screen`/`screen` keep a legacy Euclidean
  `threshold_km` path (no suppression/de-dupe) for the API override and the 18
  existing tests; the SFS path is the new default.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/screening_volumes.py` *(new)* | `ScreeningVolume` (R/T/N + `.contains()` ellipsoid test + `.circumscribing_radius()`), SFS Table 3 (`LEO_1..4`, `DEEP_SPACE`), `regime_for(perigee, ecc, period)`, `gross_threshold_km()` |
| `backend/core/conjunctions.py` | `run_screen(volumes=…, suppress=)` SFS path: gross = max semi-axis, per-pair ellipsoid cut, `_is_co_located` (Conservative-drop), `_dedupe_to_unique_pairs`, `_launch_id`. `ConjunctionScreener.screen` builds volumes from meta + threads `n_suppressed`. Legacy scalar path untouched |
| `backend/core/propagator.py` | `get_all_satrecs` meta += `object_id`, `eccentricity`, `period_min` |
| `backend/models/schemas.py` | `ConjunctionEvent` += `screening_regime`; `ConjunctionResponse` += `suppressed_count`, `threshold_km` → optional |
| `backend/routers/satellites.py` | `/api/conjunctions` defaults to SFS volumes; `threshold_km` optional override; surfaces `suppressed_count` |
| `frontend/js/conjunctions.js` | per-pair count + "N co-located hidden"; dropped the client-side threshold + min-miss floor (server does it now) |

---

## Validation (real data — the SFS model behaves exactly as intended)

- **Starlink shell (300):** **7 unique-pair conjunctions**, every one radially
  tight (r ≈ ±0.3 km, all within the 0.4 km semi-axis) with large in-track
  (t ≈ 26 km). These are real co-altitude crossings at **26–36 km Euclidean miss
  that a 25 km Euclidean cut misses entirely** — the asymmetric volume catching
  what matters and ignoring the radially-distant.
- **Stations (live, docked ISS):** legacy 50 km Euclidean = **57 events**
  (docked-module noise) → SFS = **0 events, 46 suppressed**. The docked-module
  problem is solved.
- **The synthetic crosser is correctly excluded** — its 6.6 km miss is ~3.6 km
  *radial*, and `(3.6/0.4)² = 81 ≫ 1`. A 3.6 km altitude gap isn't a collision
  risk; the volume says so.

## Test coverage

| Test (file) | Covers |
|------|--------|
| `TestRegimeFor` (test_screening_volumes) | perigee boundaries, ecc gate, deep-space, MEO/HEO fallback |
| `TestEllipsoidMembership` | `contains()` on-axis boundaries, radial-vs-in-track asymmetry, interior |
| `TestCircumscribingRadius` | max semi-axis (not box corner), catalog gross, numerical no-skip |
| `TestScreeningVolumesPath` (test_conjunctions) | requires threshold-or-volumes, length guard, **ellipsoid excludes radial crosser**, generous-volume include + de-dupe + regime tag, de-dupe min-miss, co-located + same-launch suppression, `suppress=False`, **screener SFS seam** (×2) |
| `TestDenseShellScale` | SFS default runs at 300-sat scale, de-duped, regime-tagged |
| `TestConjunctions` (test_api) | SFS default surfaces `threshold_km=null` + `suppressed_count`, excludes radial crosser; legacy override still flags it |
| `TestGetAllSatrecs` (test_propagator) | meta carries `object_id`/`eccentricity`/`period_min`, typed |

---

## Lessons learned

- **Ellipsoid, not box — and the re-read caught it.** The original plan (and my
  first draft) used a box; the handbook says ellipsoid. That changed the report
  cut and dropped the gross threshold from 67 → 51 km. Re-reading the primary
  source before building paid off.
- **The SFS volume is SELECTIVE, and that's the point.** The 0.4 km radial axis
  is much tighter than a Euclidean threshold — it finds *different* (radially
  tight) conjunctions, often **fewer** of them, including some at 26–36 km
  Euclidean miss that a distance cut would skip. "Close" means close *radially*,
  not in raw range.
- **The demo narrative shifts to real data.** Under the SFS default the `stations`
  demo shows 0 conjunctions (correct — docked clusters, no real crossings) and the
  synthetic crosser is excluded. The compelling demo is now a **real Starlink
  shell** (7 genuine conjunctions) — better for the portfolio. The `?threshold_km=`
  override still gives the legacy view; the seed is removed in 9.6.
- **Suppression has operational precedent.** 19 SDS skips Pc on "relative speed
  too small"; our v_rel floor mirrors that — not an ad-hoc hack.

---

## Remaining risks / deferred

- **Coarse pad stayed at the circumscribing radius** (51 km, provably no-skip).
  The tighter *radial* pad (≈ R + drift) is a **7.3** perf item — it needs a
  measured SGP4 radial-drift bound, which is perf-pass work. 7.2's net perf change
  is negligible (gross 50→51).
- **`V_FLOOR` / `MIN_MISS_FLOOR` are tunable** module constants (0.5 km/s, 0.05 km)
  — dataset-dependent; chosen to keep the real Starlink 0.34 km @ 1.26 km/s event
  and drop docked (~0).
- **MEO/HEO regimes are out of scope** — `regime_for` defaults them to LEO 1
  (documented as a pragmatic, non-conservative fallback). Our catalogs are LEO.

---

## Function reference

```python
# screening_volumes.py
regime_for(perigee_km, eccentricity, period_min) -> ScreeningVolume   # SFS Table 3
ScreeningVolume(name, r_km, t_km, n_km).contains(r, t, n) -> bool      # ellipsoid
ScreeningVolume(...).circumscribing_radius() -> float                  # = max semi-axis
gross_threshold_km(volumes) -> float                                   # catalog no-skip gross

# conjunctions.py — SFS path is the default when volumes are given / threshold_km is None
run_screen(satrecs, meta, start_utc, duration_hours, threshold_km=None,
           step_sec=60.0, pad_km=None, timings=None, volumes=None, suppress=True)
ConjunctionScreener(prop).screen(start_utc, duration_hours, threshold_km=None,
           step_sec=60.0, pad_km=None, timings=None, suppress=True)

# GET /api/conjunctions   (no threshold_km -> SFS ellipsoids; a value -> legacy Euclidean)
#   -> { count, suppressed_count, threshold_km|null, events:[…, screening_regime], … }
```
