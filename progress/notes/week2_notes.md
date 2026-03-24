# Week 2 Notes: TLE Data + SGP4 Propagation Research

**Date:** Mar 21–22, 2026
**Focus:** Deep dive into CelesTrak documentation, TLE format, SGP4 theory, and GP data APIs
**Tasks covered:** 2.1 (GP Data Fetcher), 2.2 (Coordinate Transforms) — research underpinning both

---

## 1. NORAD Two-Line Element Set Format (Background for Task 2.1)

**Reference:** https://celestrak.org/NORAD/documentation/tle-fmt.php

### Format Overview

TLE is a fixed-width, 80-character-per-line format dating from the punch-card era. It consists of:

- **Line 0** (optional): Satellite name (up to 24 characters)
- **Line 1**: Identification + epoch + drag terms
- **Line 2**: Orbital elements

### Line 1 Layout

```
1 NNNNNC NNNNNAAA NNNNN.NNNNNNNN +.NNNNNNNN +NNNNN-N +NNNNN-N N NNNNN
```

| Column | Field | Description |
|--------|-------|-------------|
| 01 | Line number | Always "1" |
| 03–07 | Catalog number | NORAD catalog ID (max 99999 in legacy format) |
| 08 | Classification | U = Unclassified, C = Classified, S = Secret |
| 10–11 | Launch year | Last 2 digits (57–99 = 1900s, 00–56 = 2000s) |
| 12–14 | Launch number | Sequential launch number that year |
| 15–17 | Launch piece | Piece of launch (A, B, C...) |
| 19–20 | Epoch year | Last 2 digits of year |
| 21–32 | Epoch day | Day of year + fractional day (e.g., 083.54321) |
| 34–43 | Mean motion dot | 1st derivative of mean motion / 2 (rev/day²) |
| 45–52 | Mean motion ddot | 2nd derivative of mean motion / 6 (implied decimal) |
| 54–61 | BSTAR | Drag term (implied decimal, exponential notation) |
| 63 | Ephemeris type | 0 = SGP4 (always 0 for distributed TLEs) |
| 65–68 | Element set number | Incremented when new TLE generated |
| 69 | Checksum | Modulo 10 checksum |

### Line 2 Layout

```
2 NNNNN NNN.NNNN NNN.NNNN NNNNNNN NNN.NNNN NNN.NNNN NN.NNNNNNNNNNNNNN
```

| Column | Field | Description |
|--------|-------|-------------|
| 01 | Line number | Always "2" |
| 03–07 | Catalog number | Must match Line 1 |
| 09–16 | Inclination | Degrees (0–180) |
| 18–25 | RAAN | Right Ascension of Ascending Node (degrees) |
| 27–33 | Eccentricity | **Implied leading decimal** (e.g., 0007976 = 0.0007976) |
| 35–42 | Arg of perigee | Argument of perigee (degrees) |
| 44–51 | Mean anomaly | Degrees |
| 53–63 | Mean motion | Revolutions per day |
| 64–68 | Revolution number | Rev count at epoch |
| 69 | Checksum | Modulo 10 checksum |

### Special Encoding Rules

- **Implied decimal point**: Eccentricity has no leading "0." — it's implied (e.g., `0007976` = 0.0007976)
- **Exponential notation**: BSTAR and mean motion ddot use a compact form: `+12345-6` means `0.12345 × 10⁻⁶`
- **Checksum**: Sum of all digits on the line, treating `-` as 1 and ignoring all other non-digit characters. Result mod 10.
- **Alpha-5 catalog numbers**: To exceed 99999, the first digit can be a letter (A=10, B=11, ... Z=35), giving range up to ~339999. This is a stopgap — JSON/OMM is the real solution.

### Format Limitations (Why We Use JSON Instead)

