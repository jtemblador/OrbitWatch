# Task 2.3 — C++ SGP4 Propagation Engine

**Date:** Mar 23, 2026
**Status:** DONE
**Tests:** 54/54 passing

---

## Goal

Build a C++ SGP4 propagation engine accessible from Python. Given orbital elements (from GPFetcher) and a target time, return the satellite's TEME position and velocity `(x, y, z, vx, vy, vz)` in km and km/s. This is the computational core of OrbitWatch — every position, ground track, and conjunction calculation runs through it.

---

## Architecture Decision: Option A vs Option B

Two valid approaches were evaluated before writing any code:

### Option A — Wrap Vallado's C++ via pybind11 (chosen)
Compile Vallado's `SGP4.cpp` directly into a Python extension module using pybind11. Python calls `orbitcore.sgp4init()` and `orbitcore.sgp4()`, which execute native C++.

### Option B — Use Python `sgp4` library, save C++ for conjunction scanner
Brandon Rhodes' Python `sgp4` library already wraps the same Vallado C code. It works and is well-maintained. Reserve the C++ effort for Week 6 conjunction scanning, where it matters most (N×N pairwise distance checks).

### Decision: Option A

**Why Option A:**

1. **Portfolio value.** The job applications are to aerospace/defense companies (SpaceX, Northrop Grumman, Aerospace Corp). A repo demonstrating C++/pybind11 wrapping of real NORAD orbital mechanics code is a much stronger signal than calling a Python library. Option B would make the C++ usage invisible.

2. **Conjunction scanner integration.** Week 6 builds a C++ conjunction scanner that needs to call SGP4 directly — potentially billions of times for Starlink-scale screening. With Option A, the C++ SGP4 engine is already in the same `.so` file; we extend `bindings.cpp` with a new `scan_conjunctions()` function that calls `SGP4Funcs::sgp4()` natively in a tight loop. With Option B, the scanner would need to cross the Python/C++ boundary per satellite per timestep, adding overhead.

3. **Same underlying code.** Both options run the same Vallado implementation. The accuracy is identical. Option A has no correctness downside.

---

## What Vallado's Code Is

**Source:** `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/SGP4/SGP4/SGP4.cpp`

This is David Vallado's 2006 (revised 2020) modernization of the original 1980 NORAD SGP4 code. The original FORTRAN IV had numerous bugs — infinite loops for high-eccentricity orbits, discontinuities in deep-space perturbations, broken backward propagation. Vallado's version fixes all of them and has been validated against ~9,000 real satellites.

Key properties:
- 3,247 lines of C++, all in `SGP4Funcs` namespace
- Single consolidated file — `sgp4unit`, `sgp4io`, and `sgp4ext` were merged in the 2020 version
- All bug fixes marked with `// sgp4fix` comments
- Supports both near-Earth (< 225 min period) and deep-space (≥ 225 min) orbits under a unified interface
- `sgp4init()` runs once: computes all perturbation constants and stores in `elsetrec` struct
- `sgp4()` runs per time step: takes the initialized struct + tsince (minutes from epoch), returns position/velocity

---

## Implementation

### Files copied from misc/ into orbitcore/

| File | Source | Lines |
|------|--------|-------|
| `orbitcore/src/SGP4.cpp` | `misc/.../sgp4/cpp/SGP4/SGP4/SGP4.cpp` | 3,247 |
| `orbitcore/include/SGP4.h` | `misc/.../sgp4/cpp/SGP4/SGP4/SGP4.h` | 232 |

`SGP4.h` defines:
- `gravconsttype` enum: `wgs72old`, `wgs72`, `wgs84`
- `elsetrec` struct: ~110 fields (epoch, orbital elements, perturbation constants, gravity model, metadata)
- Function signatures in `SGP4Funcs` namespace

### CMakeLists.txt update

Added `src/SGP4.cpp` to the pybind11 module sources:
```cmake
pybind11_add_module(orbitcore
    src/bindings.cpp
    src/hello.cpp
    src/SGP4.cpp
)
```

