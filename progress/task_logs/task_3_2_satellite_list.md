# Task 3.2 — Satellite List Endpoint

**Date:** Mar 24, 2026
**Status:** DONE
**Tests:** 16/16 passing

---

## Goal

Return metadata for all satellites in the current group via `GET /api/satellites`. This is the simplest endpoint — reads cached Parquet data, no propagation needed. Provides the satellite list the Cesium.js frontend will use to populate its UI.

---

## Approach

### Data Source

Reads from the shared `SatellitePropagator`'s DataFrame (loaded from cached Parquet on first request). No separate `GPFetcher` instance needed — reuses the propagator's data lifecycle.

### Field Mapping

DataFrame columns are renamed for API clarity:

| DataFrame Column | API Field | Unit |
|-----------------|-----------|------|
| `object_name` | `name` | — |
| `norad_cat_id` | `norad_id` | — |
| `object_type` | `object_type` | — (defaults to `"UNKNOWN"` if null) |
| `epoch` | `epoch` | ISO 8601 string |
| (recomputed) | `epoch_age_days` | days |
| `period` | `period_min` | minutes |
| `inclination` | `inclination_deg` | degrees |
| `apoapsis` | `apoapsis_km` | km |
| `periapsis` | `periapsis_km` | km |

### Key Decisions

- **`epoch_age_days` recomputed from `utcnow()`** instead of using the cached value from `_parse_json()`. The cached value goes stale — a 0.5-day-old value becomes wrong within hours.
- **`object_type` null handling:** gp.php doesn't return `OBJECT_TYPE` for the stations group. All come back as `None`. We default to `"UNKNOWN"` using `pd.notna()` check.
- **`group` is not a query param.** Phase 1 only serves stations. The propagator is initialized with `"stations"` and we read `propagator.group` for the response. Multi-group support deferred to Phase 2.
- **All numeric fields cast to `float()`** to prevent numpy type serialization issues in FastAPI's JSON encoder.

---

## What Was Built

| Component | Purpose |
|-----------|---------|
| `backend/routers/satellites.py` | `GET /api/satellites` endpoint |
| `backend/main.py` (modified) | Added router include |

### Response Format

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

---

## Validation

- Returns 30 satellites, all with correct field types
- ISS present with expected metadata (inc ~51.6°, period ~93 min)
- All altitudes physically valid (> 0 km, apoapsis > periapsis)
- Epochs are valid ISO 8601, all within last 30 days
- JSON round-trips cleanly (no numpy type leaks)

---

## Test Coverage

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestSatelliteList` | 16 | 200 status, response keys, group, count, required fields, field types, NORAD IDs positive, ISS present, ISS metadata sanity, ISS altitude bounds, all altitudes valid, epoch format, epoch age, object_type not empty, JSON safety |

### Notable Test Fix

Initial `test_leo_altitude_bounds` assumed all stations were in LEO (300–500 km). Failed because `FREGAT DEB` (debris) has a 2263 km apoapsis. Split into ISS-specific altitude test + general physical validity test.

---

## Scaling

`iterrows()` used for response building — flagged with `# ⚠ PERF` comment and tracked in `progress/scaling_tracker.md` (item #1). Replace with vectorized build at Phase 3.

---

## Lessons Learned

1. **Stations group includes debris/rocket bodies**, not just crewed stations. Tests should not assume all objects are in tight LEO orbits.
2. **gp.php omits `OBJECT_TYPE`** for the stations group. The field is always null. Only `sup-gp.php` or Space-Track provide it. Default to `"UNKNOWN"`.
3. **`epoch_age_days` must be recomputed per request.** The cached value from fetch time drifts. At 2-hour refresh intervals, the error can be significant for stale-data warnings.