- 5-digit catalog number cap (~99999, hit around July 2026)
- Fixed-width truncation loses numeric precision
- No ISO 8601 dates (uses 2-digit year + fractional day of year)
- Y2K-style ambiguity: year 57–99 = 1900s, 00–56 = 2000s
- No metadata fields (object type, country, decay status, etc.)
- **CelesTrak recommends migrating to JSON/OMM for new development**

---

## 2. Spacetrack Report Number 3 (STR#3) — Background for Tasks 2.2–2.3

**Reference:** `misc/spacetrk/spacetrk.pdf` (Hoots & Roehrich, 1980)

### What It Is

The definitive NORAD/USSPACECOM document defining the SGP4 family of orbit propagation models. Published December 1980, it provides both mathematical equations and FORTRAN IV source code for five analytical propagation models.

### The Five Models

| Model | Domain | Period | Notes |
|-------|--------|--------|-------|
| **SGP** | Near-Earth | < 225 min | Simplified Kozai gravity model. Obsolete. |
| **SGP4** | Near-Earth | < 225 min | Brouwer gravity + power density atmosphere. **Current NORAD standard.** |
| **SDP4** | Deep-Space | ≥ 225 min | SGP4 + lunar/solar perturbations. **Current NORAD standard.** |
| **SGP8** | Near-Earth | < 225 min | Different integration approach. Uses B coefficient instead of B*. |
| **SDP8** | Deep-Space | ≥ 225 min | SGP8 + deep-space terms. |

**For OrbitWatch: Use SGP4 (near-Earth) / SDP4 (deep-space).** Modern implementations merge both under the name "SGP4."

### The Critical Rule: TLEs MUST Use SGP4

This is the single most important takeaway:

> *"The NORAD element sets **must** be used with one of the models described in this report in order to retain maximum prediction accuracy."*

**Why:** TLE orbital elements are **mean elements**, not osculating (actual) elements. NORAD removes periodic variations using the specific SGP4/SDP4 mathematical model when fitting the elements. To get an accurate prediction, you must **reconstruct those periodic variations using the exact same model**.

Using TLEs with a different propagator (even a "more accurate" numerical integrator) produces **worse** results because:
- The different model treats mean elements as osculating → double-counts some perturbations, misses others
- Analogous to running a JPEG decoder on a PNG file — encoding and decoding must match

### SGP4 Propagation Pipeline

1. Start with mean elements from TLE
2. Recover original mean motion (n₀'') and semimajor axis (a₀'')
3. Apply secular effects (drag + J2/J3/J4 gravity) to evolve elements forward
4. Add long-period periodic corrections (J3 term)
5. Solve Kepler's equation iteratively (mean anomaly → eccentric anomaly → true anomaly)
6. Add short-period periodic corrections (J2 term)
7. Convert to position/velocity vectors in TEME coordinate frame

### Key Constants (WGS-72, used by NORAD)

- kₑ = √(GM) = 0.0743669161 (er³ᐟ² / min)
- J₂ = 1.08263 × 10⁻³
- J₃ = -2.53881 × 10⁻⁶
- Earth radius = 6378.135 km
- Internal units: Earth radii (distance), minutes (time)

### Low-Perigee Edge Cases

SGP4 has special handling for decaying/low-orbit satellites:
- Perigee < 220 km: Simplified drag equations
- Perigee < 156 km: Adjusted atmospheric density parameters (s parameter)
- Perigee < 98 km: Further density adjustments (s capped at 20 km above Earth)

### Fortran Code in `misc/spacetrk/`

| File | Purpose |
|------|---------|
| `SGP4.FOR` | SGP4 model subroutine |
| `SDP4.FOR` | SDP4 deep-space model |
| `DEEP.FOR` | Deep-space perturbations subroutine |
| `DRIVER.FOR` | Main program (reads TLEs, calls models) |
| `ACTAN.FOR`, `FMOD2P.FOR`, `THETAG.FOR` | Helper functions |
| `SGP.FOR`, `SGP8.FOR`, `SDP8.FOR` | Other models (not needed) |

