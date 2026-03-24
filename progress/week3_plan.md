# Week 3 — FastAPI Backend (Apr 10–16, 2026)

**Goal:** Stand up a REST API that serves real-time satellite positions from the Week 2 propagation pipeline. By the end of this week, any HTTP client can fetch satellite metadata, current positions, and ground tracks — ready for the Cesium.js frontend in Week 4.

---

## What We Have (from Week 2)

| Component | File | Interface |
|-----------|------|-----------|
| GP data fetcher | `backend/core/tle_fetcher.py` | `GPFetcher.fetch()`, `.load_cached()` → DataFrame (30 stations) |
| Coordinate transforms | `backend/core/coordinate_transforms.py` | `teme_to_geodetic(pos, jd, vel)` → `{lat, lon, alt, pos_ecef, vel_ecef}` |
| C++ SGP4 engine | `backend/orbitcore.cpython-312-x86_64-linux-gnu.so` | `orbitcore.sgp4init(...)` → Satrec, `orbitcore.sgp4(satrec, tsince)` → pos/vel |
| Propagator wrapper | `backend/core/propagator.py` | `SatellitePropagator.get_position(name, utc_dt)` → result dict |

**Result dict keys:** `name, norad_id, lat, lon, alt, pos_ecef, vel_ecef, speed_km_s, timestamp, epoch_age_days`

---

## Main Tasks

### ✅ 1. FastAPI App Skeleton (`backend/main.py`)

Set up the FastAPI application with uvicorn, CORS middleware, and a shared `SatellitePropagator` instance.

**What to build:**
- FastAPI app with CORS (allow `*` for local dev; tighten in Week 8 Docker)
- Single shared `SatellitePropagator` instance (lazy-loaded on first request)
- Health check endpoint: `GET /api/health`
- Uvicorn entry point (run with `uvicorn backend.main:app --reload`)

**Success criteria:**
- [x] `uvicorn backend.main:app --reload` starts without error
- [x] `GET /api/health` returns `{"status": "ok"}`
- [x] CORS headers present in responses

**Actual:** 6 tests passing (health check + CORS + edge cases)

---

### ✅ 2. Satellite List Endpoint (`GET /api/satellites`)

Return metadata for all satellites in the current group (Phase 1 = stations).

**What to build:**
- Returns list of satellite objects with: `name, norad_id, object_type, epoch, epoch_age_days, period, inclination, apoapsis, periapsis`
- Source: `GPFetcher.load_cached()` DataFrame — no propagation needed
- ~~Optional query params: `group` (default `"stations"`) for future Phase 2+ groups~~ → Deferred to Phase 2. Reads `propagator.group` instead.

**Response format:**
```json
{
  "count": 30,
  "group": "stations",
  "satellites": [
    {
      "name": "ISS (ZARYA)",
      "norad_id": 25544,
      "object_type": "UNKNOWN",
      "epoch": "2026-03-21T20:09:59.780736+00:00",
      "epoch_age_days": 2.5,
      "period_min": 92.9972,
      "inclination_deg": 51.6344,
      "apoapsis_km": 425.511,
      "periapsis_km": 417.11
    }
  ]
}
```

**Success criteria:**
- [x] Returns all ~30 Phase 1 stations (actual: 30)
- [x] Fields match expected types and units (validated all 30)
- [x] Response time < 500ms (reading cached Parquet)

