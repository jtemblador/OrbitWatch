# Week 6 — Conjunction Detection in C++ (May 1–7, 2026)

**Goal:** Detect close approaches (conjunctions) between satellites by scanning all pairs with a fast C++ coarse+medium filter, refining with Python's `scipy.optimize`, cross-validating against Orekit, and serving results through a new `/api/conjunctions` endpoint. By the end of this week, the backend can tell you which satellites will come dangerously close to each other over the next 24–72 hours.

---

## What We Have (from Weeks 2–5)

| Component | What it does |
|-----------|-------------|
| C++ SGP4 engine | `orbitcore.sgp4init()` + `orbitcore.sgp4()` — propagates any satellite to any time, returns TEME (x,y,z) + velocity |
| Satrec caching | `SatellitePropagator._satrec_cache` — initialized Satrec objects reused across calls, no re-init per timestep |
| Coordinate transforms | `teme_to_ecef()` (GMST rotation), `ecef_to_geodetic()` (SPICE), `gmst_from_jd()` — full TEME→ECEF→geodetic pipeline |
| Batch propagation | `get_all_positions()` — propagates all ~30 satellites at once (Python loop over C++ SGP4) |
| API endpoints | 5 existing endpoints, all with optional `?time=` support, Pydantic response models |
| Test framework | 279 tests across 7 files, `unittest.TestCase` pattern |
| Phase 1 data | ~30 stations (ISS, Tiangong, etc.) with cached OMM data |

---

## Background: What Is Conjunction Detection?

Two satellites are on a **conjunction** (close approach) when they pass within some distance threshold of each other. The core problem: with N satellites, there are N*(N-1)/2 pairs to check (~435 pairs for 30 sats, ~11,175 for 150 sats, ~18 million for 6,000). Checking every pair at every second is too expensive, so we use a **filter cascade**:

1. **Coarse filter (C++)** — fast rejection of pairs that can't possibly meet. Uses orbital geometry (apogee/perigee altitude bands). Eliminates >90% of pairs without propagating.
2. **Medium filter (C++)** — propagate surviving pairs at coarse time steps (e.g., every 60s over 24h). Flag pairs whose minimum distance drops below a threshold (e.g., 50 km).
3. **Fine filter (Python)** — for flagged pairs, use `scipy.optimize.minimize_scalar` to find the exact time of closest approach (TCA) and precise miss distance.
4. **Validation (Orekit)** — cross-check fine-filter results against ESA/CNES standard library.

---

## Main Tasks

### 1. Coarse Filter — Orbital Geometry Screening (C++)

Reject satellite pairs whose orbits can never intersect based on altitude bands. A satellite's orbit sweeps an altitude shell between its perigee and apogee. Two satellites can only meet if their shells overlap.

**What to build:**
- `conjunction.h` / `conjunction.cpp` — new C++ source files in `orbitcore/`
- Function: `coarse_filter(satrecs[], n, altitude_margin_km)` → list of pair indices that survive
- For each satellite: compute perigee altitude (`altp * Re`) and apogee altitude (`alta * Re`) from the Satrec
- Two satellites pass the coarse filter if their altitude bands overlap (with margin for SGP4 error)
- Return vector of `(i, j)` index pairs
- Expose to Python via pybind11 in `bindings.cpp`

**Success criteria:**
- [ ] Coarse filter reduces ~435 station pairs to a smaller candidate set
- [ ] ISS pair with nearby-altitude stations survives the filter
- [ ] Pairs with wildly different altitudes (LEO vs MEO) are rejected

---

### 2. Medium Filter — Time-Stepped Distance Screening (C++)

For surviving pairs, propagate both satellites at regular time steps and check distance.

**What to build:**
- Function: `medium_filter(satrec_a, satrec_b, jd_start, jd_end, step_minutes, threshold_km)` → list of `{time, distance}` windows where distance < threshold
- At each time step: propagate both sats → TEME positions → Euclidean distance
- TEME distance is fine for screening (no ECEF conversion needed — both positions are in the same inertial frame at the same instant)
- Return time windows where distance drops below threshold
- Expose to Python via pybind11

**Why TEME distance works:** Both satellites are propagated to the same instant, so their TEME positions are in the same inertial frame. Euclidean distance between two points in the same frame is frame-invariant — converting both to ECEF first would give the same distance.

**Success criteria:**
- [ ] Medium filter identifies time windows where pairs come within threshold
- [ ] Running 24h scan over all coarse-filter survivors completes in <5 seconds for 30 sats
- [ ] Distance values match manual spot-checks (propagate both sats to same time, compute distance in Python)

---

### 3. Fine Filter — Precise TCA via scipy (Python)

For each medium-filter window, find the exact Time of Closest Approach (TCA) and miss distance.

**What to build:**
- New module: `backend/core/conjunction.py`
- Class: `ConjunctionDetector` — orchestrates the full filter cascade
- Fine filter: for each flagged time window, use `scipy.optimize.minimize_scalar` to minimize the distance function `d(t)` between the two satellites
- Distance function calls `orbitcore.sgp4()` for both sats at time `t`, computes Euclidean distance
- Returns: TCA (datetime), miss distance (km), relative velocity (km/s), both satellite positions at TCA

**Success criteria:**
- [ ] TCA precision: within 1 second of actual closest approach
- [ ] Miss distance matches Orekit cross-validation within SGP4 accuracy (~1 km)
- [ ] Full pipeline (coarse → medium → fine) runs in <10 seconds for 30 sats, 24h window

---

### 4. Orekit Cross-Validation (Python)

Validate conjunction results against ESA's Orekit library — the industry standard for orbital mechanics.

