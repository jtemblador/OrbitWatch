# Task 6.9 — Dataset Wiring (dense Starlink shell + demo crosser)

**Date:** Jun 13, 2026
**Status:** DONE
**Tests:** 374 passing, 1 skipped (was 356 after 6.7) — +18 across the 6.8/6.9 work

---

## Goal

Give the screener a population dense enough to exercise it realistically, and guarantee at least
one visible conjunction for the demo. Done two ways: a real **single Starlink shell (~300 sats)**
sliced from the live catalog, plus a deterministic **synthetic crosser** seed so a conjunction
always exists regardless of dataset.

---

## Approach

- **Env-selectable dataset, defaults unchanged.** `ORBITWATCH_GROUP` (default `stations`) +
  `ORBITWATCH_DEMO_SEED=1`. Switching the app's catalog off `stations` would break ~dozens of
  ISS-specific tests, so the suite stays on stations+no-seed; demo modes are opt-in via env.
  - `ORBITWATCH_DEMO_SEED=1 python backend/main.py` → stations + crosser (works out of the box)
  - `ORBITWATCH_GROUP=starlink_shell ORBITWATCH_DEMO_SEED=1 python backend/main.py` → dense shell
- **`slice_to_shell`** picks the single densest (inclination, mean-altitude) bucket — one real
  shell where coarse survival ≈100% and the medium filter does the real work. Server-side orbital
  filtering doesn't exist on the GP API (query only by CATNR/INTDES/GROUP/NAME), so we fetch the
  whole `starlink` group and slice client-side.
- **`append_demo_crosser`** clones row 0 of *any* loaded catalog (RAAN +180°, MA +180.2°) → a
  guaranteed crossing partner. Real catalogs rarely contain a *visibly dramatic* close approach in
  a given window (genuine misses are small; constellations are station-kept), so the seed makes the
  demo reliable. **dtype care:** uses `df.iloc[[0]]` (DataFrame slice) not `iloc[0].to_frame().T`,
  which would upcast the whole served catalog's columns to `object`.
- **`build_synthetic_shell`** generates a deterministic OMM-shaped dense shell with no network — the
  vehicle for a 300-sat scale test on CI.
- **Network hardening (the fetch was broken in some envs).** Rewrote `_download` to use
  **requests + certifi** (proper TLS) with a **curl fallback** on SSL/connection error; 4xx is
  re-raised as `urllib.error.HTTPError` so the 403/404 no-retry policy is unchanged. This fixed a
  real `UNEXPECTED_EOF_WHILE_READING` handshake failure (root cause turned out to be a VPN
  intercepting TLS). Also: `build_starlink_shell(json_path=…)` parses a browser-saved JSON as a
  network-free escape hatch. Re-enabling proper cert verification (vs the old unverified context)
  is a security improvement.
