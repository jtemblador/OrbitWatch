# Week 6 (Phase 6) — Conjunction Pipeline Vertical Slice (Jun 12–20, 2026)

**Goal:** Get the *entire* conjunction-screening pipeline working end-to-end on a small dense set
of satellites (~300). By the end of this phase, you can open the globe, and one real close
approach is detected by the C++ engine, refined in Python, served over a new `/api/conjunctions`
endpoint, and drawn as a line between the two satellites. Every hard layer is exercised — so when
we scale up (Phase 7) and validate (Phase 8), we're tuning a working system, not building one.

> **Why a vertical slice?** The risky parts (batch propagation, the all-pairs scan, getting the
> geometry right) all live here. Proving them on 300 sats first means surprises surface now, while
> there's time to react — and the project stays demoable the whole way.
>
> **Out of scope for Phase 6 (deferred):** Orekit cross-validation and SOCRATES matching →
> **Phase 8**. Full-catalog scale + asymmetric RTN screening volumes → **Phase 7**. No Pc, no ML.

---

## What We Have (from Weeks 0–5)

| Component | What it does | Where |
|-----------|-------------|-------|
| C++ SGP4 | `sgp4init()`, `sgp4(satrec, tsince)` → TEME (pos, vel); `Satrec` exposes `alta`/`altp`/`radiusearthkm`/`jdsatepoch` | `orbitcore/src/bindings.cpp` |
| Propagator | `SatellitePropagator` — loads OMM, caches `Satrec` objects per sat, `get_position()` / `get_all_positions()` | `backend/core/propagator.py` |
| Transforms | `gmst_from_jd()`, `teme_to_ecef()`, `teme_to_geodetic()`, `utc_to_jd()` | `backend/core/coordinate_transforms.py` |
| API | 6 endpoints, Pydantic-validated responses | `backend/routers/satellites.py`, `backend/models/schemas.py` |
| Frontend | Cesium globe, points, info panel, orbit trails, time controls | `frontend/js/*.js` |
| Data fetcher | CelesTrak OMM → Parquet, supports `stations`/`visual`/`starlink`/`active` groups | `backend/core/tle_fetcher.py` |

**Key facts to reuse:** `Satrec.alta`/`altp` are apogee/perigee **in Earth radii** (multiply by
`radiusearthkm` for km altitude). `Satrec.jdsatepoch`/`jdsatepochF` give each sat's epoch — needed
because two satellites have *different* epochs, so the same UTC maps to a different `tsince` for each.

---

## Background: The Filter Cascade

With N satellites there are N·(N−1)/2 pairs (~435 for 30 sats, ~45,000 for 300, ~18M for 6,000).
Checking every pair at every second is too expensive, so we cascade cheap→expensive filters:

1. **Coarse (C++)** — reject pairs whose perigee/apogee altitude bands don't overlap. No propagation.
2. **Medium (C++)** — propagate survivors at coarse time steps (~60 s over 24–72 h), flag windows where TEME distance dips below a gross threshold.
3. **Fine (Python)** — `scipy.optimize.minimize_scalar` inside each flagged window for exact TCA + miss distance.
4. **RTN (Python)** — express the miss vector in Radial/In-Track/Cross-Track, the industry frame.

---

## Main Tasks

### 0. (6.0) Test Hygiene — Mock the Refresh Fetcher  *(do first — quick win)*

Before building, fix a pre-existing test-hygiene issue so the Phase 6 dev loop is fast and safe.
`TestDataRefresh` in `test_api.py` currently calls `POST /api/refresh` **for real** — it hits live
CelesTrak and overwrites `backend/data/tle/stations.parquet`. That makes the suite
network-dependent, non-deterministic (live data varies run-to-run), and mutates real project data.
During Phase 6 we'll run the suite constantly; each run with a >2 h-stale cache triggers a live
fetch — risking CelesTrak rate-limiting / IP-block (their 100 MB/day cap, no-retry policy), which
would also break the real app's data fetching.

