# Week 3 Notes — FastAPI Backend

## Task 3.1: FastAPI App Skeleton (Mar 24)

- Used lifespan context manager for shared `SatellitePropagator` on `app.state`
- Propagator is lazy — no Parquet load until first request
- `TestClient` must enter context manager to trigger lifespan events (caught during testing)
- Health check at `/api/health`, CORS `allow_origins=["*"]`

## Task 3.2: Satellite List Endpoint (Mar 24)

- `GET /api/satellites` serves 30 stations from cached Parquet
- `epoch_age_days` recomputed per-request (cached value drifts)
- `object_type` is always null from gp.php — defaults to `"UNKNOWN"`
- `FREGAT DEB` in stations group has 2263 km apoapsis — stations group isn't all LEO
- `group` query param removed — Phase 1 only serves stations, reads from `propagator.group`
- `iterrows()` flagged for Phase 3 scaling, tracked in `progress/scaling_tracker.md`
- Created `progress/scaling_tracker.md` to centrally track all Phase 3 perf items

## Task 3.3: Position Endpoints (Mar 24)

- Three endpoints: `/api/positions` (batch), `/api/positions/{norad_id}` (single), `/api/positions/{norad_id}/track` (ground track)
- `_parse_time()` and `_format_position()` helpers shared across endpoints — keeps DRY
- `+` in query strings decoded as space — use `Z` suffix or `%2B` encoding for UTC offset
- `get_all_positions()` changed to return `(results, errors)` tuple — callers decide how to surface failures
- `epoch_age_days` accepted from propagator as-is in position endpoints (not recomputed like `/api/satellites`)
- Track endpoint: `get_positions_at_times()` takes name, not NORAD ID — look up via `find_by_norad_id()` first
- Review found: single + track endpoints didn't catch `RuntimeError` from SGP4 propagation failure → would 500 on decayed orbits. Fixed.
- Pre-build: swapped all 3 production `iterrows()` to `iloc`, vectorized `_build_indexes()`, resolved 3 scaling tracker items
- New scaling tracker item: C++ batch SGP4 (`orbitcore.sgp4_batch()`) for Phase 3 — eliminates 6k Python→C++ boundary crossings