**Verdict: Valuable as mathematical reference, but do NOT compile or use directly.** It's 1980 FORTRAN IV with archaic constructs. Use Vallado's modernized C++ implementation instead. The test cases (Section 13) are useful for validating our implementation.

---

## 3. Revisiting Spacetrack Report #3 (AIAA 2006-6753) — Reference for Task 2.3

**Reference:** `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-Rev3.pdf`
**Authors:** David Vallado, Paul Crawford, Richard Hujsak, T.S. Kelso

### What It Is

A 2006 paper (revised through Rev3) that identifies and fixes numerous bugs in the original STR#3 code. Provides a modernized, validated, non-proprietary SGP4 implementation in 7 programming languages including **C++** (our target).

### Problems Found in Original STR#3 Code

1. **Kepler equation solver broken** for high-eccentricity orbits — could infinite-loop or fail to converge
2. **Lyddane bug** — discontinuity in lunar-solar perturbation calculations at certain angles, causing position jumps
3. **Lyddane choice problem** — method switching during propagation based on inclination changes, causing large discontinuities
4. **Choppy deep-space ephemerides** — lunar-solar terms only recomputed every 30 minutes
5. **Negative inclination handling** — GEO satellites could develop negative inclinations from lunar/solar gravity, causing step-function position errors
6. **Backwards propagation broken** — secular integrator only worked with increasing time steps
7. **Mixed single/double precision** — limited accuracy in original FORTRAN
8. **Implicit typing + GOTOs** — made debugging nearly impossible

### Corrections Made

- **Merged SGP4 + SDP4** into single unified "SGP4" codebase
- **Double precision throughout**
- **Kepler solver fixed** with realistic iteration controls
- **30-minute recomputation interval removed** — deep-space terms computed every call
- **Lyddane bug partially fixed** with proper atan2 quadrant handling
- **Integrator fixed** — always integrates from epoch to target time (repeatable, direction-independent)
- **Initialization separated from propagation** — `SGP4init()` runs once, `SGP4()` called repeatedly
- **Structures replace COMMON blocks** — proper data encapsulation
- **GOTOs eliminated** — modern control flow
- **Both WGS-72 and WGS-84 constants** provided (WGS-72 default for AFSPC compatibility)
- All changes marked with `"sgp4fix"` comment keyword for easy identification

### Relationship to DoD Code

- The true operational SGP4 is classified, embedded in SPADOC at Cheyenne Mountain
- This paper's code is a **non-proprietary best-effort reconstruction** from public sources
- Full-catalog testing (~9000 satellites) showed sub-meter agreement with GSFC version
- Original STR#3 code showed errors of **10m to 100km** for deep-space objects

### Source Code Available (7 Languages)

Located in `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/`:
- **C++** (`cpp/`): `sgp4unit.cpp/.h`, `sgp4io.cpp/.h`, `sgp4ext.cpp/.h`, `testcpp.cpp`
- **FORTRAN** (`for/`), **MATLAB** (`mat/`), **Pascal** (`pas/`), **Java** (`java/`), **C#** (`cs/`), **Excel** (`excel/`)

**The C++ implementation is our reference for building OrbitWatch's propagation engine.**

### Test Cases

Comprehensive test suite in `sgp4-ver.tle` covering:

| Category | Satellites | Tests |
|----------|-----------|-------|
| Near-Earth | 00005, 88888 | Basic propagation |
| Deep-Space | 28129 (GPS), 11801 | 12h+ period orbits |
| Resonant | 26975, 08195, 09880, 21897, 22674 | Molniya orbits, various eccentricities |
| Synchronous | 28626, 25954, 24208, 09998 | GEO, low-inclination edge cases |
| Edge Cases | 23333 (high-e), 28623/16925 (low perigee), 29141 (decaying) | Error handling |

### Coordinate System: TEME (Resolved in Task 2.2)

SGP4 output is in **TEME** (True Equator, Mean Equinox) — an approximate frame not known by SPICE. Must convert:
- TEME → ECEF via GMST Z-axis rotation (single matrix multiply)
- ECEF → geodetic via SPICE `recgeo()` with WGS-84 ellipsoid

