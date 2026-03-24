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

## Task 3.4: Data Refresh Endpoint (Mar 24)

- `POST /api/refresh` triggers CelesTrak fetch + propagator reload
- Status detection: compare `fetch_time` before/after `fetcher.fetch()` — same = `"rate_limited"`, different = `"fetched"`
- `reload_data()` only called on actual fetch — avoids clearing satrec cache on rate-limited calls
- Broad `except Exception` at API boundary — fetcher can fail multiple ways (RuntimeError, ValueError, urllib)
- Double Parquet read on rate-limited path (load_cached + fetch's _load_if_fresh) — acceptable at 30 sats, noted for Phase 3
- Scaling tracker updated: Phase 3 upgrade to 202 Accepted + background task + scheduled auto-refresh
- Design principle: GET endpoints serve from local cache, only POST /refresh touches CelesTrak

## Task 3.5: Pydantic Response Models (Mar 24)

- 8 models in `backend/models/schemas.py` — one per response shape, no inheritance
- All datetime/timestamp fields modeled as `str` since we call `.isoformat()` before returning
- `errors` in `BatchPositionResponse` uses `list[PositionError] | None = None` — Pydantic v2 serializes as `anyOf` in OpenAPI, not `default`
- `response_model=` added to all 6 endpoints (1 in main.py, 5 in satellites.py)
- Minor behavior change: batch response now includes `"errors": null` when no errors (previously key was omitted). Better for frontend — consistent shape.
- No `__init__.py` needed in `backend/models/` — Python 3 implicit namespace packages

## Flaky Test: `TestPerformance.test_index_lookup_is_constant_time` (to fix end of week)

- **What:** `tests/test_propagator.py` — asserts 100 name lookups < 0.05s, occasionally hits 0.077s
- **Root cause:** Tight timing threshold on a non-deterministic operation. First lookup triggers lazy `_ensure_data()` (Parquet load + index build), which inflates the measurement.
- **Fix options (end of Week 3):**
  1. Warm up the propagator before timing (call `_ensure_data()` in setUp)
  2. Raise threshold to 0.1s (still validates O(1) vs O(n), less flaky)
  3. Measure per-lookup time instead of total (amortizes cold-start)
- **Not a correctness issue** — lookups are O(1) via dict index, this is just a flaky timing assertion