**What to build:**
- Mock the fetch in `TestDataRefresh` (patch `GPFetcher.fetch_and_save` / the refresh code path) so
  it returns a small fixed sample instead of going to the network — no real-file writes. Mirror the
  temp-dir + mock pattern already used in `test_gp_fetcher.py`.
- Optionally keep **one** real end-to-end refresh test, gated/skipped by default (e.g.
  `@unittest.skipUnless(os.getenv("RUN_NETWORK_TESTS"), ...)`), so the live fetch can still be
  exercised deliberately — not on every run. (Standard "test pyramid": fast isolated unit tests by
  default, a few explicit integration tests run on purpose.)

**Success criteria:**
- [x] Full suite runs with **no network access** and does **not** modify `stations.parquet` (hash identical before/after)
- [x] `TestRefresh` is deterministic across repeated runs (279 passed, 1 skipped × 3 runs)
- [x] Refresh behavior still covered (status `fetched`/`rate_limited` paths, counts, schema, fetch_time)

**Actual:** `TestRefresh` already had a sibling `TestRefreshMocked` (covers the "fetched"/502 paths).
Added a module-level `_offline_fetch_patch()` helper + a `setUp` on `TestRefresh` that patches
`fetcher.fetch` to return cached data unchanged → deterministic "rate_limited", no network, no
parquet write. Fixed one stray unmocked call in `test_refresh_matches_model`. Added opt-in
`TestRefreshLive` (gated by `RUN_NETWORK_TESTS`) so the real fetch can still be exercised on purpose.
Test-only change — no production code touched.

**Review + test pass:** added `test_refresh_makes_no_network_call` — asserts `_download` is never
reached during a refresh, enforcing the offline invariant at the network boundary (catches a future
regression that removes the mock whenever the cache is stale). Verified: 280 passed, 1 skipped;
`stations.parquet` md5 byte-identical before/after a full run. Left pre-existing redundant local
`patch` imports in `TestRefreshMocked` untouched (outside 6.0 scope, harmless).

---

### 1. (6.1) C++ Batch SGP4

Propagate many satellites to a single time in one C++ call, instead of a Python loop calling
`sgp4()` once per satellite (the current bottleneck).

**What to build:**
- New C++ function `propagate_batch(satrecs, tsince_list)` in `bindings.cpp` — takes a list of
  `Satrec` + a parallel list of `tsince` values (one per sat, since epochs differ), returns a list
  of `((x,y,z),(vx,vy,vz))` in TEME. On a per-sat propagation error, return a sentinel (e.g. `None`)
  rather than throwing, so one bad sat doesn't kill the batch.
- Rebuild the `.so` (`cmake --build orbitcore/build`) and copy to `backend/`.

**Success criteria:**
- [x] `propagate_batch` returns the same positions as N individual `sgp4()` calls (bit-identical, `==` with no tolerance; also sub-meter vs python-sgp4)
- [x] Batch vs Python loop measured: **only ~1.05×** (2.08 vs 2.20 ms / 1,000 props) — `sgp4()` compute dominates and the batch still builds Python tuples per sat. Honest finding: the order-of-magnitude perf win belongs to 6.3's all-C++ medium-filter loop, not the Python-facing batch. Test asserts "not slower" + records the ratio (avoids a flaky 5%-margin timing race).
- [x] A decayed/error sat yields `None` at its index, neighbors unaffected; failed satrec reusable afterward (`sgp4()` clears `error` per call — SGP4.cpp:1779)

**Actual:** `propagate_batch(satrecs, tsince_list)` in `bindings.cpp` (~60 lines), items cast by
reference (caller's Satrecs mutate like the single-call binding; `std::vector<elsetrec>` rejected —
would copy ~10 KB/sat and diverge semantics). Length mismatch → `ValueError`; non-Satrec item →
indexed `TypeError` — review phase caught a **segfault** (pybind11 casts `None`→`nullptr` on pointer
casts without throwing); now nullptr-checked + regression-tested. 13 new tests (293 total).
`get_all_positions()` wiring deferred to Phase 7 by design. GIL release / NumPy output deferred
until profiling demands.