**Originally planned:** TEME → J2000 → ITRF93 → geodetic (via SPICE full pipeline).
**What actually happened:** SPICE doesn't know TEME (`UNKNOWNFRAME` error). The GMST rotation approach is simpler, sufficient within SGP4's ~1 km accuracy, and avoids precession/nutation matrix complexity. See `progress/task_logs/task_2_2_coordinate_transforms.md` for the full decision analysis.

---

## 4. CelesTrak GP Data API (Reference for Task 2.1)

**Reference:** https://celestrak.org/NORAD/documentation/gp-data-formats.php

### Why GP Data Instead of TLEs

- TLE format is fixed-width, limited precision, capped at 99999 catalog numbers
- GP (General Perturbations) data is the same orbital information in modern formats
- CelesTrak recommends JSON/OMM for new development
- JSON gives full numeric precision with ISO 8601 dates

### Available Formats

| Format | Content-Type | Best For |
|--------|-------------|----------|
| **JSON** | application/json | Programmatic access (what we use) |
| **XML** | application/xml | OMM standard (CCSDS 502.0-B-3) |
| **CSV** | text/csv | Pandas/spreadsheets |
| **KVN** | text/plain | OMM Key-Value Notation |
| **TLE** | text/plain | Legacy compatibility |
| **3LE** | text/plain | Legacy + name line |

### Simple API (`gp.php`)

```
https://celestrak.org/NORAD/elements/gp.php?{params}&FORMAT={format}
```

| Parameter | Example | Description |
|-----------|---------|-------------|
| `GROUP=` | `stations` | Predefined group |
| `CATNR=` | `25544` | NORAD catalog number |
| `INTDES=` | `1998-067A` | International designator |
| `NAME=` | `ISS` | Satellite name |
| `FORMAT=` | `json` | Output format |

**Groups we use:** `stations` (Phase 1), `visual` (Phase 2), `starlink` (Phase 3), `active` (Phase 4)

### Advanced API (`sup-gp.php`)

```
https://celestrak.org/NORAD/elements/sup-gp.php?{params}
```

Additional filtering parameters:

| Parameter | Example | Description |
|-----------|---------|-------------|
| `EPOCH=` | `>now-7` | Filter by epoch (relative dates supported) |
| `OBJECT_TYPE=` | `PAYLOAD` | PAYLOAD, ROCKET BODY, DEBRIS, UNKNOWN |
| `COUNTRY=` | `US` | Country/org code |
| `PERIOD=` | `90--100` | Orbital period range (minutes) |
| `INCLINATION=` | `51--52` | Inclination range (degrees) |
| `APOGEE=` / `PERIGEE=` | `400--420` | Altitude range (km) |
| `DECAY_DATE=` | `null` | `null` = still in orbit |
| `ORDERBY=` | `EPOCH DESC` | Sort order |
| `LIMIT=` | `100` | Max results |

**Range syntax:** `=90` (exact), `=>90` (greater), `=<90` (less), `=90--100` (range)

**Useful example queries:**
```
# ISS TLE
sup-gp.php?CATNR=25544&FORMAT=TLE

# LEO payloads launched 2023
sup-gp.php?OBJECT_TYPE=PAYLOAD&PERIOD=<225&LAUNCH_YEAR=2023&FORMAT=csv

# All Cosmos-1408 ASAT debris
sup-gp.php?INTDES=1982-092&OBJECT_TYPE=DEBRIS&FORMAT=json

# GEO satellites
sup-gp.php?PERIOD=1430--1440&INCLINATION=<1&ECCENTRICITY=<0.01&FORMAT=json

# Fresh data (last 24 hours)
sup-gp.php?EPOCH=>now-1&FORMAT=json
```

### Rate Limiting & Best Practices

