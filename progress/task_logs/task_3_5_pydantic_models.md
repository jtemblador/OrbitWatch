# Task 3.5 — Pydantic Response Models

**Date:** Mar 24, 2026
**Status:** DONE
**Tests:** 82 API tests passing (14 new for Task 3.5)

---

## Goal

Add Pydantic response models to all API endpoints so FastAPI auto-generates typed OpenAPI/Swagger docs at `/docs` and validates response serialization at runtime. This catches stray numpy types, missing fields, and type mismatches before they reach the frontend.

---

## Approach

Pure wiring task — no domain logic changes. Define one Pydantic `BaseModel` per response shape, then add `response_model=` to each endpoint decorator. Endpoints continue returning plain dicts; FastAPI validates them through the models during serialization.

Key decision: model fields as `str` (not `datetime`) for `epoch`, `timestamp`, and `fetch_time` because we already call `.isoformat()` before returning. Keeps models simple and avoids Pydantic datetime serialization config.

---

## Implementation

| File | Action | Purpose |
|------|--------|---------|
| `backend/models/schemas.py` | Created | 8 Pydantic response models (HealthResponse, SatelliteInfo, SatelliteListResponse, PositionResult, PositionError, BatchPositionResponse, TrackPoint, TrackResponse, RefreshResponse) |
| `backend/main.py` | Modified | Added `response_model=HealthResponse` to health check, imported schemas |
| `backend/routers/satellites.py` | Modified | Added `response_model=` to all 5 router endpoints |
| `tests/test_api.py` | Modified | 14 new tests across 2 classes (TestOpenAPISchema, TestResponseValidation) |

---

## Validation

- All 82 API tests pass (14 new + 68 existing)
- OpenAPI schema at `/openapi.json` contains all 9 custom models
- Each model's fields verified against actual endpoint response data
- `errors` field in BatchPositionResponse confirmed nullable via `anyOf` in OpenAPI schema

---

## Test Coverage

| Class | Tests | What it covers |
|-------|-------|----------------|
| TestOpenAPISchema | 8 | All 9 models present, field completeness for SatelliteInfo/PositionResult/TrackPoint/RefreshResponse, errors nullable, all endpoints have 200 responses |
| TestResponseValidation | 6 | Construct Pydantic models from actual endpoint responses — health, satellite list, batch positions, single position, track, refresh |

---

## Lessons Learned

- **Pydantic v2 `anyOf` for optionals:** `Optional[X]` becomes `{"anyOf": [{"type": "array", ...}, {"type": "null"}]}` in OpenAPI, not `{"default": null}`. Tests must check `anyOf` not `default`.
- **`response_model` adds `"errors": null`:** With response_model, Pydantic serializes the `None` default for the `errors` field — previously it was omitted when empty. Minor behavior change but actually better (consistent response shape for frontend).
- **No `__init__.py` needed:** `backend/models/` works as an implicit namespace package in Python 3.

---

## Function Reference

All models in `backend/models/schemas.py`:

| Model | Fields | Used by |
|-------|--------|---------|
| `HealthResponse` | `status: str` | `GET /api/health` |
| `SatelliteInfo` | `name, norad_id, object_type, epoch, epoch_age_days, period_min, inclination_deg, apoapsis_km, periapsis_km` | nested in SatelliteListResponse |
| `SatelliteListResponse` | `count, group, satellites: list[SatelliteInfo]` | `GET /api/satellites` |
| `PositionResult` | `name, norad_id, lat, lon, alt_km, speed_km_s, epoch_age_days` | `GET /api/positions/{norad_id}`, nested in BatchPositionResponse |
| `PositionError` | `name, norad_id, reason` | nested in BatchPositionResponse |
| `BatchPositionResponse` | `count, timestamp, positions, errors (optional)` | `GET /api/positions` |
| `TrackPoint` | `lat, lon, alt_km, timestamp` | nested in TrackResponse |
| `TrackResponse` | `norad_id, name, duration_min, steps, track: list[TrackPoint]` | `GET /api/positions/{norad_id}/track` |
| `RefreshResponse` | `status, group, satellite_count, fetch_time` | `POST /api/refresh` |
