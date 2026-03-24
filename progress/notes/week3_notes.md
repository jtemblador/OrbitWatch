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
