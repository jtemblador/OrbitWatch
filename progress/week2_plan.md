# Week 2 — TLE Data + C++ SGP4 Propagation (Apr 3–9, 2026)

**Goal:** Build a working TLE fetcher and C++ orbit propagation engine. Verify ISS positions match real-world trackers.

---

## Phase 1 Dataset
**Focus:** Space stations only (~15 objects: ISS, Tiangong, etc.)
- Simplest to test against known positions
- High visibility — easy to verify accuracy
- Gateway to Phase 2–4 scaling

---

## Main Tasks

### 1. TLE Fetcher (backend/core/tle_fetcher.py)
**Input:** CelesTrak API endpoint
**Output:** Parsed TLE data as Pandas DataFrame

```python
# Pseudo-structure
def fetch_tle_stations():
    """Fetch latest station TLEs from CelesTrak (Phase 1)."""
    # GET https://celestrak.org/NORAD/elements/stations.txt
    # Parse 3-line TLE format
    # Return: DataFrame with cols [name, tle_line1, tle_line2, fetch_time]

def refresh_tle_cache():
    """Save to local Parquet for offline use."""
```

**Success criteria:**
- [ ] Fetch returns ISS TLE + Tiangong + other stations
- [ ] TLE lines match expected format (checksums valid)
- [ ] Cache to `backend/data/tle/stations.parquet`

---

### 2. C++ SGP4 Propagation Module (orbitcore/src/sgp4.cpp)
**Input:** TLE + datetime
**Output:** ECI coordinates (x, y, z) in km

**Two approaches:**
A. **Wrap existing sgp4 C library** (faster, proven)
B. **Implement from scratch** (shows deep knowledge, much slower)

**Recommendation:** Use approach A. `libsgp4` is battle-tested, used by NORAD.

```cpp
// orbitcore/src/sgp4.cpp
struct SatellitePosition {
    double x, y, z;  // ECI coordinates (km)
    double vx, vy, vz;  // velocity (km/s)
    double jd;  // Julian date
};

SatellitePosition propagate(
    const std::string& tle_line1,
    const std::string& tle_line2,
    double et  // ephemeris time (from SPICE)
);
```

**Success criteria:**
- [ ] Compiles with CMakeLists.txt
- [ ] Takes TLE + time, returns (x,y,z) ECI
- [ ] pybind11 binding exposes to Python

---

### 3. SPICE Coordinate Transforms (backend/core/coordinate_transforms.py)
**Input:** ECI (x, y, z)
**Output:** Geodetic (lat, lon, alt)

Wrap the SPICE functions from Week 0's test into reusable Python module.

```python
def eci_to_geodetic(eci_position, et):
    """
    Convert ECI (x,y,z) to lat/lon/alt.
    Uses SPICE kernels for accuracy.
    """
    # Load kernels (cached)
    # Rotate J2000 → ITRF93
    # Cartesian → geodetic
    # Return lat, lon, alt (degrees, km)
```

**Success criteria:**
- [ ] Transforms tested with known positions
- [ ] Results within 1 km of reference trackers

---

### 4. Python Propagator Wrapper (backend/core/propagator.py)
**Orchestrates:** TLE fetch → C++ SGP4 → SPICE transform

```python
class SatellitePropagator:
    def __init__(self):
        # Load TLEs
        self.tle_cache = fetch_tle_stations()

    def get_position(self, satellite_name, datetime_utc):
        """
        High-level: Get satellite lat/lon/alt at a given time.

        Returns: {
            'lat': float,
            'lon': float,
            'alt': float,  # km
            'name': str,
            'timestamp': datetime
        }
        """
        # 1. Get TLE from cache
        # 2. Convert UTC → SPICE ET
        # 3. Call C++ propagator → ECI
        # 4. SPICE transform → geodetic
        # 5. Return result
```

**Success criteria:**
- [ ] ISS position at known time matches public trackers (±2 km)
- [ ] Handles all 15 stations
- [ ] Fast enough (propagate 15 satellites in <1 sec)

