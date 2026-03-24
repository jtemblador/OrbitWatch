# Task 3.4 — Data Refresh Endpoint

**Date:** Mar 24, 2026
**Status:** DONE
**Tests:** 68 API tests passing (15 new for Task 3.4)

---

## Goal

Add a `POST /api/refresh` endpoint that triggers a TLE data refresh from CelesTrak and reloads the propagator. This separates "fetch from upstream" from "serve to clients" — GET endpoints always serve from local Parquet cache, only POST /refresh touches CelesTrak.

---

## Approach

### Status Detection via fetch_time Comparison

The key design challenge: `GPFetcher.fetch()` handles rate limiting internally (returns cached data if <2h old) but doesn't tell the caller whether it actually fetched. Solution: snapshot `fetch_time` from cached Parquet before calling `fetch()`, then compare to the returned DataFrame's `fetch_time`.

- Same timestamp → `"rate_limited"` (cache was returned)
- Different timestamp → `"fetched"` (new data from CelesTrak)

This avoids accessing private methods or modifying the fetcher's interface.

### Selective Propagator Reload

`propagator.reload_data()` is only called on actual fetch — not on rate-limited responses. This preserves the satrec cache (expensive `sgp4init()` calls) when no new data is available.

### Error Handling

`except Exception` catches all fetcher failure modes (RuntimeError from no-cache + network-down, ValueError, urllib errors) → 502 Bad Gateway. The client never sees an unhandled 500 from the refresh path.

---

## Implementation

| File | Change |
|------|--------|
| `backend/routers/satellites.py` | Added `POST /api/refresh` endpoint (lines 168–205) |
| `tests/test_api.py` | Added `TestRefresh` (10 tests) + `TestRefreshMocked` (5 tests) |

---

## Validation

- All 15 new tests pass, 68 total API tests, 265 total project tests
- Rate limiting verified: two rapid POST calls → second returns `"rate_limited"`
- Mocked "fetched" path: patched `fetcher.fetch()` with modified `fetch_time` → status is `"fetched"`, `reload_data()` called exactly once
- Rate-limited path: `reload_data()` NOT called (verified via mock)
- Error paths: RuntimeError and ValueError both produce 502
- Integration: `/api/positions` still works after refresh (propagator reloads correctly)
- `satellite_count` matches `/api/satellites` count

---

## Test Coverage

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestRefresh` | 10 | Happy path (200, keys, status values, group, count), rate limiting, ISO 8601 fetch_time, count consistency, GET → 405, positions after refresh |
| `TestRefreshMocked` | 5 | Fetched status via mock, reload_data called on fetch, reload_data skipped on rate_limited, 502 on RuntimeError, 502 on ValueError |

---

## Lessons Learned

- **Double Parquet read on rate-limited path:** `load_cached()` reads old fetch_time, then `fetch()` internally calls `_load_if_fresh()` which reads again. Acceptable at 30 sats (~ms), but worth noting for Phase 3 optimization.
- **`pd.Timestamp.isoformat()` Pyright warning:** Pyright flags `.isoformat()` as potentially called on `NaTType`. False positive when `pd.Timestamp()` is constructed from a valid datetime. Wrapped with `str()` to silence.
- **Broad exception catch is correct here:** `fetcher.fetch()` can fail in multiple ways (RuntimeError, ValueError, urllib errors). Catching `Exception` at the API boundary ensures all paths → 502.

---

## Function Reference

### `POST /api/refresh`
**Response:**
```json
{
  "status": "fetched" | "rate_limited",
  "group": "stations",
  "satellite_count": 30,
  "fetch_time": "2026-03-24T15:30:00+00:00"
}
```
**Error:** 502 if CelesTrak fetch fails and no cache available.

---

## Phase 3 Upgrade Path

Tracked in `progress/scaling_tracker.md` (item #2):
- Current: synchronous POST, ~2s for 30 sats
- Phase 3: `202 Accepted` + background task + scheduled auto-refresh
- Goal: no client request ever directly triggers a CelesTrak fetch in the critical path
