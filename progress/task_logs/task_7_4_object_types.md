# Task 7.4 — Enable type filters (object_type from the CelesTrak name)

**Date:** Jun 23, 2026
**Status:** DONE
**Tests:** 429 passing, 1 skipped (was 421) — +8 (name derivation, edge/NaN,
parse + cached-load paths, API chain lock)

---

## Goal

Make the PAYLOAD / ROCKET BODY / DEBRIS display filters actually function. The
frontend (`controls.js`) was already complete and wired — the blocker was
upstream: **`object_type` was `None` for every satellite**, because CelesTrak's
`gp.php` (OMM/GP JSON) omits `OBJECT_TYPE` (it's a SATCAT field). Everything
collapsed to `"UNKNOWN"`, so `controls.js` rendered zero checkboxes.

---

## Approach

- **Derive `object_type` from the object NAME** — CelesTrak's GP names follow a
  consistent convention: a standalone `DEB` token → DEBRIS, an `R/B`-prefixed
  token → ROCKET BODY, everything else → PAYLOAD. **Token-matched, not substring**
  (so `DEBUT`/`ARABSAT` aren't mislabeled). Chosen over a CelesTrak SATCAT join
  (the authoritative source) because it's self-contained — it works on the GP
  data we already fetch, needs no new dependency, and the *default* `stations`
  catalog already mixes payloads with a `FREGAT DEB`, so it demos immediately.
  SATCAT remains the documented authoritative upgrade if alert types ever need to
  be certifiable.
- **Fill at every read path, fill only nulls.** A real `OBJECT_TYPE` (from
  sup-gp/Space-Track) is preserved; only nulls are derived. Applied in all three
  fetcher read paths (`_parse_json`, `load_cached`, `_load_if_fresh`). Deriving on
  `load_cached` means the **existing cached parquets get real types with no
  re-fetch** — so the offline test suite and the demo work immediately. One column
  fixed at the source propagates everywhere (positions, `/api/satellites`,
  conjunction events).

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/tle_fetcher.py` | `_derive_object_type(name)` (token-matched, non-string → PAYLOAD); `_ensure_object_type(df)` staticmethod (fast no-op when fully typed, else one copy + fill nulls from the name); called in `_parse_json`, `load_cached`, `_load_if_fresh` |
| `tests/test_gp_fetcher.py` | `TestObjectType` (7): convention, token-vs-substring, empty/None/NaN, null-name-no-crash, parse-derives, parse-preserves-real, cached-load-fills; updated `test_optional_metadata_defaults_to_none` (object_type is now derived, not None) |
| `tests/test_api.py` | `test_object_type_is_derived_not_all_unknown` — the full-chain lock |

No frontend change — `controls.js` was already complete; it just never received
non-UNKNOWN types.

---

## Validation

- **Unit:** `ISS (ZARYA)`/`STARLINK-1008` → PAYLOAD, `FREGAT DEB`/`COSMOS 2251 DEB`
  → DEBRIS, `SL-16 R/B`/`FALCON 9 R/B` → ROCKET BODY; `DEBUT SAT`/`ARABSAT` →
  PAYLOAD (token-match guard); `None`/`NaN`/`""` → PAYLOAD (no crash).
- **Chain (automated + manual):** cached `stations` load → propagator meta →
  `/api/satellites` → **24 PAYLOAD / 1 DEBRIS** → `controls.js` `meaningfulTypes`
  = `[DEBRIS, PAYLOAD]` → the two checkboxes render. Conjunction events inherit
  the same column, so alerts now read real types instead of "UNKNOWN vs UNKNOWN".
- **No cross-validation applies** — no propagation/transform added, and gp.php
  carries no `OBJECT_TYPE` to check against (the heuristic is the source).
- **Full suite:** 429 passing, 1 skipped — offline/deterministic.

---

## Test coverage

| Test (file) | Covers |
|------|--------|
| `TestObjectType.test_derive_by_name_convention` (test_gp_fetcher) | payload / rocket body / debris by name |
| `…test_token_match_not_substring` | `DEBUT`/`ARABSAT` → PAYLOAD (no substring false-positive) |
| `…test_empty_or_none_name_is_payload` | `""`, `None`, `NaN` → PAYLOAD |
| `…test_null_object_name_does_not_crash` | null name surviving a parquet round-trip (NaN) doesn't crash the fill |
| `…test_parse_json_derives_when_absent` / `…preserves_real_type` | derive when null; never overwrite a real OBJECT_TYPE |
| `…test_load_cached_fills_null_object_type` | pre-7.4 cache (null types) filled on load |
| `TestSatelliteList.test_object_type_is_derived_not_all_unknown` (test_api) | the full fetcher→propagator→API chain surfaces meaningful types |

---

## Lessons learned

- **gp.php omits `OBJECT_TYPE`** — it's a SATCAT field, not an OMM/GP one. The
  fetcher's `rec.get("OBJECT_TYPE")` was silently `None` for every object, which
  is why the type filters never appeared. CelesTrak's *naming convention* (`DEB`,
  `R/B`) is the type signal carried in the GP feed itself.
- **Fill on load, not just on fetch** — deriving in `load_cached`/`_load_if_fresh`
  (not only `_parse_json`) means existing caches and the offline test fixtures get
  real types with no rebuild. A fetch-only fix would have left the committed
  parquets (and the tests) stuck on null.
- **`(name or "").upper()` is a NaN trap** — `float('nan')` is truthy, so it
  reaches `.upper()` and crashes; `isinstance(name, str)` is the safe gate.

---

## Remaining risks / deferred

- **Heuristic ~99%, not authoritative** — a CelesTrak **SATCAT join** (option B)
  is the certifiable upgrade if conjunction-alert types ever need to be; deferred.
- **Single-type catalogs** — Starlink is all PAYLOAD (one checkbox); a
  launch/debris group shows all three. `stations` shows two. Demo note, not a bug.
- **Conjunction-line hiding** — when a type is filtered, the conjunction lines/list
  rows touching a hidden satellite aren't yet hidden (step 4, deferred frontend
  polish).

---

## Function reference

```python
# tle_fetcher.py
_derive_object_type(name) -> str    # "ROCKET BODY" | "DEBRIS" | "PAYLOAD"
                                    #   token-matched from a CelesTrak GP name
GPFetcher._ensure_object_type(df) -> df   # fill null object_type from object_name;
                                          #   preserve a real OBJECT_TYPE; idempotent
# Called in _parse_json / load_cached / _load_if_fresh, so every read path
# yields real types. One column → frontend filters + conjunction-event types.
```
