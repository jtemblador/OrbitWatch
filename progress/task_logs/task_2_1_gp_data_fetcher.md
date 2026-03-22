# Task 2.1 — GP Data Fetcher

**Date:** Mar 21–22, 2026
**Status:** DONE
**Tests:** 37/37 passing

---

## Goal

Build a module that fetches satellite orbital data from CelesTrak and stores it locally for the SGP4 propagation engine to consume. This is the data ingestion layer — everything downstream (propagation, coordinate transforms, conjunction screening) depends on the quality and completeness of what we fetch here.

---

## Approach

### Format Decision: JSON/OMM vs Legacy TLE

The original plan called for fetching legacy 3-line TLE text files (`stations.txt`). During research into CelesTrak's documentation, we discovered this would be a mistake:

- **5-digit NORAD catalog number cap.** The TLE format's fixed-width field for catalog numbers maxes out at 99999. The catalog is expected to hit this limit around July 2026 — within this project's lifetime. JSON/OMM has no such limit.
- **Precision loss.** TLE encodes eccentricity as 7 digits with an implied leading decimal (e.g., `0007976` = 0.0007976). JSON provides full floating-point precision.
- **Epoch ambiguity.** TLE uses a 2-digit year + fractional day-of-year (e.g., `26083.54321`). JSON uses ISO 8601 (`2026-03-24T13:02:13.344`). No Y2K-style guessing.
- **CelesTrak's own recommendation.** Their documentation explicitly recommends migrating to JSON/OMM for new development.

The tradeoff: TLE strings can be fed directly into SGP4 parsers. With JSON, we extract individual fields (mean_motion, eccentricity, inclination, etc.) and call `Satrec.sgp4init()` directly. This is actually cleaner — no string parsing, and we control the data types.

### Architecture

```python
class GPFetcher:
    fetch(group, force)       # Download from CelesTrak, validate, cache
    fetch_by_catnr(norad_id)  # Single satellite lookup
    load_cached(group)        # Read from local Parquet
    _load_if_fresh(group)     # Check if cache is < 2 hours old
    _download(url)            # HTTP request with SSL workaround
    _parse_json(records)      # JSON → validated DataFrame
    _derive_orbit_params(mm, ecc)  # Compute period, apoapsis, periapsis
    _cache_to_parquet(df, group)   # Atomic write to Parquet
```

### Data Flow
```
CelesTrak JSON API → HTTP download → JSON parse → per-record validation
→ derive orbital params → build DataFrame → atomic Parquet write → return
```

---

## Findings

### CelesTrak API Behavior
- Data updates every ~2 hours. Fetching more often wastes bandwidth and risks IP-blocking.
- 403/404 responses should NOT be retried — CelesTrak escalates to permanent blocks.
- The server occasionally returns empty `[]` during data refresh windows.
- SSL certificate chain issues in our environment required `ssl._create_unverified_context()` as a workaround. Acceptable for public, non-sensitive orbital data.
- `gp.php` provides the 17 core OMM fields but NOT object metadata (type, RCS size, country). That requires either `sup-gp.php` or direct Space-Track API access.

### Data Quality Issues
- CelesTrak aggregates from Space-Track, which occasionally has corrupted records (e.g., zero mean motion, negative eccentricity).
- Some objects in the `stations` group have decayed (re-entered atmosphere). Propagating these through SGP4 produces positions underground.
- Non-SGP4 ephemeris types (type ≠ 0) exist in the catalog. Feeding these to SGP4 produces incorrect results.

### Derived Orbital Parameters
CelesTrak's `gp.php` endpoint doesn't provide period, apoapsis, or periapsis — but these are essential for conjunction screening (altitude-band filtering). We compute them ourselves:
- **Period** (minutes) = 1440 / mean_motion. Determines near-Earth vs deep-space SGP4 mode.
- **Semimajor axis** from Kepler's 3rd law: `a = (GM / n²)^(1/3)`, where `n` is mean motion in rad/s.
- **Apoapsis/periapsis** = `a(1 ± e) - R_earth`. Used to skip satellite pairs in different altitude bands during conjunction screening.