- Data updates every ~2 hours — don't fetch more often
- 100 MB/day bandwidth cap
- No authentication required (unlike Space-Track)
- **Do NOT retry on 403/404** — CelesTrak will IP-block aggressive fetchers
- Cache locally to Parquet (already implemented in our GPFetcher)

---

## 5. Key JSON/OMM Fields — Task 2.1 Input Schema

| Field | SGP4 Input? | Description |
|-------|-------------|-------------|
| `OBJECT_NAME` | No | Satellite name |
| `OBJECT_ID` | No | International designator |
| `NORAD_CAT_ID` | No | Catalog number (supports > 99999) |
| `EPOCH` | Yes | ISO 8601 epoch datetime |
| `MEAN_MOTION` | Yes | Revolutions per day |
| `ECCENTRICITY` | Yes | Orbital eccentricity |
| `INCLINATION` | Yes | Degrees |
| `RA_OF_ASC_NODE` | Yes | RAAN (degrees) |
| `ARG_OF_PERICENTER` | Yes | Argument of perigee (degrees) |
| `MEAN_ANOMALY` | Yes | Degrees |
| `BSTAR` | Yes | Drag term (1/Earth radii) |
| `MEAN_MOTION_DOT` | Yes | 1st deriv of mean motion |
| `MEAN_MOTION_DDOT` | Yes | 2nd deriv of mean motion |
| `EPHEMERIS_TYPE` | Yes | 0 = SGP4 |
| `ELEMENT_SET_NO` | No | Element set number |
| `REV_AT_EPOCH` | No | Revolution count at epoch |
| `CLASSIFICATION_TYPE` | No | U = unclassified |

---

## 6. Task 2.1 Audit: GPFetcher Hardening (Mar 21)

After the initial implementation, audited the fetcher for collision-prediction readiness:

### Issues Found & Fixed
1. **One bad record killed entire batch** — malformed CelesTrak record with missing field would crash `_parse_json` with KeyError, losing all 6000+ Starlink records. Fixed: per-record try/except, skip and log bad records.
2. **Division by zero** — `mean_motion = 0` would crash `_derive_orbit_params`. Fixed: validate `mean_motion > 0` and `0 <= eccentricity < 1` before processing.
3. **Empty response overwrote good cache** — CelesTrak sometimes returns `[]` during data refresh windows. Previously this saved an empty Parquet, destroying cached data. Fixed: guard against empty results, fall back to existing cache.
4. **Dead code in `_download`** — `urlopen` already raises `HTTPError` for non-2xx, so manual status check was unreachable. Removed.
5. **Non-atomic cache writes** — process kill mid-write corrupted Parquet file. Fixed: write to temp file, then atomic rename.
6. **No epoch staleness tracking** — added `epoch_age_days` column. SGP4 error grows ~5-10 km/day from epoch. Stale TLEs produce unreliable conjunction predictions.
7. **No filtering of unusable objects** — decayed satellites (re-entered) and non-SGP4 ephemeris types now skipped during parse.

### Fields Available per Endpoint
| Field Category | `gp.php` | `sup-gp.php` | Space-Track |
|----------------|----------|--------------|-------------|
| Core OMM (17 fields) | Yes | Yes | Yes |
| Period/Apoapsis/Periapsis | **Computed** | **Computed** | Provided |
| Object type/RCS size | No | No | Yes |
| Country code | No | No | Yes |
| Epoch staleness | **Computed** | **Computed** | **Computed** |

---

## 7. Implications for OrbitWatch (Updated Mar 23)

### What We're Using
- **Our own C++ SGP4 via pybind11** (`import orbitcore`) — wraps Vallado's validated 2020 implementation (Task 2.3 DONE)
- **JSON/OMM format** via CelesTrak `gp.php` API — implemented in `GPFetcher` (Task 2.1 DONE)
- **GMST Z-rotation** for TEME→ECEF, **SPICE `recgeo()`** for ECEF→geodetic (Task 2.2 DONE)

