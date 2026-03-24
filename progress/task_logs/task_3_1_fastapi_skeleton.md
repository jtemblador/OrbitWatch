# Task 3.1 — FastAPI App Skeleton

**Date:** Mar 24, 2026
**Status:** DONE
**Tests:** 6/6 passing (health check + CORS + edge cases)

---

## Goal

Stand up the FastAPI application with uvicorn, CORS middleware, and a shared `SatellitePropagator` instance. This is the foundation that all Week 3 API endpoints build on.

---

## Approach

### Lifespan-based Propagator

Used FastAPI's `@asynccontextmanager` lifespan to create a single `SatellitePropagator` on startup, stored on `app.state.propagator`. The propagator itself is lazy — no data loaded until first request calls `_ensure_data()`. This means the app starts instantly even if no cached Parquet exists yet.

### CORS

`allow_origins=["*"]` for local dev. Will tighten to specific frontend origin in Week 8 Docker deployment.

### Router Structure

Health check lives on `app` directly. Satellite/position endpoints go in `backend/routers/satellites.py` (included via `app.include_router()`). This keeps `main.py` minimal.

---

## What Was Built

| Component | Purpose |
|-----------|---------|
| `backend/__init__.py` | Empty package marker (makes `backend.main:app` work) |
| `backend/main.py` | FastAPI app, CORS, lifespan, health check, router include |

---

## Validation

- `uvicorn backend.main:app` starts without error
- `GET /api/health` → `{"status": "ok"}` (200)
- CORS preflight returns `access-control-allow-origin` header
- CORS on regular GET also returns the header

---

## Test Coverage

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestHealthCheck` | 4 | 200 status, body, CORS preflight, CORS GET |
| `TestApiEdgeCases` | 2 | Unknown route → 404, POST health → 405 |

---

## Lessons Learned

1. **TestClient requires context manager for lifespan.** `client = TestClient(app)` at module level does NOT trigger lifespan events. Must use `client = TestClient(app).__enter__()` or `with TestClient(app) as client:` to set `app.state.propagator`.

---

## Function Reference

### `GET /api/health`
Returns `{"status": "ok"}`. No authentication, no dependencies.
