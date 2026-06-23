# Task 7.0 — Live & epoch-matched data

**Date:** Jun 22, 2026
**Status:** DONE
**Tests:** 381 passing, 1 skipped (was 374 after Phase 6) — +5 (live mode / freshness)

---

## Goal

Stop screening a stale static snapshot. Make the screener run on **freshly-fetched, epoch-current**
data (fetch-on-demand), and surface **how fresh** it is — the prerequisite for Phase 8's
epoch-matched SOCRATES comparison. Per the roadmap split, the **background scheduler** is deferred
to 9.7; 7.0 is fetch-on-demand + freshness visibility.

---

## Approach

- **Live fetch-on-load.** New `SatellitePropagator(live=True)` makes `_ensure_data` call
  `GPFetcher.fetch(group)` (re-downloads only past CelesTrak's 2 h cache, serves cache otherwise,
  falls back to cache offline) instead of `load_cached`. Off by default → the suite stays offline.
- **In-app shell slice.** `max_sats=N` slices the loaded catalog to one dense shell
  (`slice_to_shell`) so the live `starlink` group (~10.5 k) stays tractable. Phase 7.1 lifts the cap.
- **Freshness, two distinct signals.** `data_freshness()` returns `last_fetched` (when we
  downloaded) and `max_epoch_age_days` (oldest orbital epoch — what actually drives SGP4 accuracy).
  Both surface on `/api/conjunctions`. Drop the "live" label per Jose — just show when last fetched.
- **Env-selectable, defaults safe.** `ORBITWATCH_LIVE=1` + `ORBITWATCH_MAX_SATS=N` in `main.py`;
  defaults keep tests on cached `stations`.
- **Index invariant hardened.** `_ensure_data` now `reset_index(drop=True)` after slice/seed, so the
  `df.iloc[label]` lookups in `_build_indexes`/`find_*` always see a 0..n-1 RangeIndex (previously
  it just relied on Parquet returning one).

**"Live" semantics:** fresh **at load** + on manual `POST /api/refresh` — NOT continuously
auto-refreshing (that's the 9.7 scheduler). `last_fetched` surfaces the aging so it's visible.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/propagator.py` | `live` + `max_sats` ctor flags; `_ensure_data` live fetch + slice + reset_index; new `data_freshness()` |
| `backend/models/schemas.py` | `ConjunctionResponse` += `last_fetched`, `data_max_epoch_age_days` |
| `backend/routers/satellites.py` | `/api/conjunctions` surfaces freshness from `data_freshness()` |
| `backend/main.py` | `ORBITWATCH_LIVE` / `ORBITWATCH_MAX_SATS` env wiring |
| `backend/data/tle/starlink.parquet` | local cache (gitignored) built from `starlink.json` so live mode works offline for dev |

---

## Validation

- **Verified live on real data** (built `starlink.parquet` from the saved JSON to exercise the path
  without network): live mode loaded the live `starlink` group (10,544) → sliced to 300 (+crosser)
  → screened 617 events; surfaced `last_fetched` = now, `data_max_epoch_age_days` = **13.67**. That
  gap (fresh file, 13-day-old *epochs*) is exactly the distinction the freshness fields expose, and
  why a real network fetch matters.
- **Tests:** live fetches via `fetch` not `load_cached`; default uses cache; `max_sats` slices and
  preserves the 0..n-1 index; `data_freshness` reports a positive epoch age; endpoint returns the
  freshness fields and they flow through deterministically (crosser stub). All mocked → offline.

---

## Test coverage

| Test | File | Covers |
|------|------|--------|
| `TestLiveModeAndFreshness` (4) | test_propagator | live fetch vs cache, in-app slice + index invariant, freshness fields |
| `TestConjunctions` (updated) | test_api | endpoint surfaces `last_fetched` + `data_max_epoch_age_days`; deterministic values via crosser stub |

---

## Lessons learned

- **`last_fetched` ≠ epoch age.** Fetch time is when *we* downloaded; epoch is when the *orbit was
  measured* (often older). Epoch age is the meaningful staleness metric for SGP4 — surface both.
- **"Live" without a scheduler = fresh-at-load only.** `_ensure_data` caches `self._df`, so the
  fetch happens once per reload; continuous freshness is the 9.7 scheduler. The freshness fields make
  the staleness visible in the meantime.
- **`reset_index` after any slice/concat** keeps the positional `iloc[label]` lookups correct.

---

## Remaining risks (tracked)

- **Live + no cache + no network → clear `RuntimeError` (500).** Edge case (fresh clone, offline);
  covered by **roadmap 9.4** (Docker data bootstrap — fetch on first startup).
- **Fetching ~10 k Starlink to keep 300** — bounded by the 2 h cache; **7.1** lifts the slice cap.

---

## Function reference

```python
SatellitePropagator(group="stations", fetcher=None, seed_demo=False,
                    live=False, max_sats=None)
SatellitePropagator.data_freshness() -> {"last_fetched": str|None,
                                         "max_epoch_age_days": float}
# Live dense demo:
#   ORBITWATCH_LIVE=1 ORBITWATCH_GROUP=starlink ORBITWATCH_MAX_SATS=300 \
#   ORBITWATCH_DEMO_SEED=1 python backend/main.py
```
