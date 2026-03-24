raps # Week 2 — TLE Data + C++ SGP4 Propagation (Apr 3–9, 2026)

**Goal:** Build a working TLE fetcher and C++ orbit propagation engine. Verify ISS positions match real-world trackers.

---

## Phase 1 Dataset
**Focus:** Space stations only (~30 objects: ISS, Tiangong, etc.)
- Simplest to test against known positions
- High visibility — easy to verify accuracy
- Gateway to Phase 2–4 scaling

---

## Main Tasks

### ✅ 1. GP Data Fetcher (backend/core/tle_fetcher.py) — DONE

**Format decision:** JSON/OMM instead of legacy TLE format.
- Legacy TLE hits 5-digit NORAD ID cap ~July 2026
- JSON has full precision, ISO 8601 dates, and is CelesTrak's recommended format
- SGP4 initialized directly from OMM fields via `Satrec.sgp4init()` — no TLE string needed

**What was built (beyond original plan):**
- `GPFetcher` class with `fetch()`, `fetch_by_catnr()`, `load_cached()`
- Per-record validation: skips malformed, decayed, non-SGP4 (ephemeris_type ≠ 0) records
- Derived orbital params computed: `period`, `semimajor_axis`, `apoapsis`, `periapsis`
- `epoch_age_days` staleness metric — critical for knowing prediction reliability
- Atomic Parquet writes (temp file + rename) — no corruption on interrupt
- mtime fast pre-check before loading full Parquet for freshness test
- 37/37 unit tests passing (`tests/test_gp_fetcher.py`)

**Success criteria:**
- [x] Fetch returns ISS + Tiangong + other stations (30 objects verified live)
- [x] JSON/OMM fields parsed correctly (all 17 OMM fields + 11 derived/metadata)
- [x] Cache to `backend/data/tle/stations.parquet`
- [x] Rate limiting enforced (2-hour minimum between fetches)
- [x] Error handling: graceful fallback to cache on network failure
- [x] Unit tests pass (37/37)

> **Note:** `tle_parser.py` is NOT needed — JSON parsing is handled inside `GPFetcher._parse_json()`.

---

### ✅ 2. Coordinate Transforms (backend/core/coordinate_transforms.py) — DONE

**Input:** (x, y, z) position in TEME frame (SGP4 output)
**Output:** Geodetic (lat, lon, alt in degrees/km) + ECEF position/velocity

**Key finding:** SPICE does NOT know the TEME frame (`UNKNOWNFRAME` error). Three approaches evaluated:
1. SPICE full pipeline (TEME→J2000→ITRF93) — too complex, requires precession/nutation matrices
2. Astropy (native TEME support) — 200MB dependency for one rotation
3. **GMST Z-rotation** — chosen: single matrix multiply, accuracy within SGP4's ~1 km limit