**Actual:** 16 tests passing. `object_type` always `"UNKNOWN"` (gp.php doesn't provide it). `epoch_age_days` recomputed per-request.

---

### ✅ 3. Position Endpoints

#### `GET /api/positions` — All satellites, current time

Batch-propagate all satellites to the current UTC time (or a provided timestamp).

**What to build:**
- Calls `SatellitePropagator.get_all_positions(utc_dt)`
- Optional query param: `time` (ISO 8601 string, defaults to `utcnow()`)
- Returns list of position dicts (lat, lon, alt, speed, etc.)
- Satellites that fail propagation (decayed orbits) are skipped with a warning, not a 500

**Response format:**
```json
{
  "count": 30,
  "timestamp": "2026-03-24T15:30:00Z",
  "positions": [
    {
      "name": "ISS (ZARYA)",
      "norad_id": 25544,
      "lat": 12.3,
      "lon": -45.6,
      "alt_km": 420.1,
      "speed_km_s": 7.66,
      "epoch_age_days": 0.5
    }
  ]
}
```

#### `GET /api/positions/{norad_id}` — Single satellite

Propagate one satellite by NORAD catalog number.

**What to build:**
- Calls `SatellitePropagator.get_position_by_norad_id(norad_id, utc_dt)`
- Optional query param: `time`
- Returns single position dict
- 404 if NORAD ID not found

#### `GET /api/positions/{norad_id}/track` — Ground track

Return positions over a time range for orbit trail rendering (Week 4–5 frontend).

**What to build:**
- Calls `SatellitePropagator.get_positions_at_times(name, utc_dts)`
- Query params: `duration_min` (default 90 = ~1 orbit), `steps` (default 60)
- Returns array of `{lat, lon, alt, timestamp}` points

**Success criteria:**
- [x] `/api/positions` returns all ~30 satellites with valid lat/lon/alt
- [x] `/api/positions/25544` returns ISS position (alt ~400–435 km)
- [x] `/api/positions/25544/track` returns 60 points spanning ~1 orbit
- [x] Custom `time` param works (ISO 8601 string)
- [x] Unknown NORAD ID returns 404
- [x] Response time < 1s for batch, < 100ms for single satellite

**Actual:** 33 new tests passing. `iterrows()` replaced with `iloc` in all production files. `get_all_positions()` now returns `(results, errors)` tuple. RuntimeError caught on single + track endpoints.

---

### ✅ 4. Data Refresh Endpoint (`POST /api/refresh`)

Trigger a TLE data refresh from CelesTrak.

**What to build:**
- Calls `GPFetcher.fetch()` (respects 2-hour rate limit internally)
- Calls `SatellitePropagator.reload_data()` to clear caches and rebuild indexes
- Returns status: fetched, rate-limited, or error

**Success criteria:**
- [x] Fresh data fetched and propagator reloaded
- [x] Rate-limited if called within 2 hours of last fetch
- [x] Does not break in-flight position requests

**Actual:** 15 new tests passing (68 total API). Status detection via `fetch_time` comparison. `reload_data()` only called on actual fetch (preserves satrec cache). All fetcher exceptions → 502.

---

### ✅ 5. Pydantic Response Models

Define response schemas so FastAPI auto-generates OpenAPI docs.

**What to build:**
- `SatelliteInfo` — metadata fields
- `SatellitePosition` — position result fields
- `TrackPoint` — ground track point
- Response wrappers with `count`, `timestamp`, list of items

**Success criteria:**
- [x] `/docs` (Swagger UI) shows all endpoints with typed schemas
- [x] Response validation catches any propagator output mismatches

**Actual:** 8 Pydantic models in `backend/models/schemas.py`. All 9 models appear in OpenAPI schema. 14 new tests (82 total API tests). `errors` field correctly nullable via Pydantic v2 `anyOf`.

---

### ✅ 6. Unit Tests (`tests/test_api.py`)

**What to test:**
- Health check returns 200
- `/api/satellites` returns correct count and field types
- `/api/positions` returns valid positions for all stations
- `/api/positions/{norad_id}` returns correct satellite
- `/api/positions/{norad_id}` with unknown ID returns 404
- `/api/positions/{norad_id}/track` returns expected number of points
- Custom `time` param parses and propagates correctly
- `/api/refresh` triggers fetch (mock network call)
- CORS headers present
- Response schemas match Pydantic models

**Success criteria:**
- [x] All API tests pass
- [x] Tests use FastAPI `TestClient` (no real server needed)
- [x] Network calls mocked where appropriate

**Actual:** All 10 test items covered by 82 tests across 10 test classes, distributed across Tasks 3.1–3.5 as each endpoint was built. No additional tests needed.

---

## Implementation Order

1. ✅ **Pydantic models** — 8 response models, all endpoints wired (14 new tests, 82 total API tests)
2. ✅ **App skeleton** — FastAPI + CORS + health check + shared propagator (6 tests)
3. ✅ **`/api/satellites`** — simplest endpoint (no propagation, just cached data) (16 tests)
4. ✅ **`/api/positions`** — single, batch, and ground track (33 new tests, 53 total API tests)
5. ✅ **`/api/refresh`** — data refresh (15 new tests, 68 total API tests)
6. ✅ **Tests** — 82 API tests across 10 test classes (schema tests complete Task 3.6)

---

## Key Files

```
backend/
├── main.py              ⬜ CREATE — FastAPI app, CORS, uvicorn entry point
├── routers/
│   └── satellites.py    ⬜ CREATE — all satellite/position endpoints
├── models/
│   └── schemas.py       ✅ CREATED — 8 Pydantic response models
├── core/
│   ├── propagator.py    ✅ EXISTS — SatellitePropagator (Week 2)
│   ├── tle_fetcher.py   ✅ EXISTS — GPFetcher (Week 2)
│   └── coordinate_transforms.py  ✅ EXISTS — teme_to_geodetic (Week 2)
└── orbitcore.cpython-312-x86_64-linux-gnu.so  ✅ EXISTS — C++ SGP4

tests/
└── test_api.py          ⬜ CREATE — API endpoint tests
```

---

## Things to Watch

| Concern | Detail |
|---------|--------|
| `orbitcore` import path | `backend/main.py` must have `sys.path` set correctly before importing propagator, or the namespace shadowing issue will resurface |
| Propagator is not thread-safe | Uvicorn with `--workers > 1` would create separate propagator instances (fine). But `--reload` mode is single-process — no issue for dev |
| Stale TLE data | `epoch_age_days` should be surfaced in responses so the frontend can warn users when predictions are unreliable (> 3 days old) |
| Serialization of numpy types | Propagator returns Python floats, but if any numpy types leak through, FastAPI's JSON encoder will choke. Pydantic models will catch this |
| Phase 1 only | This API serves ~30 stations. No pagination needed yet. Add pagination when scaling to Phase 2+ (Week 6/8) |

---

## Success Criteria (Definition of Done)

- [x] `uvicorn backend.main:app` starts cleanly
- [x] `/api/health` → 200
- [x] `/api/satellites` → 30 stations with correct metadata
- [x] `/api/positions` → 30 positions with valid lat/lon/alt/speed
- [x] `/api/positions/25544` → ISS position (~420 km alt)
- [x] `/api/positions/25544/track` → ground track points
- [x] `/api/refresh` → triggers data reload
- [x] `/docs` → Swagger UI with typed schemas
- [x] All API tests pass (82 total)
- [x] Ready for Cesium.js frontend integration in Week 4
