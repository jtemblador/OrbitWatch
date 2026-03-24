# Task 2.4 — Python Propagator Wrapper

**Date:** Mar 23, 2026
**Status:** DONE
**Tests:** 80/80 passing (10 test classes)

---

## Goal

Build a single entry point that downstream code (API, frontend) can call to get a satellite's position. The propagator orchestrates three previously-built components into one pipeline:

```
GPFetcher (cached OMM data) → unit conversion → C++ SGP4 → coordinate transforms → lat/lon/alt
```

Without this wrapper, callers would need to manually extract OMM fields, convert units (degrees→radians, rev/day→rad/min, epoch→days), initialize a C++ Satrec, propagate, convert TEME→geodetic — about 20 lines of boilerplate per satellite. The wrapper reduces this to one call: `prop.get_position("ISS (ZARYA)", utc_dt)`.

---

## Approach

### Unit Conversion (the tricky part)

CelesTrak OMM data and the SGP4 engine use different units. Getting any conversion wrong produces silently wrong positions — the code runs fine but outputs garbage. The conversions:

| Field | OMM Format | SGP4 Expects | Conversion |
|-------|-----------|--------------|------------|
| inclination, RAAN, argp, MA | degrees | radians | × π/180 |
| mean_motion | rev/day | rad/min | ÷ xpdotp (1440/2π) |
| mean_motion_dot | rev/day² (already ÷2) | internal units | ÷ (xpdotp × 1440) |
| mean_motion_ddot | rev/day³ (already ÷6) | internal units | ÷ (xpdotp × 1440²) |
| epoch | ISO 8601 datetime | days since 1949-12-31 | datetime → JD → JD - 2433281.5 |
| bstar, eccentricity | dimensionless | dimensionless | pass through |

These were extracted from the `key_information.md` notes and cross-validated by comparing our output against the Python sgp4 library. All 30 satellites match to sub-meter.

### Scalability: O(1) Lookups via Indexes

The initial implementation used pandas `df["object_name"].str.upper() == name.upper()` for every lookup — a full DataFrame scan. At 30 satellites this is fine. At 6,000 (Starlink) this would be called thousands of times per API refresh, wasting cycles.

**Fix:** Build dict indexes on first data load:
- `_name_index: dict[str, int]` — maps uppercase name → DataFrame row index
- `_norad_index: dict[int, int]` — maps NORAD ID → DataFrame row index

Both are rebuilt on `reload_data()`. Lookups are O(1) dict gets instead of O(n) scans.

### Satrec Caching

`sgp4init()` is expensive — it computes all perturbation coefficients for the satellite's orbit. But once initialized, `sgp4()` (propagation) is cheap. The propagator caches `(Satrec, jd_epoch, jd_epoch_frac)` tuples keyed by NORAD ID. Repeated time-step propagations skip initialization entirely.

Memory estimate: each Satrec is ~1 KB (elsetrec struct = ~110 doubles). At 6,000 Starlink sats = ~6 MB — negligible.

### Lazy Loading

Data is not loaded from Parquet until the first call. This means creating a `SatellitePropagator()` is instant, and the API can construct it at startup without blocking.

---

## What Was Built

### `backend/core/propagator.py`

| Function/Class | Purpose |
|----------------|---------|
| `XPDOTP` | Constant: 1440/(2π) ≈ 229.18, converts rev/day to rad/min |
| `omm_to_sgp4_params(row)` | Convert one DataFrame row → 13 sgp4init parameters |
| `SatellitePropagator.__init__(group, fetcher)` | Constructor, defaults to "stations" group |
| `.get_position(name, utc_dt)` | Propagate by satellite name → result dict |
| `.get_position_by_norad_id(norad_id, utc_dt)` | Propagate by NORAD catalog number |
| `.get_all_positions(utc_dt)` | Batch propagate all satellites in group |
| `.get_positions_at_times(name, utc_dts)` | One satellite at multiple times (ground tracks) |
| `.reload_data()` | Clear all caches, force re-read from Parquet |
| `._build_indexes()` | Build O(1) name/NORAD lookup dicts |

Result dict format:
```python
{
    "name": "ISS (ZARYA)",
    "norad_id": 25544,
    "lat": 48.49,          # degrees
    "lon": -153.21,        # degrees
    "alt": 428.4,          # km above WGS-84 ellipsoid
    "pos_ecef": [x, y, z], # km (for conjunction distances)
    "vel_ecef": [vx,vy,vz],# km/s (ground-relative)
    "speed_km_s": 7.659,   # inertial speed (TEME)
    "timestamp": datetime,  # the requested UTC time
    "epoch_age_days": 0.5,  # how stale the TLE is
}
```

---

## Validation

### Test Classes (80 tests)