### pybind11 bindings (`orbitcore/src/bindings.cpp`)

Exposes the following to Python:

| Python name | C++ equivalent | Notes |
|-------------|---------------|-------|
| `orbitcore.GravConst` | `gravconsttype` enum | WGS72OLD, WGS72, WGS84 |
| `orbitcore.Satrec` | `elsetrec` struct | ~20 key fields exposed |
| `orbitcore.sgp4init(...)` | `SGP4Funcs::sgp4init()` | Returns initialized Satrec |
| `orbitcore.sgp4(satrec, tsince)` | `SGP4Funcs::sgp4()` | Returns `((x,y,z), (vx,vy,vz))` |
| `orbitcore.jday(y,m,d,h,mn,s)` | `SGP4Funcs::jday_SGP4()` | Returns `(jd, jdFrac)` |
| `orbitcore.invjday(jd, jdFrac)` | `SGP4Funcs::invjday_SGP4()` | Returns `(y,m,d,h,mn,s)` |
| `orbitcore.getgravconst(whichconst)` | `SGP4Funcs::getgravconst()` | Returns dict of constants |
| `orbitcore.SGP4_VERSION` | `SGP4Version` string | Version identifier |

---

## Key Bug Found During Implementation

### `jdsatepoch` not set by `sgp4init()`

After wrapping, tests showed `satrec.jdsatepoch == 0.0`. This caused tsince calculations to be wildly wrong.

**Root cause:** In Vallado's code, `jdsatepoch` and `jdsatepochF` are only set inside `twoline2rv()` — the function that parses TLE strings. We bypass `twoline2rv()` entirely (we initialize directly from OMM fields), so these fields never get populated.

**Fix (bindings.cpp):** After calling `sgp4init()`, back-compute the Julian Date from the epoch parameter:
```cpp
// sgp4init doesn't set jdsatepoch (only twoline2rv does).
// Back-compute it from the epoch parameter so Python can access it.
double jd_epoch = epoch + 2433281.5;
satrec.jdsatepoch = floor(jd_epoch) + 0.5;
satrec.jdsatepochF = jd_epoch - satrec.jdsatepoch;
```

Where `2433281.5` is the Julian Date of 1949 Dec 31 00:00 UTC — the base date for SGP4's epoch parameter. This is the inverse of the epoch conversion in `omm_to_sgp4_params()`.

---

## Validation

### Vallado's 33 Test Cases (`SGP4-VER.TLE`)

Propagated all 33 satellites from `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/SGP4-VER.TLE` and compared our output against the Python `sgp4` library (same Vallado code, used as reference):

| Result | Count |
|--------|-------|
| Sub-micrometer match (< 0.000001 km) | 32/33 |
| Mismatch | 1/33 — satellite 23599 |

**Satellite 23599 (mismatch explained):** Deep-space, highly eccentric orbit (e=0.714). Difference: 0.9 km. Root cause: Python `sgp4` library defaults to `opsmode='i'` (improved), our bindings use `opsmode='a'` (AFSPC). Verified: with the same opsmode, the difference is exactly 0.000000000000 km. Not a bug — expected behavior when comparing different operation modes.

### ISS End-to-End
- Altitude at epoch: 409 km (expected ~400–430 km) ✓
- Speed: 7.67 km/s (expected ~7.66 km/s) ✓
- Full pipeline: C++ SGP4 → teme_to_geodetic → lat/lon/alt verified ✓

### GPS End-to-End
- Altitude: ~20,200 km (expected GPS orbital altitude) ✓

---

## Test Coverage (54 tests in `tests/test_sgp4_cpp.py`)