- **Cache-only groups.** `fetch()` serves the cached parquet for a group with no CelesTrak source
  (e.g. `starlink_shell`) instead of erroring → `POST /api/refresh` is a no-op, not a 502.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/tle_fetcher.py` | `slice_to_shell`, `build_starlink_shell` (live + `json_path` + injectable `fetcher`), robust `_download` (requests→curl) + `_download_via_curl`, cache-only `fetch()` branch, `starlink-shell` CLI |
| `backend/core/demo_seed.py` | NEW — `append_demo_crosser`, `build_synthetic_shell`, `DEMO_CROSSER_ID` |
| `backend/core/propagator.py` | `seed_demo` flag applied in `_ensure_data` |
| `backend/main.py` | `ORBITWATCH_GROUP` / `ORBITWATCH_DEMO_SEED` env wiring; sys.path + absolute-frontend-path fixes so `python backend/main.py` works from any cwd |
| `backend/data/tle/starlink.json` | raw Starlink download (gitignored) — permanent source for offline rebuilds |

---

## Validation

- **Real Starlink shell, full pipeline:** 301 sats (incl. crosser), 24 h @ 50 km = **2.57 s**,
  668 events — **607 of them natural** Starlink-vs-Starlink conjunctions. Closest:
  **STARLINK-5969 × STARLINK-5771 at 0.34 km, 1.26 km/s** (a real intra-shell near-miss). This is
  the payoff: real data produces real conjunctions, not just the seed.
- **`slice_to_shell`** selected a coherent real shell (inc≈43°, alt≈483 km) from 10,544 objects.
- **Cross-validation:** the screener reuses `fine_filter`, anchored to 6.6's 0.01 s brute-force
  reference; no new propagation math.
- **Scale test (synthetic, deterministic):** 301 sats, 6 h = ~1.75 s, crosser event flows, RTN
  norm == miss.
- **Offline rebuild** from the saved JSON reproduces the 300-sat shell.

---

## Test coverage

| Test class | File | Covers |
|-----------|------|--------|
| `TestSliceToShell` (5) | test_gp_fetcher | densest-shell pick, cap, determinism, empty, from-JSON build |
| `TestCacheOnlyGroup` (2) | test_gp_fetcher | cache-only serve w/o network; unknown-no-cache raises |
| `TestDownloader` (3) | test_gp_fetcher | requests 200→text, 4xx→HTTPError (no curl), SSLError→curl |
| `TestDemoSeed` (6) | test_propagator | append row, idempotent, no-op empty, orbit-shape offsets, dtype preserved, propagator seed flag |
| `TestDenseShellScale` (1) | test_conjunctions | 300-sat synthetic shell screens error-free + conjunction flows |
| `TestSeededScreenIntegration` (1) | test_conjunctions | seeded stations catalog yields a crosser event |

Frontend (`conjunctions.js`) has no automated tests by project convention.

---

## Lessons learned

- **No server-side orbital filter on CelesTrak's GP API** (CATNR/INTDES/GROUP/NAME/SPECIAL only) —
  to get one shell you fetch the whole group and slice locally.
- **Real conjunctions are rare per snapshot** — a uniform synthetic grid *over*-produces them
  (worst-case for the fine stage); a real station-kept shell still yielded 607 in 24 h here, but
  that's not guaranteed in every window, which is why the seed exists.
- **A VPN can break TLS to CelesTrak** (`UNEXPECTED_EOF_WHILE_READING`); requests+certifi / curl are
  far more robust than raw urllib + an unverified context.
- **`df.iloc[[0]]` vs `df.iloc[0].to_frame().T`** — the latter upcasts every column to `object` on
  concat. Use the DataFrame slice to preserve dtypes.
- **CelesTrak limits (verified Jun 2026):** 250 MB/IP/day (was 100), one download per 2 h update,
  >50 HTTP 301/403/404 in 2 h → firewall, CSV now the gp.php default (we pass FORMAT=json).

---

## Known limitation (tracked → Phase 7)

`starlink_shell` is a **static derived snapshot** — it doesn't auto-refresh (cache-only), and the
app has **no scheduled refresh at all** (only manual `POST /api/refresh`), so data ages until
rebuilt. Fine for the Phase-6 demo (current data); SGP4 drifts ~5–10 km/day. Fix path
(`scaling_tracker.md #2, #5`): scheduled auto-refresh + screen the live `starlink`/`active` group
directly at scale so the static slice disappears. Rebuild meanwhile:
`python -m backend.core.tle_fetcher starlink-shell backend/data/tle/starlink.json`.

---

## Function reference

```python
# backend/core/tle_fetcher.py
slice_to_shell(df, max_sats=300, inc_tol_deg=1.0, alt_tol_km=25.0) -> DataFrame
build_starlink_shell(max_sats=300, json_path=None, fetcher=None) -> DataFrame

# backend/core/demo_seed.py
append_demo_crosser(df) -> DataFrame          # +1 crossing partner (NORAD 9900001), idempotent
build_synthetic_shell(n=300, ...) -> DataFrame  # deterministic dense shell, no network
```