---

### 2. (6.2) C++ Coarse Filter (Altitude-Band Overlap)

Throw out pairs that can *never* get close because their orbits don't share an altitude band.
This is the cheap filter that kills the vast majority of the N² pairs before any propagation.

**What to build:**
- C++ function `coarse_filter(periapsis_km[], apoapsis_km[], pad_km)` → list of `(i, j)` index
  pairs whose `[perigee, apogee]` altitude bands overlap (with a `pad_km` margin). Pure number
  comparison, no propagation.
- Python side computes the perigee/apogee km arrays from each `Satrec` (`altp * radiusearthkm`,
  `alta * radiusearthkm`).

**Success criteria:**
- [ ] Two satellites with disjoint altitude bands (e.g. ISS @420 km vs a GEO sat) are *not* paired
- [ ] Two co-altitude satellites *are* paired
- [ ] On the ~300-sat set, the surviving pair count is a small fraction of N(N−1)/2 (log the %)

---

### 3. (6.3) C++ Medium Filter (Time-Stepped Distance)

For the pairs that survive coarse filtering, step through the screening window and flag the time
windows where the two satellites actually come close. This is the **O(pairs × timesteps) hot loop**
— it belongs in C++.

**What to build:**
- C++ function `medium_filter(satrecs, pairs, jd_start, jd_end, step_sec, threshold_km)` →
  list of `(i, j, tsince_i_at_flag, distance_km)`. Internally: for each timestep, propagate both
  members of each pair (converting the absolute time to each sat's own `tsince` via its epoch),
  compute the TEME distance, and record the step where distance dips below `threshold_km`.
- Use a **generous** gross threshold here (e.g. 50 km) — this stage only needs to *bracket* close
  approaches for the fine filter, not pinpoint them.

**Success criteria:**
- [ ] For a known close pair, the flagged window brackets the true closest moment
- [ ] Distance is computed in TEME (both sats at the same absolute time)
- [ ] Step size + threshold are chosen so no sub-threshold approach is skipped between steps (see Things to Watch)

---

### 4. (6.4) pybind11 Bindings + Rebuild

Expose the three new C++ functions to Python.

**What to build:**
- Add `propagate_batch`, `coarse_filter`, `medium_filter` to the `PYBIND11_MODULE` block with arg
  names + docstrings, matching the existing style.
- Rebuild and verify import from `backend/`.

**Success criteria:**
- [ ] `import orbitcore; orbitcore.coarse_filter`, `.medium_filter`, `.propagate_batch` all callable
- [ ] Docstrings present (`help(orbitcore.medium_filter)`)

---

### 5. (6.5) RTN Coordinate Transform

Convert a pair's relative position into the Radial / In-Track / Cross-Track frame — the frame every
real conjunction report uses. Small code, large credibility payoff.

**What to build:**
- `teme_to_rtn(r_primary, v_primary, r_secondary)` in `coordinate_transforms.py`. Build the frame
  from the primary's state: `R̂ = r/|r|`, `N̂ = (r×v)/|r×v|` (cross-track), `T̂ = N̂ × R̂` (in-track).
  Project the relative vector `(r_secondary − r_primary)` onto `[R̂, T̂, N̂]` → `(R, T, N)` in km.

**Success criteria:**
- [ ] `R² + T² + N²` equals the raw miss distance squared (frame is orthonormal — sanity check)
- [ ] A purely-radial offset maps to R only; an along-velocity offset maps to T only
- [ ] Unit test against a hand-computed example

---

### 6. (6.6) Python Fine Filter (Exact TCA + Miss Distance)

Inside each flagged window, find the exact Time of Closest Approach and minimum miss distance.
**Stays in Python by design** — it runs only on the handful of survivors, and each distance
evaluation already calls the C++ propagator, so the only Python overhead is the optimizer's control
loop (~10–50 iterations). C++-where-it's-hot, Python-where-it's-not.

**What to build:**
- `fine_filter(satrec_i, satrec_j, t_lo, t_hi)` using `scipy.optimize.minimize_scalar` (bounded,
  Brent) over a distance-vs-time function that propagates both sats (via C++ `sgp4`) at a candidate
  time. Bracket `[t_lo, t_hi]` = the flagged step ± one step. Returns TCA (UTC), miss distance (km),
  relative speed (km/s), and both TEME states at TCA (for the RTN transform).

**Success criteria:**
- [ ] Refined miss distance ≤ the medium-filter flagged distance (it's a minimum)
- [ ] TCA falls inside the bracket
- [ ] For a synthetic crossing pair, recovered TCA matches the analytic answer within seconds

---

### 7. (6.7) `/api/conjunctions` Endpoint + Schemas

Expose the pipeline output as CDM-like JSON.

**What to build:**
- Pydantic `ConjunctionEvent` (sat A + B metadata, TCA, miss_distance_km, relative_speed_km_s,
  RTN components `r_km`/`t_km`/`n_km`) and `ConjunctionResponse` (count, screening window, list of
  events) in `schemas.py`.
- `GET /api/conjunctions?duration_hours=&threshold_km=&time=` in `satellites.py` — runs
  coarse → medium → fine → RTN over the loaded catalog and returns sorted-by-miss-distance events.
- A `ConjunctionScreener` orchestrator (new `backend/core/conjunctions.py`) tying the stages
  together, so the router stays thin.

**Success criteria:**
- [ ] `GET /api/conjunctions` returns valid JSON matching the schema (visible in `/docs`)
- [ ] Each event includes RTN components and both satellites' names/NORAD IDs
- [ ] Invalid params → 422; empty result → `count: 0`, not an error

---

### 8. (6.8) Minimal Globe Visualization

Prove the data reaches the frontend — full polish (alert table, fly-to, detail panel) comes in
Phase 9.

**What to build:**
- Fetch `/api/conjunctions` once; draw a polyline between the two satellites of the top event at
  its TCA position; a bare text list of events in a corner.
- Keep it crude — a single line + list is enough to confirm the end-to-end chain.

**Success criteria:**
- [ ] At least one conjunction line is visible on the globe
- [ ] The event list shows the pair names + miss distance + TCA
- [ ] No console errors

---

### 9. (6.9) Dataset Wiring

Give the pipeline a population dense enough to actually have a close approach.

**What to build:**
- Load a ~300-sat dense subset — e.g. one Starlink shell via the existing `tle_fetcher` `starlink`
  group, sliced down. (Stations are too sparse — real conjunctions are rare among ~30 objects.)
- If no natural sub-threshold approach appears in the screening window, add a **known/synthetic
  close pair** (two TLEs engineered to pass near each other) purely to prove the plumbing; keep it
  as a deterministic test fixture afterward.

**Success criteria:**
- [ ] The screener runs on the ~300-sat set without errors
- [ ] At least one conjunction (natural or the seeded test pair) flows through the full pipeline

---

### 10. (6.10) Tests

**What to build:**
- Unit tests: `coarse_filter` (overlap/no-overlap), `teme_to_rtn` (orthonormality + hand example),
  `fine_filter` (synthetic crossing), `medium_filter` (brackets a known approach).
- Integration test: feed a fixed 2–3 sat fixture (incl. the seeded close pair) through
  `ConjunctionScreener` and assert one event with expected TCA/miss distance (tolerance-bounded).
- Cross-check `propagate_batch` vs individual `sgp4` calls.

**Success criteria:**
- [ ] New tests pass; existing 279 still pass
- [ ] Pipeline integration test is deterministic (fixed TLEs + fixed screening window)

---

## File Structure (Planned Changes)

```
orbitcore/src/
└── bindings.cpp          MODIFY — add propagate_batch, coarse_filter, medium_filter

backend/core/
├── conjunctions.py       NEW    — ConjunctionScreener (orchestrates coarse→medium→fine→RTN)
├── coordinate_transforms.py  MODIFY — add teme_to_rtn()
└── propagator.py         MODIFY — expose cached Satrecs + per-sat epoch for batch/screening

backend/models/
└── schemas.py            MODIFY — add ConjunctionEvent, ConjunctionResponse

backend/routers/
└── satellites.py         MODIFY — add GET /api/conjunctions

frontend/js/
└── conjunctions.js       NEW    — fetch + draw one line + minimal event list

tests/
├── test_conjunctions.py  NEW    — coarse/medium/fine/RTN + integration
├── test_sgp4_cpp.py      MODIFY — propagate_batch cross-check
└── test_api.py           MODIFY — mock refresh fetcher (6.0), add /api/conjunctions tests
```

> **Note:** Unlike the old plan, the C++ filters live directly in `bindings.cpp` (small functions),
> not a separate `conjunction.cpp`/`conjunction.h` + CMake change. Revisit if they grow large.

---

## Implementation Order

0. **6.0 mock refresh fetcher** — quick test-hygiene win; do first so the rest of the phase runs fast/offline
1. **6.1 batch SGP4** — foundation everything rides on; verify against existing `sgp4()`
2. **6.5 RTN transform** — pure Python, independently testable, no deps on the C++ work
3. **6.2 coarse filter** → **6.3 medium filter** → **6.4 bindings** — the C++ screening core
4. **6.6 fine filter** — Python refinement on medium-filter output
5. **6.7 API + orchestrator** — wire stages into `ConjunctionScreener` + endpoint
6. **6.9 dataset** + **6.8 viz** — feed real data, see it on the globe
7. **6.10 tests** — backfill alongside each stage, integration test last

---

## Things to Watch

| Concern | Detail |
|---------|--------|
| **Per-sat epoch** | `tsince` is minutes from *that sat's* epoch. Two sats have different epochs → to evaluate both at the same UTC, compute `tsince_k = (jd_target − jdsatepoch_k) × 1440` per satellite. Getting this wrong silently corrupts every distance. |
| **`alta`/`altp` units** | These are in **Earth radii** (altitude above surface). Multiply by `radiusearthkm` for km. |
| **Step size vs miss** | Relative speeds reach ~14 km/s; a 60 s step moves objects ~840 km. If the step is too coarse relative to the threshold, a real close approach can pass *between* steps undetected. Keep the gross threshold generous (≥ step-distance/2) or shrink the step. Document the choice. |
| **TEME is fine for screening** | Both sats are in the same inertial frame at the same time → distance is correct in TEME. No ECEF needed for the miss vector. RTN is built from TEME `r`,`v`. |
| **Fine-filter bracket** | `minimize_scalar` needs a bounded bracket. Use the flagged step ± one step; verify the minimum isn't at a boundary (if it is, widen). |
| **Error satrecs** | Decayed/invalid sats throw in `sgp4()`. Batch + screening must skip them gracefully. |
| **opsmode** | Keep `'a'` (AFSPC) to match existing propagation; don't mix modes. |
| **Don't over-build** | Phase 6 uses a single gross threshold and a small set. Asymmetric screening volumes (RTN boxes) and full-catalog scale are **Phase 7** — resist pulling them forward. |

---

## Success Criteria (Definition of Done)

- [ ] C++ `propagate_batch`, `coarse_filter`, `medium_filter` built, bound, and callable from Python
- [ ] `teme_to_rtn()` implemented and unit-tested (orthonormality holds)
- [ ] Python fine filter recovers exact TCA + miss distance within a flagged window
- [ ] `GET /api/conjunctions` returns schema-valid CDM-like JSON with RTN components
- [ ] At least one conjunction detected end-to-end on the ~300-sat set and drawn on the globe
- [ ] New tests pass; existing 279 still pass
- [ ] No console errors; pipeline integration test is deterministic
- [ ] Ready for Phase 7 (scale to dense catalog + asymmetric screening volumes)