**What was built:**
- `gmst_from_jd()` — IAU 1982 GMST formula (same as Vallado's SGP4)
- `teme_to_ecef()` — GMST Z-rotation + ω×r velocity correction
- `ecef_to_geodetic()` — SPICE `recgeo()` with WGS-84 ellipsoid
- `teme_to_geodetic()` — full pipeline, returns dict with lat/lon/alt/pos_ecef/vel_ecef
- `utc_to_jd()` — datetime → Julian Date for SGP4
- 26/26 unit tests passing (`tests/test_coordinate_transforms.py`)

**Validated with 5 real satellites:** ISS, CSS (Tianhe), FREGAT DEB (eccentric), HTV-X1, CREW DRAGON 12

**Success criteria:**
- [x] TEME frame issue resolved (GMST rotation — documented in task_logs)
- [x] `teme_to_geodetic(pos, jd, vel)` function implemented
- [x] Tested with real ISS SGP4 output → altitude 425.4 km, speed 7.358 km/s
- [x] Results validated against known orbital parameters for 5 satellites
- [x] Unit tests pass (26/26)

---

### ✅ 3. C++ SGP4 Propagation Engine (orbitcore/) — DONE

**Input:** OMM orbital elements (radians) + time since epoch (minutes)
**Output:** (x, y, z, vx, vy, vz) in TEME frame (km, km/s)

**Architecture decision:** Wrapped Vallado's C++ SGP4 via pybind11 (Option A) rather than using the Python `sgp4` library. Rationale:
- Portfolio value: demonstrates C++/pybind11 with real aerospace code
- Performance: conjunction scanner (Week 6) can call SGP4 directly in C++ without Python overhead
- Same validated Vallado code that Brandon Rhodes' Python library wraps

**What was built:**
- Copied `SGP4.cpp` (3,247 lines) + `SGP4.h` from Vallado's reference into `orbitcore/`
- pybind11 bindings expose: `sgp4init()`, `sgp4()`, `jday()`, `invjday()`, `getgravconst()`
- `Satrec` Python class wrapping `elsetrec` struct (~20 key fields exposed)
- `GravConst` enum (WGS72OLD, WGS72, WGS84)
- `sgp4init()` returns `Satrec` object, raises `RuntimeError` on failure
- `sgp4()` returns `((x,y,z), (vx,vy,vz))` tuples, raises `RuntimeError` on propagation error
- Back-computes `jdsatepoch`/`jdsatepochF` from epoch parameter (Vallado's code only sets these in `twoline2rv`, which we bypass)
- 54/54 unit tests passing (`tests/test_sgp4_cpp.py`)

**Validation results:**
- 32/33 Vallado test satellites match Python `sgp4` library to **sub-micrometer** (< 1 nm)
- 1 satellite (23599, deep-space e=0.714) differed by 0.9 km due to opsmode ('a' vs 'i') — confirmed identical at same opsmode
- ISS at epoch: altitude 409 km, speed 7.67 km/s — correct
- End-to-end tested: C++ SGP4 → coordinate transforms → geodetic verified for ISS + GPS

**Success criteria:**
- [x] Vallado's C++ code compiles via CMakeLists.txt (clean build, zero warnings)
- [x] pybind11 binding: `orbitcore.sgp4init(...)` + `orbitcore.sgp4(satrec, tsince)` → `((x,y,z), (vx,vy,vz))`
- [x] Validated against Vallado's 33 test cases (`SGP4-VER.TLE`) — 32/33 exact match
- [x] Cross-validated against Python `sgp4` library — identical results
- [x] Unit tests pass (54/54)

---

### ✅ 4. Python Propagator Wrapper (backend/core/propagator.py) — DONE

**Orchestrates:** GPFetcher → C++ SGP4 → coordinate transforms

**What was built:**
- `omm_to_sgp4_params(row)` — handles all unit conversions from OMM → sgp4init params
- `SatellitePropagator` class with lazy data loading, O(1) name/NORAD lookup indexes, and Satrec caching
- `get_position(name, utc_dt)` — single satellite by name
- `get_position_by_norad_id(norad_id, utc_dt)` — single satellite by NORAD ID
- `get_all_positions(utc_dt)` — batch propagate all satellites in group
- `get_positions_at_times(name, utc_dts)` — ground track / time series for one satellite

**Unit conversions implemented:**
- Degrees → radians for angular elements (inclination, RAAN, argp, mean anomaly)
- rev/day → rad/min for mean_motion: `÷ xpdotp` where `xpdotp = 1440/(2π)`
- `mean_motion_dot`: OMM value `÷ (xpdotp × 1440)`
- `mean_motion_ddot`: OMM value `÷ (xpdotp × 1440²)`
- ISO 8601 epoch → Julian date → epoch days (`jd - 2433281.5` for sgp4init)

**Scalability design (Phase 3 ready):**
- O(1) lookup indexes (`_name_index`, `_norad_index`) built once on data load — no DataFrame scans per call
- Satrec cache: `sgp4init()` runs once per satellite, `sgp4()` reused for all time steps
- Satrec memory: ~1 KB each → 6,000 Starlink sats = ~6 MB total

> **⚠ PERF TODO (Week 8 Phase 3 scale-up):** `get_all_positions()` uses `df.iterrows()` — acceptable at 30 sats, needs replacement with vectorized iteration at 6,000+. See roadmap flag.

**Success criteria:**
- [x] ISS: alt=428 km, speed=7.659 km/s — correct
- [x] All 30 Phase 1 stations propagated without error
- [x] Propagate 30 satellites in <1 sec (typically ~0.1s)
- [x] Cross-validated: all 30 stations match Python sgp4 to sub-meter

---

### ✅ 5. Unit Tests — DONE

1. **SGP4 Propagation** ✅ (54/54 in `tests/test_sgp4_cpp.py`)
   - [x] C++ module imports, version string, GravConst enum, Satrec struct
   - [x] Propagate ISS/GPS/Molniya at epoch — correct altitude/speed
   - [x] Validated against all 33 Vallado test cases (SGP4-VER.TLE)
   - [x] Cross-validated against Python sgp4 library (sub-micrometer match)
   - [x] Forward/backward propagation, repeatability, order independence
   - [x] Edge cases: zero/negative/large tsince, multi-sat independence, 1000x rapid loop
   - [x] End-to-end: C++ SGP4 → coordinate transforms → geodetic (ISS, GPS)

2. **Coordinate Transforms** ✅ (26/26 in `tests/test_coordinate_transforms.py`)
   - [x] TEME → geodetic produces reasonable lat/lon/alt
   - [x] Multi-satellite validation (5 satellites, diverse orbits)
   - [x] Ground track test (12 time offsets over 7 days)

3. **End-to-end** ✅ (covered in test_sgp4_cpp.py TestEndToEnd)
   - [x] C++ SGP4 → teme_to_geodetic → ISS lat/lon/alt verified
   - [x] ISS groundtrack over full orbit stays within inclination band
   - [x] GPS altitude ~20200 km verified through full pipeline

4. **Propagator Wrapper** ✅ (80/80 in `tests/test_propagator.py`)
   - [x] Unit conversion correctness (20 tests: degrees→radians, rev/day→rad/min, epoch)
   - [x] ISS/CSS single-satellite sanity (altitude, speed, lat bounded by inclination)
   - [x] All 30 Phase 1 stations batch propagate (positive alt, valid lat/lon, reasonable speeds)
   - [x] Multi-time propagation / ground track (longitude sweep, altitude stability, lat oscillation)
   - [x] Caching and indexes (lazy load, O(1) lookup, cache clear, case-insensitive)
   - [x] Performance: 30 sats < 1s, 100 time steps < 1s, constant-time index lookup
   - [x] Scalability simulation: 6000-sat index build < 1s, 1000 lookups < 10ms
   - [x] Cross-validation: all 30 stations match Python sgp4 to sub-meter

> **Total: 197/197 tests passing** across test_gp_fetcher.py (37), test_coordinate_transforms.py (26), test_sgp4_cpp.py (54), test_propagator.py (80).

---

## Implementation Order

1. ✅ **GP Data Fetcher** — done (37/37 tests)
2. ✅ **Coordinate Transforms** — done (26/26 tests, GMST approach)
3. ✅ **C++ SGP4 Engine** — done (54/54 tests, Vallado C++ via pybind11)
4. ✅ **Propagator Wrapper** — done (80/80 tests, O(1) indexes, Satrec cache)
5. ✅ **Tests** — 197/197 total

---

## Key Files

```
backend/
├── core/
│   ├── tle_fetcher.py           ✅ DONE (37 tests)
│   ├── coordinate_transforms.py ✅ DONE (26 tests)
│   └── propagator.py            ✅ DONE (80 tests)
├── orbitcore.cpython-312-x86_64-linux-gnu.so  ✅ Built (import orbitcore)
└── data/tle/stations.parquet    ✅ Generated

orbitcore/
├── CMakeLists.txt               ✅ Updated (compiles SGP4.cpp)
├── src/
│   ├── SGP4.cpp                 ✅ Vallado's implementation (3,247 lines)
│   ├── bindings.cpp             ✅ pybind11 bindings (sgp4init, sgp4, jday, etc.)
│   └── hello.cpp                ✅ Hello world (kept for verification)
├── include/
│   ├── SGP4.h                   ✅ elsetrec struct + function declarations
│   └── hello.h                  ✅ Hello world header
└── build/
    └── orbitcore.cpython-312-x86_64-linux-gnu.so  ✅ Compiled module

tests/
├── test_gp_fetcher.py           ✅ 37/37 passing
├── test_coordinate_transforms.py ✅ 26/26 passing
├── test_sgp4_cpp.py             ✅ 54/54 passing
└── test_propagator.py           ✅ 80/80 passing
```

---

## Success Criteria (Definition of Done)

- [x] GP fetcher retrieves and parses Phase 1 stations (30 objects)
- [x] Parquet cache, rate limiting, error handling, tests all done
- [x] Coordinate transform module implemented (TEME → ECEF → geodetic via GMST)
- [x] C++ SGP4 compiles and exposes via pybind11 (54 tests, 33 Vallado test sats validated)
- [x] ISS position accurate — 409 km altitude, 7.67 km/s, matches Python sgp4 exactly
- [x] All 30 stations propagatable without error
- [x] Propagator wrapper integrates GPFetcher → C++ SGP4 → coordinate transforms

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| ~~SPICE doesn't support TEME frame natively~~ | ~~Medium~~ | **RESOLVED** — Used GMST Z-rotation, validated with 5 satellites |
| ~~Vallado's C++ build issues with CMake~~ | ~~Medium~~ | **RESOLVED** — Clean build, no warnings. Key: no precompiled headers, no stdafx, no CLR |
| ~~Unit conversion mistakes (deg→rad, rev/day→rad/min)~~ | ~~High~~ | **RESOLVED** — Cross-validated all 30 stations vs Python sgp4, sub-meter match |
| ~~Accuracy worse than expected~~ | ~~Medium~~ | **RESOLVED** — Sub-micrometer match vs reference. SGP4 accuracy is ~1 km at epoch as expected |

---

## Definition of Done for Week 2
**When you finish Week 2, you should be able to:**
1. Fetch ISS OMM data, propagate position 72 hours into future
2. Compare result against Heavens-Above or N2YO — match within 2 km
3. Demonstrate all 30 Phase 1 stations propagatable
4. Unit tests pass with >90% coverage
5. Code ready for API integration in Week 3
