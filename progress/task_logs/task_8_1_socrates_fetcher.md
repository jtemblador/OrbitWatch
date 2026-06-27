# Task 8.1 — SOCRATES fetcher (the Phase-8 validation anchor)

**Date:** Jun 24, 2026
**Status:** DONE
**Tests:** 458 passing, 2 skipped (was 432) — +26 (`test_socrates_fetcher.py`), the
`tle_fetcher` download refactor kept its 54 green.

---

## Goal

Fetch CelesTrak **SOCRATES-Plus** conjunction predictions — the open, SGP4-based
(same-method) reference we validate our screener against in Phase 8 — into a
clean, cached DataFrame, mirroring `tle_fetcher.py`'s fetch/serve discipline.

---

## Approach

- **Bulk CSV over the query endpoint (the key decision).** Investigating the
  format page surfaced that CelesTrak publishes each full run as a static
  **RFC-4180 CSV** (`https://celestrak.org/SOCRATES/sort-minRange.csv`, ~148 k
  rows, one per conjunction). This **obsoleted the planned HTML-table scraping**
  (the live table is a fragile 2-`<tr>`-per-conjunction layout; `FORMAT=csv` on
  the query endpoint is ignored → HTML). We download the full run once, cache it,
  and serve slices locally — no scraping, no auth, no `MAX≤1000` cap.
- **Query *semantics* reproduced locally.** The query endpoint's useful filters —
  a single object (`CATNR`), or conjunctions between two groups (`NAME=a,b`) — are
  reproduced as `by_catnr` / `between` on the cached frame (better: no network, no
  cap). Only `INTDES` (launch filter) is query-only, and we don't need it.
- **Shared downloader (DRY).** Extracted the robust requests→curl-TLS-fallback
  (the 6.9 VPN fix) into `http_fetch.download_text`, used by **both** fetchers;
  `GPFetcher._download` now delegates.
- **Mirror `GPFetcher` hardening:** Parquet cache, 8 h TTL (SOCRATES updates
  3×/day), atomic temp+rename write, graceful cache-fallback, never overwrite a
  good cache with empty.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/socrates_fetcher.py` | **new** — `SOCRATESFetcher`: `fetch` (CSV→typed frame→Parquet, TTL), `top_n`/`by_name`/`by_catnr`/`between` slices, `_split_name_status` (strip the trailing `[status]` suffix), `_parse_csv` (with a header guard) |
| `backend/core/http_fetch.py` | **new** — shared `download_text` (requests→curl fallback) |
| `backend/core/tle_fetcher.py` | `GPFetcher._download` delegates to `http_fetch`; dropped the duplicated curl method + now-unused imports |
| `tests/fixtures/socrates_sample.csv` | **new** — 20 real conjunctions (all status tags, parens, payload-vs-debris) for offline tests |
| `tests/test_socrates_fetcher.py` | **new** — 26 tests (+1 skipped live) |
| `tests/test_gp_fetcher.py` | `TestDownloader` patches repointed to `backend.core.http_fetch` (logic moved) |

---

## Validation

- **Live cross-check vs real SOCRATES:** 147,814 conjunctions, **0 nulls in every
  key column**, closest 0.016 km; fetch 11.9 s (16 MB) / 0.03 s cached.
- **Parse correctness:** TCA byte-exact incl. `.fff` ms as UTC; name/status split
  keeps internal parens (`SHIYAN-21 (SY-21)` → `+`); `MAX_PROB`/`DILUTION` dropped.
- **Cache is lossless:** `assert_frame_equal(fetched, load_cached())` — Parquet
  preserves tz-aware datetimes + `int64` IDs.
- **Mutation-checked:** the regex-literal, schema-guard, and exact-TCA tests
  provably bite (inject the fault → they fail).

---

## Test coverage

| Class (`test_socrates_fetcher.py`) | Covers |
|------|--------|
| `TestParse` (8) | shape/columns, dtypes, drops Pc cols, first-row values, both DSE present, **exact UTC TCA**, empty→empty schema, **unexpected columns raise** |
| `TestNameStatusSplit` (5) | basic split, all status tags, internal parens preserved, no-bracket, **literal-not-regex** |
| `TestSlices` (6) | top_n closest-sorted, by_name (either object, case-insensitive), debris reachable, by_catnr (either position), between (pairwise), no-match→empty |
| `TestFetchCache` (7) | parse+cache (1 download), **lossless round-trip**, force, stale→re-download, network fail→cache, no cache→raise, empty→keep cache |
| `TestLiveFetch` (1, skipped) | opt-in live fetch |

---

## Lessons learned

- **Check the format doc for a bulk/raw feed before scraping.** The official
  `sort-minRange.csv` (one row/conjunction, RFC-4180) replaced a fragile HTML
  parse entirely — more robust *and* matches the documented schema field-for-field.
- **`OBJECT_NAME` carries a trailing `[status]` tag** (`[+]/[-]/[?]/[P]`, also
  `[B]/[X]` live) — strip the **trailing** bracket only (keep internal parens).
- **Object 1 / object 2 are POSITIONAL, not primary/secondary** (format doc) —
  don't assume object 1 is the payload; either side can be debris. All filters
  check both positions. (Matters for 8.2's debris-secondary scope.)
- **`pandas .str.contains` defaults to regex** — object names carry regex-special
  chars (`R/B(1) DEB`), so substring filters need `regex=False` or they silently
  mis-match.
- **`core/` vs `backend.core/` dual-module hazard** — a cross-module function
  (`_download_via_curl`) is a *different object* per import path; patch the module
  that actually executes (`backend.core.http_fetch`), not the test's `core.*` alias.

---

## Remaining risks / deferred

- **Schema drift** — the header guard + fixture test fail loudly if columns
  change, but can't predict it. Acceptable for an external source.
- **30 s download timeout vs 16 MB** on a slow link → degrades to cache, not a hang.
- **`INTDES` (launch) filtering** is query-only (not a CSV column) — unused, not a loss.

---

## Function reference

```python
# backend/core/socrates_fetcher.py
SOCRATESFetcher(cache_dir=DATA_DIR)
  .fetch(force=False)   -> DataFrame   # full run, Parquet-cached, 8 h TTL
  .top_n(n)             -> DataFrame   # n closest (by range_km)
  .by_name(substr)      -> DataFrame   # either object's name contains substr (literal)
  .by_catnr(norad_id)   -> DataFrame   # one object's conjunctions (= CATNR query)
  .between(a, b)        -> DataFrame   # one obj ~a AND the other ~b (= NAME=a,b)
# Output schema: norad_id_1, name_1, status_1, dse_1, norad_id_2, name_2,
#   status_2, dse_2, tca(UTC), range_km, rel_speed_km_s, fetch_time

# backend/core/http_fetch.py
download_text(url, user_agent="OrbitWatch/1.0") -> str   # requests → curl fallback
```
