# misc/ — Reference Materials

Source documents and reference implementations for OrbitWatch. These are **not our code** — they're external resources we reference during development.

---

## spacetrk/ — Original Spacetrack Report #3 (1980)

**What it is:** The original NORAD/USSPACECOM document defining the SGP4 family of orbit propagation models. Published December 1980 by Hoots & Roehrich.

**Key file:** `spacetrk.pdf`

**What's inside:**
- Mathematical equations for SGP4, SDP4, SGP, SGP8, SDP8
- FORTRAN IV source code for all five models
- Test cases (Section 13) for validating implementations
- WGS-72 gravity constants (kₑ, J₂, J₃, J₄, Earth radius = 6378.135 km)

**Use for OrbitWatch:** Mathematical reference only. Do NOT compile or use the FORTRAN code — it's 1980-era with known bugs (broken Kepler solver, Lyddane discontinuity, single precision, GOTOs everywhere). Use Vallado's corrected C++ implementation instead.

---

## Revisiting Spacetrack Report #3/ — Vallado's Corrected SGP4 (2006, updated through 2017)

**What it is:** AIAA paper 2006-6753 by David Vallado, Paul Crawford, Richard Hujsak, and T.S. Kelso. Identifies and fixes numerous bugs in the original STR#3 code. Provides modernized, validated, non-proprietary SGP4 in 7 languages.

### Papers & Documentation

| File | Purpose |
|------|---------|
| `AIAA-2006-6753-Rev3.pdf` | Full paper (latest revision) — bug analysis, corrections, test results |
| `AIAA-2006-6753-summary.pdf` | Change notes between revisions |
| `sgp4/sgp4_CodeReadme.pdf` | **Critical: build instructions, operational notes, change log (2006–2017)** |

### C++ Source (Our Target)

**Location:** `sgp4/cpp/SGP4/SGP4/`

| File | Size | Purpose |
|------|------|---------|
| `SGP4.cpp` | 110 KB (3,247 lines) | Complete SGP4/SDP4 implementation in namespace `SGP4Funcs` |
| `SGP4.h` | 6.8 KB (232 lines) | Header: `elsetrec` struct (~110 fields), function declarations, `gravconsttype` enum |
| `SGP4TJK.cpp` | 122 KB | Alternate variant (T.S. Kelso's version) — not needed |
| `SGP4TJK.h` | 14 KB | Header for alternate variant |

### Key C++ Functions

```
sgp4init(whichconst, opsmode, satn, epoch, xbstar, xndot, xnddot,
         xecco, xargpo, xinclo, xmo, xno_kozai, xnodeo, satrec)
→ Initializes elsetrec from orbital elements. Call once per satellite.

sgp4(satrec, tsince, r[3], v[3])
→ Propagates to time tsince (minutes from epoch). Returns TEME position/velocity.
   Position in km, velocity in km/s.

getgravconst(whichconst, tumin, mus, radiusearthkm, xke, j2, j3, j4, j3oj2)
→ Loads gravity constants. Three options: wgs72old, wgs72, wgs84.
```

### Test Verification Suite

**Location:** `sgp4/cpp/testsgp4/TestSGP4/`

| File | Purpose |
|------|---------|
| `SGP4-VER.TLE` | 33 test satellites covering near-Earth, deep-space, resonant, GEO, edge cases |
| `*.e` files (33 files) | Expected TEME position/velocity outputs per satellite (STK ephemeris format) |
| `TestSGP4.cpp` | Test driver program showing usage pattern |

**Verification reference output:** `sgp4/mat/tmatverDec2015.out` (MATLAB version — includes orbital elements + calendar dates)

### Build Notes (from sgp4_CodeReadme.pdf)

**Compiler settings (critical for C++):**
1. Don't use pre-compiled headers
2. Don't use stdafx files
3. Don't use assembly.cpp files
4. Don't use CLR support
5. Use the `SGP4Funcs::` namespace convention

**Operational settings:**
- **opsmode = 'a'** (AFSPC mode) — matches how NORAD generates TLEs operationally
- **Constants = WGS-72** — matches NORAD's element fitting. WGS-84 option exists but is not used operationally
- **Julian Date split into jd + jdFrac** — 2016 change for improved time accuracy

### Change History Highlights

| Date | Change |
|------|--------|
| 2017-02-20 | Note: ephemeris_type=2 forces SGP4 over deep-space for extreme orbits |
| 2016-03-09 | Migrated from Borland C++ to MSVS 2013. Added direct variable interface (no TLE text files needed) |
| 2010-08-30 | Performance improvements, alternate GSTime method discussed (cm-level effect) |
| 2008-11-03 | Return codes changed from int to bool. Error details in `satrec.error` |
| 2008-09-03 | Added opsmode ('a'=AFSPC, 'i'=improved) for two modes of operation |
| 2006-08-01 | Original baseline |

### Other Languages Available

`sgp4/for/` (FORTRAN), `sgp4/mat/` (MATLAB), `sgp4/java/` (Java, 2 versions), `sgp4/cs/` (C#), `sgp4/excel/` (Excel)

---

## Key Rules for Using These Materials

1. **TLEs MUST use SGP4** — mean elements are fitted using SGP4's specific mathematical model. Any other propagator produces worse results, even "more accurate" numerical integrators.
2. **Use WGS-72 gravity constants** for SGP4 propagation (matching NORAD). Use WGS-84 for geodetic conversion (Earth's physical shape).
3. **SGP4 output is in TEME frame** — Vallado confirms: "Conversion to other frames is best handled by converting TEME to ECEF (ITRF)."
4. **AFSPC never officially defined TEME→J2000** — from FAQ: "AFSPC has never officially released a method detailing how the TEME coordinate frame is related to other official standard coordinate frames." Our GMST Z-rotation approach (Task 2.2) is the standard workaround.
5. **Leap seconds are not handled by SGP4** — they're an output concern, not part of the propagation math.

---

## External Resources (Not on Disk)

| Resource | URL | Purpose |
|----------|-----|---------|
| CelesTrak GP Data API | `celestrak.org/NORAD/elements/gp.php` | JSON/OMM satellite data (what GPFetcher uses) |
| CelesTrak SATCAT API | `celestrak.org/satcat/records.php` | Satellite catalog: RCS, object type, status, launch info |
| CelesTrak Advanced Queries | `celestrak.org/NORAD/elements/sup-gp.php` | Filtered queries by period, inclination, object type, etc. |
| Space-Track.org | `space-track.org` | CDM (Conjunction Data Messages) for ML training data |
| STR#3 FAQ | `celestrak.org/publications/AIAA/2006-6753/faq.php` | Implementation questions, coordinate frame notes |

### SATCAT API (Useful for Later Phases)

The SATCAT provides metadata not in GP/OMM data:
- **RCS (radar cross-section)** — needed for collision probability calculations
- **Object type** — PAYLOAD, ROCKET BODY, DEBRIS, UNKNOWN
- **Operational status** — active vs. defunct (defunct objects can't maneuver)
- **Country/owner** — for attribution
- **Decay date** — null means still in orbit

Query format: `celestrak.org/satcat/records.php?CATNR=25544&FORMAT=json`