| Class | Tests | Covers |
|-------|-------|--------|
| `TestModuleBasics` | 3 | Import, version string, hello_world backward compat |
| `TestGravityConstants` | 6 | WGS-72/84 enum, constant values, xke consistency |
| `TestSatrec` | 5 | Construction, field access, orbital elements, epoch, alta/altp |
| `TestSgp4Init` | 8 | Valid inits (ISS/GPS/Molniya), both opsmodes, invalid inputs |
| `TestSgp4Propagation` | 10 | Output format, altitudes, speeds, forward/backward, repeatability |
| `TestTimeConversions` | 6 | J2000 epoch, Unix epoch, round-trips, leap year, fractional seconds |
| `TestCrossValidation` | 3 | ISS/GPS/Molniya exact match vs Python sgp4 library |
| `TestValladoVerification` | 2 | All 33 Vallado test sats, near-Earth sub-micrometer accuracy |
| `TestEndToEnd` | 4 | C++ SGP4 → teme_to_geodetic → ISS/GPS lat/lon/alt, groundtrack |
| `TestEdgeCases` | 7 | Zero/negative/large tsince, multi-sat independence, 1000x rapid loop |

---

## Files

| File | Action | Purpose |
|------|--------|---------|
| `orbitcore/src/SGP4.cpp` | Added | Vallado's SGP4 implementation (3,247 lines) |
| `orbitcore/include/SGP4.h` | Added | elsetrec struct + SGP4Funcs function declarations |
| `orbitcore/src/bindings.cpp` | Rewritten | pybind11 bindings: sgp4init, sgp4, jday, invjday, getgravconst, Satrec, GravConst |
| `orbitcore/CMakeLists.txt` | Modified | Added SGP4.cpp to pybind11 module sources |
| `tests/test_sgp4_cpp.py` | Created | 54 tests (10 classes) |
| `misc/README.md` | Created | Documents both misc/ reference folders for future sessions |

---

## Function Reference

### `orbitcore.sgp4init(whichconst, opsmode, satnum, epoch, bstar, ndot, nddot, ecco, argpo, inclo, mo, no_kozai, nodeo) → Satrec`
Initializes a satellite record. All angular inputs in radians, mean motion in rad/min, epoch in days since 1949-12-31. Returns Satrec ready for propagation. Raises `RuntimeError` on failure.

### `orbitcore.sgp4(satrec, tsince) → ((x,y,z), (vx,vy,vz))`
Propagates the satellite to `tsince` minutes from epoch. Returns position in km and velocity in km/s, both in the TEME frame. Raises `RuntimeError` on propagation failure (e.g., decayed orbit). Can propagate forward or backward in time.

### `orbitcore.jday(year, mon, day, hr, minute, sec) → (jd, jdFrac)`
Converts a calendar date to Julian Date in split form (whole + fraction). The split form preserves floating-point precision over long time spans.

### `orbitcore.getgravconst(whichconst) → dict`
Returns gravity constants for the specified model: `tumin`, `mus`, `radiusearthkm`, `xke`, `j2`, `j3`, `j4`, `j3oj2`. Use `GravConst.WGS72` for SGP4.

---

## Lessons Learned

1. **`sgp4init()` and `twoline2rv()` are not equivalent.** The standard SGP4 tutorials show `twoline2rv()` as the entry point — it parses TLE strings and calls `sgp4init()` internally, but also sets `jdsatepoch`. When bypassing TLE strings (using OMM fields directly), `jdsatepoch` must be set manually. This took debugging to discover.

2. **The `orbitcore/` directory shadows the compiled .so.** Python treats any directory as a namespace package, even without `__init__.py`. The `orbitcore/` source directory causes `import orbitcore` to find the directory before the compiled `.so` in `backend/`. Workaround: `sys.path.insert(0, "backend/")` before importing. Long-term fix: install as a proper package.

3. **opsmode matters for deep-space orbits.** For near-Earth satellites (the majority), opsmode `'a'` (AFSPC) and `'i'` (improved) give identical results. For highly eccentric deep-space objects, there can be sub-kilometer differences. AFSPC mode matches what NORAD actually uses operationally — use `'a'`.

4. **Validate against an independent reference, not just your own outputs.** Running our C++ against Vallado's own test vectors would only catch crashes. Cross-validating against the Python `sgp4` library (same algorithm, independent implementation) caught the `jdsatepoch = 0` bug immediately because tsince was wildly wrong.
