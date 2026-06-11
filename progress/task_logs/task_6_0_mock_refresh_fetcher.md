# Task 6.0 — Mock the Refresh Fetcher (Test Hygiene)

**Date:** Jun 11, 2026
**Status:** DONE
**Tests:** 280 passing, 1 skipped (was 279) — +2 net in `test_api.py` (offline `setUp` rework, `test_refresh_makes_no_network_call`, `TestRefreshLive`)

---

## Goal

Make the test suite run fully offline and stop mutating real project data. The `TestRefresh` class
was calling `POST /api/refresh` **unmocked**, so every full-suite run hit live CelesTrak (whenever
the cache was >2 h stale) and overwrote `backend/data/tle/stations.parquet`. That made the suite
network-dependent, non-deterministic (live catalog churn), and risked CelesTrak rate-limiting /
IP-block during the constant test-running of Phase 6 — which would also break the real app's fetch.

This is a pre-build hygiene task (done first in Phase 6) so the rest of the phase runs fast/offline.

---

## Approach

### Mock at the fetcher boundary, return cached data unchanged

A module-level helper `_offline_fetch_patch()` patches `app.state.propagator.fetcher.fetch` to
return the existing cached DataFrame **unchanged**. Because the route compares the cached
`fetch_time` (via `load_cached`) against the fetched one, returning the same df yields identical
timestamps → deterministic `"rate_limited"`, with **no network call and no Parquet write**.

Chosen over the alternatives:
- *Mock `_download` + temp cache dir* — more faithful (exercises real parse/cache logic) but requires
  rewiring the module-level `client`/propagator to a temp dir. Not worth it: the cache logic is
  already covered by `test_gp_fetcher.py`, and `TestRefreshMocked` already covers the "fetched" path.
- *Always return a tiny synthetic df* — would break `test_refresh_count_matches_satellite_list`
  (count must match `/api/satellites`). Returning the real cached df keeps counts consistent.

### Keep one real end-to-end test, opt-in

Added `TestRefreshLive`, gated by `@unittest.skipUnless(os.getenv("RUN_NETWORK_TESTS"), ...)`. Standard
test-pyramid practice: fast isolated tests by default, a few explicit integration tests run on purpose.

### Enforce the invariant (review/test phase)

Added `test_refresh_makes_no_network_call` asserting `_download` is never reached during a refresh —
a regression guard at the actual network boundary.

---

## Implementation

| File | Change |
|------|--------|
| `tests/test_api.py` | Module-level `from unittest.mock import patch` + `_offline_fetch_patch()` helper; `setUp` on `TestRefresh` (patch started, `addCleanup(p.stop)`); wrapped stray `test_refresh_matches_model` call; new `test_refresh_makes_no_network_call`; new opt-in `TestRefreshLive` |
| `progress/week6_plan.md` | 6.0 task spec + success criteria checked + "Actual" notes |

**No production code touched** — purely a test change.

---

## Validation

- **280 passed, 1 skipped**, stable across 3 consecutive full-suite runs.
- **`stations.parquet` md5 byte-identical** before vs after a full run — confirms no mutation.
- The 1 skip is exactly `TestRefreshLive` (verified via `-rs`); runs only with `RUN_NETWORK_TESTS=1`.
- `test_refresh_makes_no_network_call` passes — `_download` not called during refresh.
- Isolation confirmed: full suite green (other classes unaffected by the per-test patch).

---

## Test Coverage

| Class | Tests | What it covers |
|-------|-------|----------------|
| `TestRefresh` (reworked) | 11 | Offline via `setUp`; happy path, rate-limited path, count consistency, ISO 8601, GET→405/404, positions-after-refresh, **+ no-network invariant** |
| `TestRefreshMocked` (pre-existing) | 5 | "fetched" status, reload_data called/skipped, 502 on RuntimeError/ValueError |
| `TestRefreshLive` (new, opt-in) | 1 | Real CelesTrak refresh; skipped unless `RUN_NETWORK_TESTS=1` |

---

## Lessons Learned

- **`TestRefresh` was the source of the earlier flaky `test_epoch_age_is_reasonable`.** Its unmocked
  refresh refetched live data mid-suite (order-dependent), occasionally swapping in a catalog object
  that tripped the old strict epoch bound. Mocking it removes the contamination at the root.
- **The no-network guard only bites when the cache is stale.** With a fresh (<2 h) cache, even the
  *real* `fetch` skips `_download` via the rate-limit guard — so the test can't detect a removed mock
  in that window. It still catches the dangerous case (stale cache → real network). Documented in the
  test docstring rather than over-claiming.
- **Return the real cached df, not a synthetic one** — keeps `satellite_count` consistent with
  `/api/satellites` so the existing consistency test keeps passing.

---

## Function Reference

### `_offline_fetch_patch()` (test helper, `tests/test_api.py`)
Returns an `unittest.mock` patcher for `app.state.propagator.fetcher.fetch` that yields the current
cached DataFrame unchanged → `/api/refresh` reports `"rate_limited"` offline. Usage:
```python
p = _offline_fetch_patch(); p.start(); self.addCleanup(p.stop)   # in setUp
with _offline_fetch_patch(): ...                                 # one-off call site
```

---

## Related

- Deeper context + decision in `progress/notes/week6_notes.md` and the "Testing Gotchas" section of
  `progress/notes/key_information.md`.