| Class | Tests | Covers |
|-------|-------|--------|
| `TestUnitConversions` | 21 | All 13 sgp4init params, edge cases (0°, 360°, negative drag, pass-through) |
| `TestSingleSatPropagation` | 16 | ISS/CSS altitude/speed/lat, ECEF magnitude, velocity, NAUKA co-location |
| `TestErrorHandling` | 6 | Unknown name/ID, empty name, far future, backward propagation |
| `TestBatchPropagation` | 9 | All 30 stations, altitude/lat/lon/speed bounds, ISS present |
| `TestMultiTimePropagation` | 6 | Ground track, altitude stability, lat oscillation, timestamp ordering |
| `TestCachingAndIndexes` | 8 | Lazy load, index rebuild, cache clear, case-insensitive keys |
| `TestPerformance` | 4 | 30 sats < 1s, 100 time steps < 1s, warm vs cold, constant-time lookup |
| `TestScalability` | 3 | 6000-sat index build < 1s, 1000 lookups < 10ms, Satrec memory estimate |
| `TestCrossValidation` | 3 | ISS, CSS, all 30 stations vs Python sgp4 (sub-meter ECEF match) |
| `TestPropagatorConfig` | 4 | Constructor defaults, custom group/fetcher, initial empty state |

### ISS Position Check
```
ISS: lat=48.49°, lon=-153.21°, alt=428.4 km, speed=7.659 km/s
```
Altitude ~428 km (expected 400–435 km), speed 7.659 km/s (expected ~7.66 km/s).

### All 30 Phase 1 Stations
All 30 propagated without error. All altitudes positive, all lat/lon in valid ranges, all speeds between 1–11 km/s.

### Cross-Validation vs Python sgp4
All 30 stations compared against the Python sgp4 library (Brandon Rhodes' wrapper of the same Vallado C code). Maximum ECEF position difference across all satellites: **< 0.001 km** (sub-meter). This confirms our unit conversions are correct.

### Performance
- 30 satellites propagated in **< 1 second** (typically ~0.1s)
- 100 time steps for one satellite in **< 1 second** (typically ~0.06s)
- Simulated 6,000-satellite index build: **< 1 second**
- 1,000 index lookups: **< 10ms**

---

## Files

| File | Action | Purpose |
|------|--------|---------|
| `backend/core/propagator.py` | Created | Propagator wrapper: pipeline orchestration, O(1) indexes, Satrec cache |
| `tests/test_propagator.py` | Created | 80 tests: unit conversions, propagation sanity, batch, ground tracks, caching, scalability, cross-validation |
| `progress/roadmap.md` | Updated | Added Phase 3 perf flag for `iterrows()` replacement at Week 8 |

---

## Function Reference

### `omm_to_sgp4_params(row: pd.Series) → dict`
Converts a GPFetcher DataFrame row into the 13 parameters required by `orbitcore.sgp4init()`. Handles datetime→epoch, degrees→radians, rev/day→rad/min. Accepts both `datetime` and `pd.Timestamp` epoch values.

### `SatellitePropagator.get_position(name, utc_dt) → dict`
The main entry point. Looks up the satellite by name (O(1) via index), initializes or retrieves a cached Satrec, computes tsince in minutes from epoch, calls `orbitcore.sgp4()` for TEME position/velocity, converts to geodetic via `teme_to_geodetic()`, and returns a result dict with lat/lon/alt/ECEF/speed.

### `SatellitePropagator.get_all_positions(utc_dt) → list[dict]`
Iterates over all satellites in the group's DataFrame, propagating each. Catches and logs `RuntimeError` from SGP4 (e.g., decayed orbits) without aborting the batch. Returns all successfully propagated positions.

### `SatellitePropagator._build_indexes()`
Called once when data is first loaded. Builds two dictionaries: `_name_index` (uppercase name → row index) and `_norad_index` (NORAD ID → row index). These provide O(1) satellite lookups, critical for scaling beyond Phase 1.

---

## Lessons Learned

1. **Namespace package shadowing is subtle.** The `orbitcore/` source directory acts as a Python namespace package, shadowing the compiled `orbitcore.cpython-312-x86_64-linux-gnu.so` in `backend/`. Required explicit `sys.path.insert()` to ensure the .so is found first. This will need a cleaner solution (e.g., install as a package) before deployment.

2. **Unit conversions are the riskiest part of the pipeline.** The SGP4 algorithm produces no error if you feed it wrong units — it just computes the wrong orbit. Cross-validation against an independent implementation (Python sgp4 library) was essential to catch mistakes.

3. **Design for the next phase, not just the current one.** Adding O(1) indexes now (instead of waiting until Phase 3) took 10 minutes and prevents a performance cliff when scaling from 30 to 6,000 satellites. The simulated scalability tests verify the approach works.

4. **Satrec caching is essential.** `sgp4init()` is much more expensive than `sgp4()`. For API endpoints that refresh satellite positions every few seconds, re-initializing all 6,000 Satrecs each time would dominate latency. The cache makes repeat calls ~10× faster.