---

## Results

### Live Test
```
Fetched stations GP data from CelesTrak (30 objects)
  Parsed 30 satellites
  Cached to backend/data/tle/stations.parquet
```

### Validation Behavior
- Zero mean motion → skipped (prevents division by zero)
- Negative eccentricity → skipped (physically impossible)
- Eccentricity ≥ 1 → skipped (parabolic/hyperbolic — not a bound orbit)
- Ephemeris type ≠ 0 → skipped (non-SGP4, incompatible)
- Decayed objects → skipped (would produce underground positions)
- Missing required JSON field → skipped with log message (one bad record doesn't crash the batch)

### Cache Behavior
- Fresh cache (< 2 hours): returns cached data, prints age and time until next update
- Stale cache (≥ 2 hours): fetches from CelesTrak, overwrites cache
- Network error: falls back to stale cache if available, raises RuntimeError if not
- Empty response: does NOT overwrite valid cache (CelesTrak refresh window protection)
- Write safety: atomic temp-file-then-rename prevents corruption on process kill

### DataFrame Schema (28 columns)
| Category | Columns |
|----------|---------|
| Identity | object_name, object_id, norad_cat_id, classification |
| Epoch | epoch, epoch_age_days |
| SGP4 inputs | mean_motion, eccentricity, inclination, ra_of_asc_node, arg_of_pericenter, mean_anomaly, bstar, mean_motion_dot, mean_motion_ddot |
| Derived | period, semimajor_axis, apoapsis, periapsis |
| Metadata | object_type, rcs_size, country_code, launch_date, decay_date |
| Element set | ephemeris_type, element_set_no, rev_at_epoch |
| Fetch info | fetch_time |

---

## Files

| File | Action | Purpose |
|------|--------|---------|
| `backend/core/tle_fetcher.py` | Created | GPFetcher class — fetch, validate, cache GP data |
| `backend/core/__init__.py` | Created | Python package init (empty) |
| `tests/test_gp_fetcher.py` | Created | 37 unit tests covering parse, validation, cache, errors, schema |
| `requirements.txt` | Modified | Added fastapi, sgp4, pandas, spiceypy, etc. |
| `.gitignore` | Modified | Added .parquet, .claude/ exclusions |

---

## Function Reference

### `GPFetcher.fetch(group, force) → DataFrame`
Main entry point. Checks cache freshness → downloads if stale → validates → caches → returns. Groups: `stations`, `visual`, `starlink`, `active`.

### `GPFetcher.fetch_by_catnr(norad_id) → DataFrame`
Single-satellite lookup by NORAD catalog number. No caching. Raises ValueError on 403/404.

### `GPFetcher._parse_json(records) → DataFrame`
Iterates CelesTrak JSON records. Per-record try/except catches malformed data. Validates physics (mean_motion > 0, 0 ≤ eccentricity < 1), filters decayed/non-SGP4, computes derived params, timestamps with `epoch_age_days` and `fetch_time`.

### `GPFetcher._derive_orbit_params(mean_motion, eccentricity) → dict`
Static method. Returns `{period, semimajor_axis, apoapsis, periapsis}`. Uses WGS-72 constants (GM = 398600.8 km³/s², R = 6378.135 km) to match SGP4's gravity model.

### `GPFetcher._cache_to_parquet(df, group)`
Atomic write: creates temp file in same directory → writes Parquet → renames to final path. If write fails mid-way, temp file is cleaned up and original cache is untouched.

---

## Lessons Learned

1. **Always validate external data per-record, not per-batch.** One malformed CelesTrak record out of 6000 would have crashed the entire Starlink fetch if we used a simple DataFrame constructor without try/except.
2. **Derive what the API doesn't provide.** CelesTrak's simple endpoint lacks period/apoapsis/periapsis, but we compute them from mean_motion + eccentricity with standard orbital mechanics. This is more reliable than depending on `sup-gp.php` which may have different availability.
3. **Cache defensively.** The empty-response guard and atomic writes were both identified in the code audit — real failure modes, not theoretical ones.
