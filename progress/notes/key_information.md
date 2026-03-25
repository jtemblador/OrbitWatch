# Key Information & Actionable Findings

**Purpose:** Quick-reference list of critical facts, decisions, and gotchas discovered during research. Consult this before and during each task to avoid mistakes.

---

## Critical Rules

1. **TLE mean elements MUST be propagated with SGP4/SDP4 — no exceptions.** Using TLEs with any other propagator (numerical integrator, different analytical model) gives WORSE results, not better. The encoding (element fitting) and decoding (propagation) are mathematically coupled. (Source: STR#3, p.1)

2. **SGP4 outputs TEME coordinates, not J2000 or ECEF.** TEME (True Equator Mean Equinox) is an approximate frame that doesn't rotate with Earth and isn't aligned to a standard inertial axis. Must convert before use.

3. **SPICE does NOT know the TEME frame.** Tested: `sp.pxform('TEME', 'J2000', et)` → `SPICE(UNKNOWNFRAME)`. We handle TEME→ECEF ourselves via GMST rotation, then hand off to SPICE for ECEF→geodetic only.

4. **Use WGS-72 constants for SGP4, WGS-84 for geodetic.** Different purposes:
   - WGS-72: gravity model used by NORAD when fitting TLE elements → must match for SGP4
   - WGS-84: Earth's physical shape → used for ECEF→lat/lon/alt conversion (same as GPS)

5. **CelesTrak rate limiting is strict.** Data updates every 2 hours max. Do NOT retry on 403/404 — they will IP-block. 100 MB/day bandwidth cap. Our GPFetcher already enforces the 2-hour cache interval.

6. **Near-Earth vs Deep-Space split at 225 minutes orbital period.** Period < 225 min = SGP4 (near-Earth). Period >= 225 min = SDP4 (deep-space, adds lunar/solar perturbations). Modern implementations merge both under "SGP4" automatically. The Python `sgp4` library handles this transparently.

---

## Coordinate Transform Pipeline (RESOLVED)

**What we originally planned:**
```
SGP4 (TEME) → precession/nutation → J2000 → SPICE → ITRF93 → geodetic
```

**What we actually built (simpler, sufficient accuracy):**
```
SGP4 (TEME) → GMST Z-rotation → ECEF → SPICE recgeo → geodetic
```

**Why this works:** TEME and ECEF share the same Z-axis (Earth's pole). The only difference is Earth's spin angle (GMST). One matrix multiply converts between them. The original plan required precession + nutation + Earth rotation — three steps with more error surface. Going TEME→ECEF directly is one step.

**What we skip (and why it's fine):**
- Polar motion corrections: ~10m error (SGP4 is ~1 km)
- Equation of equinoxes (GMST→GAST): ~30m error (SGP4 is ~1 km)
- Both corrections are dwarfed by SGP4's inherent accuracy limit

**Velocity transform:** Includes the ω×r correction (Earth's angular velocity × position). Without this, ECEF velocities would be wrong by ~0.46 km/s at LEO.

**Implemented in:** `backend/core/coordinate_transforms.py`
**Tested with:** ISS, CSS Tianhe, FREGAT DEB (eccentric), HTV-X1, Crew Dragon — all 30/30 stations pass

---

## Resources On Disk

| What | Path | Use For |
|------|------|---------|
| **Vallado's C++ SGP4** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/` | Reference implementation for our C++ propagation engine (Task 2.3) |
| **SGP4 test cases** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/` (sgp4-ver.tle) | Validation test suite — input TLEs + expected output positions |
| **Original STR#3 PDF** | `misc/spacetrk/spacetrk.pdf` | Mathematical reference for SGP4 equations |
| **Original FORTRAN** | `misc/spacetrk/SGP4.FOR`, `SDP4.FOR`, `DEEP.FOR` | Math reference only — do NOT compile (1980 FORTRAN IV) |
| **Revisiting STR#3 paper** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-Rev3.pdf` | Bug fixes, corrections, technical details |
| **STR#3 summary** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-summary.pdf` | Change notes per language, build instructions |
| **SPICE kernels** | `backend/data/spice_kernels/` | naif0012.tls, pck00011.tpc, earth_latest_high_prec.bpc |

---

## Vallado C++ Source (Wrapped in Task 2.3)

**Source:** `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/SGP4/SGP4/SGP4.cpp` (3,247 lines)
**Wrapped into:** `orbitcore/src/SGP4.cpp` + `orbitcore/include/SGP4.h`
**Bindings:** `orbitcore/src/bindings.cpp` (pybind11)

The consolidated `SGP4.cpp` contains everything (sgp4unit + sgp4ext + sgp4io merged into one file under `SGP4Funcs` namespace). We do NOT use `twoline2rv()` — we init from OMM fields directly via `sgp4init()`.

Key functions exposed to Python:
- `orbitcore.sgp4init(whichconst, opsmode, satnum, epoch, bstar, ndot, nddot, ecco, argpo, inclo, mo, no_kozai, nodeo)` → `Satrec`
- `orbitcore.sgp4(satrec, tsince)` → `((x,y,z), (vx,vy,vz))` in TEME (km, km/s)
- `orbitcore.jday(yr,mo,dy,hr,mn,sec)` → `(jd, jdFrac)`
- `orbitcore.getgravconst(GravConst.WGS72)` → dict of gravity constants

All bug fixes in the code are marked with the comment keyword **`sgp4fix`** — search for it to see every correction vs original STR#3.

---

## Python sgp4 Library (Already Installed)

The `sgp4` Python library by Brandon Rhodes wraps Vallado's C implementation. Key usage:

```python
from sgp4.api import Satrec, WGS72

# Initialize from OMM fields (no TLE string parsing needed!)
sat = Satrec()
sat.sgp4init(
    WGS72,              # gravity model (use WGS72, not WGS84)
    'i',                # improved mode ('i') vs afspc mode ('a')
    norad_cat_id,       # NORAD catalog number
    epoch_jd - 2433281.5,  # epoch in days since 1949 Dec 31
    bstar,              # drag term
    mean_motion_dot / (1440.0 * 2),  # convert to internal units
    0.0,                # mean_motion_ddot (not used)
    eccentricity,
    arg_of_pericenter * (pi/180),  # radians!
    inclination * (pi/180),         # radians!
    mean_anomaly * (pi/180),        # radians!
    mean_motion * (2*pi/1440),      # rad/min (convert from rev/day)
    ra_of_asc_node * (pi/180),      # radians!
)

# Propagate to a Julian date
e, r, v = sat.sgp4(jd_whole, jd_fraction)
# e = error code (0 = success)
# r = (x, y, z) position in km (TEME frame!)
# v = (vx, vy, vz) velocity in km/s (TEME frame!)
```

**Important:** All angular inputs to `sgp4init()` must be in **radians**. Our JSON data is in **degrees**. Must convert.

**mean_motion_dot conversion:** CelesTrak gives rev/day². The sgp4 library expects it divided by `(1440 * 2)` — this is the "ndot over 2" convention from the TLE format.

---

## Known Bugs We Must Avoid

These were fixed in Vallado's code but are present in many online SGP4 implementations:

1. **Kepler solver infinite loop** — high-eccentricity orbits can fail to converge. Vallado's code has iteration limits.
2. **Lyddane discontinuity** — position jumps at certain angles in deep-space orbits. Fixed with proper atan2 handling.
3. **Negative inclination at GEO** — low-inclination GEO satellites can get negative inclination from lunar/solar perturbations, causing position step-functions.
4. **Backwards propagation breaks** — original integrator only worked with increasing time. Vallado's code restarts from epoch each call.

**These are all fixed in the Python `sgp4` library and Vallado's C++ code.** Only relevant if we write our own implementation from scratch (which we should NOT do).

---

## SGP4 Accuracy Expectations

| Time from Epoch | Expected Error |
|-----------------|---------------|
| At epoch        | ~1 km         |
| 1 day           | ~5-10 km      |
| 3 days          | ~15-30 km     |
| 7 days          | ~50-100+ km   |

**Use the freshest TLE available.** CelesTrak updates every ~2 hours. Our 2-hour cache interval is correct.

For conjunction screening, accuracy matters most at close approach time. Always re-fetch TLEs before critical calculations.

---

## Data Quality & Validation (Implemented in GPFetcher)

**Records are skipped (not crashed) if:**
- `MEAN_MOTION <= 0` — physically impossible, would cause division by zero
- `ECCENTRICITY < 0` or `>= 1` — not a valid orbit (parabolic/hyperbolic)
- `EPHEMERIS_TYPE != 0` — non-SGP4 elements, incompatible with our propagator
- `DECAYED == 1` — re-entered objects produce underground positions
- Missing required fields (KeyError) — malformed CelesTrak record

**Epoch staleness (`epoch_age_days`):**
- Computed at fetch time: `(now - epoch).total_seconds() / 86400`
- Downstream code should flag objects with epoch_age > 3-5 days as unreliable
- For conjunction screening, always re-fetch before computing miss distances

**Cache safety:**
- Empty CelesTrak responses do NOT overwrite valid cached data
- Parquet writes are atomic (write to temp file, then rename)
- `fetch_time` column stored in UTC for consistent freshness checks

---

## JSON vs TLE: What We Chose and Why

| Concern | TLE | JSON/OMM |
|---------|-----|----------|
| Catalog numbers > 99999 | Breaks (~July 2026) | Supported |
| Numeric precision | Fixed-width truncation | Full floating point |
| Date format | 2-digit year + fractional day | ISO 8601 |
| Parsing complexity | Column-position dependent | Standard JSON |
| SGP4 compatibility | Direct input | Extract fields → `sgp4init()` |
| CelesTrak recommendation | Legacy | **Recommended for new code** |

**We use JSON.** Already implemented in `backend/core/tle_fetcher.py` (`GPFetcher` class).

---

## API Quick Reference

### Simple fetch (what we use now)
```
GET https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=json
```

### Single satellite by catalog number
```
GET https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json
```

### Advanced queries (useful for Phase 3-4 filtering)
```
GET https://celestrak.org/NORAD/elements/sup-gp.php?OBJECT_TYPE=PAYLOAD&PERIOD=<225&FORMAT=json
GET https://celestrak.org/NORAD/elements/sup-gp.php?EPOCH=>now-1&FORMAT=json
GET https://celestrak.org/NORAD/elements/sup-gp.php?CATNR=25544&FORMAT=TLE
```

No authentication needed for CelesTrak. Space-Track requires login (for CDM conjunction data in later weeks).

---

## FastAPI Backend (Week 3)

**TestClient lifespan gotcha:** `TestClient(app)` at module level does NOT trigger lifespan events. Must enter context manager (`__enter__()`) for `app.state` to be populated. This affects any test that accesses endpoints depending on lifespan-initialized state.

**gp.php omits `OBJECT_TYPE`:** The simple `gp.php` endpoint does not return the `OBJECT_TYPE` field for any satellite. Only `sup-gp.php` or Space-Track provide it. API defaults to `"UNKNOWN"`.

**Stations group includes debris:** CelesTrak's "stations" group is not limited to crewed LEO stations. Includes rocket bodies and debris (e.g., `FREGAT DEB` at 2263 km apoapsis). Do not assume tight LEO altitude bounds for the entire group.

**`epoch_age_days` recomputation:** The value cached in Parquet is computed at fetch time and goes stale. API endpoints recompute it from `utcnow()` on each request.

**Scaling tracker:** `progress/scaling_tracker.md` centrally tracks all `iterrows()` and other Phase 3 performance items. Add entries there whenever flagging code with `# ⚠ PERF`.

**`+` in query strings decoded as space:** `?time=2026-03-24T12:00:00+00:00` breaks because `+` becomes ` `. Use `Z` suffix for UTC or `%2B` URL encoding. This affects any endpoint accepting ISO 8601 time params.

**Propagator `RuntimeError` must be caught at API layer:** Any endpoint calling propagator methods (`get_position_by_norad_id`, `get_positions_at_times`) must catch `RuntimeError` — SGP4 propagation can fail for decayed orbits. Batch endpoint handles this internally via `get_all_positions()` error collection; single and track endpoints need explicit try/except.

**`get_all_positions()` returns `(results, errors)` tuple:** Changed from returning just a list. Callers must unpack: `results, errors = propagator.get_all_positions(utc_dt)`.

**Fetch/serve separation:** GET endpoints always serve from local Parquet cache. Only `POST /api/refresh` triggers a CelesTrak fetch. No client request in the GET path ever directly contacts CelesTrak. Phase 3 will move the fetch to a background task (202 Accepted) + scheduled auto-refresh.

---

## Cesium.js Frontend (Week 4)

**StaticFiles catch-all changes HTTP status codes:** Mounting `StaticFiles(directory="frontend", html=True)` at `/` means undefined routes return 404 from the static mount instead of 405 from FastAPI's router. This affects any test asserting 405 Method Not Allowed. Accept both 404 and 405 in tests.

**Use `PointPrimitiveCollection`, NOT Entity API:** Cesium Entity API has per-object overhead (picking, labels, property evaluation). AstriaGraph uses Entity + CallbackProperty for 17K objects and is laggy. `PointPrimitiveCollection` batches all points into a single GPU draw call. trackthesky.com uses this pattern for 9K+ satellites successfully.

**Cesium Ion token is client-side:** Unlike backend `.env` secrets, the Ion token is embedded in frontend JS (same as Google Maps API keys). Restrict by domain in Ion dashboard for production. Stored in gitignored `config.js` with committed `config.example.js` template.

**UHD 620 performance settings:** `terrain: undefined` (ellipsoid only), `resolutionScale: 1.0`, all default UI widgets disabled. These are the three biggest GPU savers for integrated graphics.

**Cesium label `FILL_AND_OUTLINE` causes distortion.** Text outline rasterization on label textures produces artifacts at oblique angles. Use `FILL` style with `showBackground: true` (translucent dark) for clean rendering. Also: `disableDepthTestDistance: Number.POSITIVE_INFINITY` defeats globe occlusion — remove it so labels behind Earth are hidden.

**`Cartesian3.fromDegrees(lon, lat, height)` — height is in meters.** API returns `alt_km`. Must multiply by 1000. Longitude is the first argument (not latitude).

**Cesium position setter copies the value.** A scratch `Cartesian3` can be reused across all primitives in a loop — Cesium copies on assignment, doesn't store the reference. Safe for lerp loops with a single scratch object.

---

## Task Checklist

### Task 2.1 (GP Data Fetcher) — DONE
- GPFetcher implemented with JSON/OMM format
- Caching, rate limiting, error handling all in place
- Data validation: skips malformed records, decayed objects, non-SGP4 ephemeris types
- Derived orbital params computed: period, semimajor_axis, apoapsis, periapsis
- Epoch staleness tracked (`epoch_age_days`)
- Atomic cache writes, empty response guard
- 37/37 tests passing

### Task 2.2 (Coordinate Transforms) — DONE
- SPICE TEME support tested → NOT available (UNKNOWNFRAME)
- GMST Z-rotation approach implemented: TEME → ECEF → geodetic
- Velocity transform includes ω×r Earth rotation correction
- Tested with 5 diverse satellites (LEO, eccentric, different inclinations)
- ISS ground track verified over 7 days — lat bounded by inclination, alt stable
- 26/26 tests passing

### Task 2.3 (C++ SGP4 Engine) — DONE
- Wrapped Vallado's `SGP4.cpp` (3,247 lines) via pybind11 into `orbitcore/`
- Chose Option A (own C++ wrapper) over Option B (Python sgp4 library) for portfolio value + conjunction scanner integration
- Exposes: `sgp4init()`, `sgp4()`, `jday()`, `invjday()`, `getgravconst()`, `Satrec` class, `GravConst` enum
- Used WGS-72 constants, AFSPC opsmode
- Back-computes `jdsatepoch` from epoch parameter (Vallado's `sgp4init` doesn't set it — only `twoline2rv` does)
- Validated: 32/33 Vallado test sats match Python sgp4 to sub-micrometer
- 54/54 tests passing (including end-to-end C++ SGP4 → coordinate transforms → geodetic)

### Task 2.4 (Propagator Wrapper) — DONE
- Full pipeline: GPFetcher → unit conversion → C++ SGP4 → coordinate transforms → result dict
- 80/80 tests passing
- Cross-validated all 30 stations against Python sgp4 to sub-meter

### Task 2.5 (Tests) — DONE
- 197/197 tests passing across all Week 2 test files

### Task 3.1 (FastAPI Skeleton) — DONE
- FastAPI app with CORS, lifespan-based shared propagator, health check
- 6/6 tests passing

### Task 3.2 (Satellite List) — DONE
- `GET /api/satellites` returns 30 stations with metadata from cached Parquet
- `epoch_age_days` recomputed per-request, `object_type` defaults to `"UNKNOWN"`
- 16/16 tests passing

### Task 3.4 (Data Refresh) — DONE
- `POST /api/refresh` triggers CelesTrak fetch + propagator reload
- Status detection via `fetch_time` comparison (no private method access needed)
- `reload_data()` only called on "fetched" — preserves satrec cache on rate-limited calls
- All fetcher exceptions caught at API boundary → 502 Bad Gateway
- 15/15 new tests passing (68 total API tests, 265 total project tests)

### Task 3.6 (Unit Tests) — DONE
- All 10 checklist items covered by 82 API tests across Tasks 3.1–3.5 — no additional tests needed
- Week 3 complete

### Task 3.5 (Pydantic Response Models) — DONE
- 8 Pydantic models in `backend/models/schemas.py`, `response_model=` on all 6 endpoints
- OpenAPI at `/openapi.json` includes all models with typed fields
- `errors` field is `list[PositionError] | None = None` — Pydantic v2 uses `anyOf` (not `default`) in OpenAPI
- 14/14 new tests passing (82 total API tests)

### Task 3.3 (Position Endpoints) — DONE
- Three endpoints: batch, single (by NORAD ID), ground track
- `iterrows()` eliminated from all production code (replaced with `iloc` + vectorized `dict(zip(...))`)
- `get_all_positions()` returns `(results, errors)` tuple, errors surfaced in API response
- `RuntimeError` from SGP4 failure caught on single + track endpoints (422, not 500)
- 53/53 API tests passing (33 new for Task 3.3)
- 250/250 total tests passing

### Task 4.1 (Cesium.js Setup) — DONE
- Cesium 1.139.1 via jsDelivr CDN, no bundler
- Viewer with terrain disabled (UHD 620), all default UI stripped, resolutionScale 1.0
- Token in gitignored `config.js`, template in `config.example.js`, missing-token guard in `app.js`
- FastAPI StaticFiles mount at `/` after API routes (html=True)
- 82/82 API tests passing

### Task 4.2 (Satellite Points on Globe) — DONE
- `PointPrimitiveCollection` + `LabelCollection` for GPU-batched rendering
- Smooth interpolation at ~20fps between 5-second API refreshes
- CartoDB dark tiles (`dark_all`) for base map — country borders on dark background
- Label style: FILL only (FILL_AND_OUTLINE causes rendering artifacts)
- `Cartesian3.fromDegrees` height in meters — `alt_km * 1000`
- 279/279 tests passing (no regressions)
