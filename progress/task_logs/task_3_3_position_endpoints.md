# Task 3.3 — Position Endpoints

**Date:** Mar 24, 2026
**Status:** DONE
**Tests:** 53 API tests passing (33 new for Task 3.3)

---

## Goal

Build three endpoints that propagate satellites to user-specified times, completing the core REST API. These endpoints connect the Week 2 propagation pipeline to HTTP, ready for the Cesium.js frontend in Week 4.

---

## Approach

### Endpoint Design

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/positions` | GET | Batch-propagate all ~30 satellites |
| `/api/positions/{norad_id}` | GET | Single satellite by NORAD ID |
| `/api/positions/{norad_id}/track` | GET | Ground track (multi-step time series) |

### Key Decisions

- **`time` query param** — optional ISO 8601 string, defaults to `utcnow()`. Naive datetimes treated as UTC. Shared `_parse_time()` helper across all 3 endpoints.
- **`_format_position()` helper** — maps propagator result dict keys to API format (`alt` → `alt_km`). Casts all numerics to `float()` to prevent numpy serialization issues.
- **`epoch_age_days` accepted from propagator** — not recomputed per-request in position endpoints (unlike `/api/satellites`). The value is informational and will be revisited when live tracking is built.
- **Batch errors surfaced, not swallowed** — `get_all_positions()` changed to return `(results, errors)` tuple. Failed sats appear in an `errors` array in the response instead of being silently dropped.
- **Track endpoint uses name lookup** — `get_positions_at_times()` takes a satellite name, not NORAD ID. Endpoint looks up name via `find_by_norad_id()` first.
- **Query param bounds** — `duration_min` (1–1440), `steps` (2–500) enforced by FastAPI `Query` constraints.

### Pre-build Improvements

Before implementing endpoints, three perf improvements were made:

1. **`iterrows()` → `iloc`** in `satellites.py`, `propagator.py` (2 locations) — removes per-iteration Series creation overhead
2. **`_build_indexes()` vectorized** — replaced `iterrows()` loop with `dict(zip(df[col], df.index))`
3. **`get_all_positions()` return type** — changed from `list[dict]` to `tuple[list[dict], list[dict]]` to surface propagation errors

---

## What Was Built

| Component | Purpose |
|-----------|---------|
| `backend/routers/satellites.py` (modified) | Added 3 position endpoints + 2 helper functions |
| `backend/core/propagator.py` (modified) | `get_all_positions()` returns `(results, errors)`, `_build_indexes()` vectorized, `iterrows()` → `iloc` |
| `tests/test_api.py` (modified) | 33 new tests for position endpoints |
| `tests/test_propagator.py` (modified) | Updated 2 callers for new `(results, errors)` tuple |
| `progress/scaling_tracker.md` (modified) | 3 items resolved, 1 new item (C++ batch SGP4) |

### Response Formats

**Batch** (`/api/positions`):
```json
{
  "count": 30,
  "timestamp": "2026-03-24T15:30:00+00:00",
  "positions": [{"name": "ISS (ZARYA)", "norad_id": 25544, "lat": 12.3, "lon": -45.6, "alt_km": 420.1, "speed_km_s": 7.66, "epoch_age_days": 0.5}],
  "errors": []
}
```

**Single** (`/api/positions/25544`):
```json
{"name": "ISS (ZARYA)", "norad_id": 25544, "lat": 12.3, "lon": -45.6, "alt_km": 420.1, "speed_km_s": 7.66, "epoch_age_days": 0.5}
```

**Track** (`/api/positions/25544/track`):
```json
{
  "norad_id": 25544, "name": "ISS (ZARYA)",
  "duration_min": 90, "steps": 60,
  "track": [{"lat": 12.3, "lon": -45.6, "alt_km": 420.1, "timestamp": "2026-03-24T15:30:00+00:00"}]
}
```

---

## Validation

- All 30 stations propagate successfully via `/api/positions`
- ISS position: alt 300–500 km, speed 7.0–8.0 km/s
- All latitudes in [-90, 90], longitudes in [-180, 180], altitudes > 0
- Custom time param works (ISO 8601 with `Z` suffix)
- Unknown NORAD ID → 404
- Malformed time → 422 on all 3 endpoints
- Track: 60 points spanning ~88.5 min (59 intervals over 90 min)
- No numpy types leak through JSON serialization

---

## Test Coverage

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestBatchPositions` | 13 | 200 status, response keys, count, required fields, lat/lon/alt/speed ranges, custom time, malformed time → 422, timestamp format, JSON safety |
| `TestSinglePosition` | 8 | ISS 200, required fields, altitude bounds, speed bounds, NORAD ID match, unknown → 404, custom time, malformed time → 422 |
| `TestGroundTrack` | 10 | 200 status, response keys, default 60 points, track fields, lat range, alt positive, custom steps/duration, timestamp span, unknown → 404, malformed time → 422 |
| (Prior Task 3.1–3.2) | 22 | Health check, CORS, satellite list |

---

## Review Fixes

Three issues found and fixed during review:

1. **Stale type hint** on `get_all_positions()` — updated `-> list[dict]` to `-> tuple[list[dict], list[dict]]` with corrected docstring
2. **Unhandled `RuntimeError`** in single position endpoint — decayed orbit would 500. Now catches and returns 422 with reason.
3. **Unhandled `RuntimeError`** in track endpoint — same fix, wraps `get_positions_at_times()`.

---

## Scaling

- All 3 former `iterrows()` items resolved and moved to "Resolved" in scaling tracker
- New item added: C++ batch SGP4 (`orbitcore.sgp4_batch()`) for Phase 3 — eliminates 6,000 Python→C++ boundary crossings

---

## Lessons Learned

1. **`+` in query strings is decoded as space.** `?time=2026-03-24T12:00:00+00:00` fails because `+00:00` becomes ` 00:00`. Tests must use `Z` suffix or URL-encode the `+` as `%2B`.
2. **Propagation `RuntimeError` can escape through API endpoints.** Any endpoint calling propagator methods must catch `RuntimeError` for decayed/invalid orbits. Batch endpoint handles this internally; single and track endpoints need explicit catches.
3. **`get_all_positions()` silently dropping errors was a design smell.** Returning `(results, errors)` is cleaner — callers decide how to handle failures instead of the propagator printing to stdout.