### Coordinate Transform Pipeline (RESOLVED — Task 2.2)
**Original plan:** TEME → J2000 → ITRF93 → geodetic (full SPICE pipeline)
**What we actually built:** TEME → ECEF (GMST rotation) → geodetic (SPICE `recgeo()`)

**Why the change:** SPICE doesn't know the TEME frame (`UNKNOWNFRAME` error). Three options were evaluated:
1. SPICE full pipeline (TEME→J2000→ITRF93) — requires precession/nutation matrices, two complex steps
2. Astropy (native TEME support) — 200MB dependency for one rotation
3. GMST Z-rotation — **chosen**: single matrix multiply, skips polar motion (~10m) and equation of equinoxes (~30m), both well within SGP4's ~1 km accuracy

Validated with 5 real satellites (ISS, CSS, FREGAT DEB, HTV-X1, CREW DRAGON). 26/26 tests passing.

### Architecture Decision: Custom C++ Wrapping (Task 2.3 — RESOLVED)

**Two options were evaluated:**
1. **Wrap Vallado's C++ via pybind11** — own the propagation engine, portfolio value, tighter C++ integration for conjunction scanning
2. **Use Python `sgp4` library** — already wraps same code, less work, reserve C++ for conjunction scanning only

**Chose Option 1.** Rationale:
- Portfolio signal: demonstrates C++/pybind11 with real aerospace-grade code to employers
- Performance: conjunction scanner (Week 6) can call SGP4 directly in C++ without Python/C++ boundary overhead per satellite per timestep
- Full control: we own the propagation engine, can extend it for batch propagation later

**What was built:** Vallado's `SGP4.cpp` (3,247 lines, namespace `SGP4Funcs`) wrapped via pybind11 bindings in `orbitcore/src/bindings.cpp`. Exposes `sgp4init()`, `sgp4()`, `jday()`, `invjday()`, `getgravconst()`, `Satrec` class, `GravConst` enum.

**Key finding during wrapping:** `sgp4init()` does NOT set `jdsatepoch`/`jdsatepochF` — only `twoline2rv()` does. Since we bypass TLE string parsing (we init from OMM fields directly), we back-compute the Julian Date in our binding layer.

### Validation Results (Task 2.3)
- **32/33 Vallado test satellites** match Python `sgp4` library to sub-micrometer (< 1 nm)
- 1 satellite (23599, deep-space e=0.714) differed by 0.9 km: opsmode='a' (AFSPC) vs 'i' (improved). Confirmed identical at same opsmode — not a bug
- ISS at epoch: altitude 409 km, speed 7.67 km/s
- End-to-end: C++ SGP4 → coordinate transforms → ISS lat/lon/alt verified
- 54/54 tests passing

### Next Step: Propagator Wrapper (Task 2.4)
- `propagator.py` orchestrates: GPFetcher cache → unit conversion → `orbitcore.sgp4init()` → `orbitcore.sgp4()` → `teme_to_geodetic()`
- Key conversions: degrees→radians, rev/day→rad/min, ISO 8601→Julian Date→epoch days
- Must handle all 30 Phase 1 stations without error

---

## 8. Sources & References

| Resource | Location | Purpose |
|----------|----------|---------|
| TLE Format Spec | https://celestrak.org/NORAD/documentation/tle-fmt.php | Field definitions |
| Spacetrack Report #3 | `misc/spacetrk/spacetrk.pdf` | Original SGP4 math + FORTRAN |
| Revisiting STR#3 | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-Rev3.pdf` | Corrected SGP4 + C++ code |
| STR#3 Rev Summary | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-summary.pdf` | Change notes |
| Vallado C++ Source | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/` | Reference implementation |
| GP Data Formats | https://celestrak.org/NORAD/documentation/gp-data-formats.php | JSON/OMM API docs |
| SupGP Queries | https://celestrak.org/NORAD/documentation/sup-gp-queries.php | Advanced query API |
| STR#3 FAQ | https://celestrak.org/publications/AIAA/2006-6753/faq.php | Common questions |