**What to build:**
- Install Orekit Python wrapper (`orekit` package + JVM setup)
- Validation script or test: for each detected conjunction, propagate both satellites in Orekit and compute TCA + miss distance
- Compare: our TCA vs Orekit TCA (should agree within seconds), our miss distance vs Orekit (should agree within ~1 km)
- Log discrepancies for analysis

**Success criteria:**
- [ ] Orekit installed and functional (JVM + data files loaded)
- [ ] At least 3 conjunction events cross-validated
- [ ] TCA agreement: within 10 seconds
- [ ] Miss distance agreement: within 5 km (SGP4 accuracy limit)

**Risk:** Orekit requires a JVM and ~100 MB of data files. If setup proves too heavy for the project timeline, we can defer to Week 8 and rely on manual validation for now.

---

### 5. API Endpoint — `/api/conjunctions`

Serve conjunction results through the REST API.

**What to build:**
- New Pydantic schemas: `ConjunctionEvent`, `ConjunctionResponse`
- `GET /api/conjunctions?hours=24&threshold_km=25` — returns all detected conjunctions within time window
- Each result includes: both satellite names/IDs, TCA, miss distance, relative velocity, positions at TCA
- Optional `?time=` parameter (for simulated time support from frontend clock)
- Wire into existing router pattern

**Success criteria:**
- [ ] Endpoint returns valid JSON matching schema
- [ ] Results include ISS close approaches (if any exist in the time window)
- [ ] Response time <15 seconds for 30 sats, 24h window
- [ ] Error handling for invalid parameters (negative hours, etc.)

---

### 6. Phase 2 Scale-Up (~150 satellites)

Switch from "stations" (~30) to "visual" (~150 satellites) and verify conjunction pipeline handles the increased pair count.

**What to build:**
- Add `visual` group support to GPFetcher (may already work — CelesTrak has this group)
- Run conjunction pipeline on 150 sats (~11,175 pairs) — verify coarse filter reduces this dramatically
- Profile performance, identify bottlenecks
- Update frontend to handle larger satellite count (PointPrimitiveCollection already scales)

**Success criteria:**
- [ ] Pipeline runs on 150 sats within 60 seconds (24h window)
- [ ] Coarse filter eliminates >80% of pairs
- [ ] No new bugs from increased data volume

---

## File Structure

```
orbitcore/
├── CMakeLists.txt          — MODIFY: add conjunction.cpp to build
├── include/
│   ├── SGP4.h              — no changes
│   └── conjunction.h       — CREATE: coarse + medium filter declarations
└── src/
    ├── SGP4.cpp            — no changes
    ├── bindings.cpp        — MODIFY: add conjunction function bindings
    ├── conjunction.cpp     — CREATE: coarse + medium filter implementations
    └── hello.cpp           — no changes

backend/
├── core/
│   ├── conjunction.py      — CREATE: ConjunctionDetector (fine filter + orchestration)
│   ├── propagator.py       — no changes (reuse Satrec caching)
│   └── coordinate_transforms.py — no changes
├── models/
│   └── schemas.py          — MODIFY: add ConjunctionEvent, ConjunctionResponse
└── routers/
    └── satellites.py       — MODIFY: add /api/conjunctions endpoint

tests/
└── test_conjunction.py     — CREATE: conjunction detection tests
```

---

## Implementation Order

1. **C++ coarse filter** — altitude band screening, pybind11 bindings, rebuild .so
2. **C++ medium filter** — time-stepped propagation, distance check, pybind11 bindings
3. **Python fine filter** — scipy optimization for TCA, ConjunctionDetector class
4. **API endpoint** — Pydantic schemas, `/api/conjunctions` route
5. **Tests** — unit tests for each filter stage, integration test for full pipeline
6. **Orekit validation** — install, validate, cross-check results
7. **Phase 2 scale-up** — switch to 150 sats, profile, optimize

---

## Things to Watch

| Concern | Detail |
|---------|--------|
| TEME vs ECEF for distance | TEME distance is fine for screening (same frame, same instant). Final miss distance can also be TEME — frame-invariant for same-instant comparison. |
| Satrec lifetime | Satrecs are initialized once per satellite and reused across all time steps. Don't re-init per pair per step. |
| SGP4 error codes | Some satellites may fail to propagate at certain times (decayed orbits, bad TLEs). Catch and skip gracefully. |
| scipy minimizer bounds | Bound `minimize_scalar` to the medium-filter time window ± 1 step. Unbounded search can diverge. |
| Orekit JVM memory | Default JVM heap may be too small for 150+ satellite propagation. May need `-Xmx512m` or similar. |
| Phase 2 pair count | 150 sats = 11,175 pairs. Coarse filter must be fast (microseconds per pair). Medium filter is the bottleneck — O(surviving_pairs × time_steps). |
| Epoch staleness | Old TLEs degrade conjunction accuracy. Flag results where either satellite's TLE is >3 days old. |
| CMake rebuild | After adding `conjunction.cpp`, must rebuild the C++ extension: `cd orbitcore/build && cmake .. && make` |

---

## Success Criteria (Definition of Done)

- [ ] C++ coarse filter screens satellite pairs by altitude band
- [ ] C++ medium filter finds close-approach time windows via time-stepped propagation
- [ ] Python fine filter pinpoints TCA and miss distance via scipy optimization
- [ ] `/api/conjunctions` endpoint serves results with proper Pydantic schemas
- [ ] Orekit cross-validation confirms results (or deferred with documented reason)
- [ ] Full pipeline runs in <15 seconds for 30 sats, 24h window
- [ ] Tests covering each filter stage + integration
- [ ] Phase 2 (150 sats) runs within 60 seconds
- [ ] No regressions — all 279 existing tests still pass