---

### 5. Unit Tests (tests/test_propagation.py)
**Test cases:**

1. **TLE Fetch**
   - [ ] Fetch succeeds, returns ≥15 objects
   - [ ] TLE format valid (checksum passes)

2. **SGP4 Propagation**
   - [ ] C++ module imports successfully
   - [ ] Propagate ISS at 2024-03-21 00:00:00 UTC
   - [ ] Output (x,y,z) in reasonable range (~6700 km)

3. **Coordinate Transforms**
   - [ ] ECI → geodetic works
   - [ ] Result matches expected lat/lon/alt

4. **End-to-end**
   - [ ] Real ISS TLE → propagate → transform → get position
   - [ ] Compare against N2YO or Heavens-Above
   - [ ] Difference < 2 km (expected SGP4 accuracy)

---

## Implementation Order

1. **TLE Fetcher** (easiest, unblocks others)
2. **SPICE Transforms** (reuse Week 0 code, low risk)
3. **C++ SGP4** (hardest, highest risk)
4. **Propagator Wrapper** (glues it together)
5. **Tests** (validate everything)

---

## Key Files to Create/Modify

```
backend/
├── core/
│   ├── tle_fetcher.py         ← NEW
│   ├── propagator.py          ← NEW (orchestrator)
│   ├── coordinate_transforms.py ← NEW (SPICE wrapper)
│   └── tle_parser.py          ← NEW (TLE parsing)
└── data/
    └── tle/
        └── stations.parquet   ← Generated at runtime

orbitcore/
├── CMakeLists.txt             ← MODIFY (add sgp4 linking)
├── src/
│   ├── hello.cpp              ← DELETE (test file)
│   └── sgp4.cpp               ← NEW
├── include/
│   ├── hello.h                ← DELETE
│   └── sgp4.h                 ← NEW
└── build/                      ← Regenerated

tests/
└── test_propagation.py         ← NEW
```

---

## Success Criteria (Definition of Done)

- [ ] TLE fetcher retrieves and parses Phase 1 stations
- [ ] C++ SGP4 compiles and exposes via pybind11
- [ ] ISS position accurate to within 2 km vs. public tracker
- [ ] All 15 stations propagatable without error
- [ ] Unit tests pass
- [ ] Code committed with clear commit messages

---

## Stretch Goals (if time allows)
- [ ] Performance profile: how fast can we propagate 1000 satellites?
- [ ] Add Phase 2 (150 visual satellites) and test scaling
- [ ] Document SGP4 algorithm choice in notes

---

## Timeline
- **Days 1–2:** TLE fetcher + coordinate transforms
- **Days 3–5:** C++ SGP4 implementation + pybind11 binding
- **Days 5–6:** Propagator wrapper + end-to-end test
- **Day 7:** Polish, tests, commit

---

## Notes for Implementation
- **SGP4 library:** Look for C++ bindings or lightweight C implementation
- **TLE parsing:** CelesTrak format is strict — test edge cases
- **SPICE ET:** Must convert UTC → SPICE ephemeris time correctly
- **Caching:** Cache TLEs locally to avoid repeated fetches
- **Error handling:** TLE fetch may fail — graceful degradation

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| SGP4 C++ library hard to integrate | High | Start with Python sgp4 as fallback |
| SPICE transforms off by large amount | Medium | Use Week 0 test as reference |
| CelesTrak API rate limiting | Low | Cache locally, test with saved TLE file |
| Accuracy worse than expected | Medium | Document expected SGP4 error bounds |

---

## Definition of Done for Week 2
**When you finish Week 2, you should be able to:**
1. Fetch ISS TLE, propagate position 72 hours into future
2. Compare result against Heavens-Above or N2YO — match within 2 km
3. Demonstrate all 15 Phase 1 stations propagatable
4. Unit tests pass with >90% coverage
5. Code ready for API integration in Week 3
