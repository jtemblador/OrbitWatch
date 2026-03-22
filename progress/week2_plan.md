# Week 2 — TLE Data + C++ SGP4 Propagation (Apr 3–9, 2026)

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

### ⬜ 3. C++ SGP4 Propagation Engine (orbitcore/src/sgp4.cpp) — AFTER SPICE

**Input:** OMM orbital elements + datetime
**Output:** (x, y, z, vx, vy, vz) in TEME frame

**Approach:** Wrap Vallado's validated C++ code.
- Source already on disk: `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/`
- Files: `sgp4unit.cpp/.h`, `sgp4ext.cpp/.h`, `sgp4io.cpp/.h`
- Copy into `orbitcore/src/`, expose via pybind11

**Do NOT:**
- Implement SGP4 from scratch (Vallado's code is the reference implementation)
- Use WGS-84 constants (use WGS-72 — matches how NORAD generates TLEs)

**Success criteria:**
- [ ] Vallado's C++ code compiles via CMakeLists.txt
- [ ] pybind11 binding: `orbitcore.propagate(omm_fields, jd) → (x,y,z,vx,vy,vz)`
- [ ] Validated against Vallado's test cases (`sgp4-ver.tle`)

---

### ⬜ 4. Python Propagator Wrapper (backend/core/propagator.py)

**Orchestrates:** GPFetcher → C++ SGP4 → coordinate transforms

```python
class SatellitePropagator:
    def get_position(self, satellite_name: str, utc_dt: datetime) -> dict:
        """
        Returns: {'lat': float, 'lon': float, 'alt': float, 'name': str, 'timestamp': datetime}
        """
        # 1. Get OMM row from GPFetcher cache
        # 2. Convert UTC → Julian date (utc_to_jd)
        # 3. Call C++ propagator → TEME (x,y,z,vx,vy,vz)
        # 4. teme_to_geodetic() → lat/lon/alt + ECEF position
        # 5. Return result dict
```

**Unit conversions the wrapper must handle:**
- Degrees → radians for angular elements (inclination, RAAN, etc.)
- rev/day → rad/min for mean_motion
- ISO 8601 epoch → Julian date (`epoch_jd - 2433281.5` for sgp4init)

**Success criteria:**
- [ ] ISS position at known time matches public trackers (±2 km)
- [ ] All 30 Phase 1 stations propagatable without error
- [ ] Propagate 30 satellites in <1 sec

---

### ⬜ 5. Unit Tests (tests/test_propagation.py)

1. **SGP4 Propagation**
   - [ ] C++ module imports successfully
   - [ ] Propagate ISS at known epoch → (x,y,z) in ~6700 km range
   - [ ] Validate against Vallado's `sgp4-ver.tle` test cases

2. **Coordinate Transforms** ✅ DONE (26/26 in `tests/test_coordinate_transforms.py`)
   - [x] TEME → geodetic produces reasonable lat/lon/alt
   - [x] Multi-satellite validation (5 satellites, diverse orbits)
   - [x] Ground track test (12 time offsets over 7 days)

3. **End-to-end**
   - [ ] Real ISS OMM → propagate → transform → compare against tracker

> **Note:** `tests/test_gp_fetcher.py` (37 tests) covers Task 1. `tests/test_coordinate_transforms.py` (26 tests) covers Task 2. This file covers Tasks 3–4.

---

## Implementation Order

1. ✅ **GP Data Fetcher** — done (37/37 tests)
2. ✅ **Coordinate Transforms** — done (26/26 tests, GMST approach)
3. ⬜ **C++ SGP4 Engine** — NEXT: Vallado's code, wrap via pybind11
4. ⬜ **Propagator Wrapper** — glues 2+3 together, handles unit conversions
5. ⬜ **Tests** — coordinate transform tests done, SGP4 + end-to-end remain

---

## Key Files

```
backend/
├── core/
│   ├── tle_fetcher.py           ✅ DONE (37 tests)
│   ├── coordinate_transforms.py ✅ DONE (26 tests)
│   └── propagator.py            ⬜ PENDING
└── data/tle/stations.parquet    ✅ Generated

orbitcore/
├── CMakeLists.txt               ⬜ MODIFY
├── src/
│   ├── sgp4unit.cpp             ⬜ Copy from misc/
│   ├── sgp4ext.cpp              ⬜ Copy from misc/
│   ├── sgp4io.cpp               ⬜ Copy from misc/
│   └── bindings.cpp             ✅ Exists (add SGP4 binding)
└── include/
    ├── sgp4unit.h               ⬜ Copy from misc/
    └── sgp4ext.h                ⬜ Copy from misc/

tests/
├── test_gp_fetcher.py           ✅ 37/37 passing
├── test_coordinate_transforms.py ✅ 26/26 passing
└── test_propagation.py          ⬜ PENDING
```

---

## Success Criteria (Definition of Done)

- [x] GP fetcher retrieves and parses Phase 1 stations (30 objects)
- [x] Parquet cache, rate limiting, error handling, tests all done
- [x] Coordinate transform module implemented (TEME → ECEF → geodetic via GMST)
- [ ] C++ SGP4 compiles and exposes via pybind11
- [ ] ISS position accurate to within 2 km vs. public tracker
- [ ] All 30 stations propagatable without error
- [ ] test_propagation.py passes

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| ~~SPICE doesn't support TEME frame natively~~ | ~~Medium~~ | **RESOLVED** — Used GMST Z-rotation, validated with 5 satellites |
| Vallado's C++ build issues with CMake | Medium | Follow sgp4_CodeReadme.pdf in misc/ |
| Unit conversion mistakes (deg→rad, rev/day→rad/min) | High | Validate against Vallado test cases first |
| Accuracy worse than expected | Medium | Document expected SGP4 error bounds (~1 km at epoch) |

---

## Definition of Done for Week 2
**When you finish Week 2, you should be able to:**
1. Fetch ISS OMM data, propagate position 72 hours into future
2. Compare result against Heavens-Above or N2YO — match within 2 km
3. Demonstrate all 30 Phase 1 stations propagatable
4. Unit tests pass with >90% coverage
5. Code ready for API integration in Week 3
