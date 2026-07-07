# Task 9.3 — Point the frontend at `snapshot.json` + scale to ~11k

**Date:** Jul 3, 2026
**Status:** DONE
**Tests:** 563 backend (unchanged — frontend-only) + a **65-check Node harness** on the
real frontend sources (3× flake-clean, mutation-verified). Frontend has no pytest
per project convention.

---

## Goal

Cut the deployed frontend over from the FastAPI backend to the single static
`snapshot.json` (built in 9.2), so the site makes **zero API calls per visit** and
propagates all ~11k satellites **client-side** with `satellite.js`. Every existing
feature (info panel, orbit trail, nadir line, conjunction list, click-a-conjunction
→ TCA playback, type/label toggles, time controls) had to keep working, now driven
by in-browser SGP4 instead of `/api/*`. New UI: an "updated X ago" freshness line
linking the validation report.

**What did NOT change:** the local FastAPI backend still runs for dev; 9.3 only
changes where the *deployed* UI gets its data. The demo Jose ran
(`ORBITWATCH_LIVE=1 … python backend/main.py`) still works — its UI is untouched in
behavior, only its data source (API → snapshot + worker) is swapped.

---

## Approach + key decisions

- **Snapshot-only, no fallback.** Deleted every `fetch('/api/…')`; the frontend
  reads one file. One data path means 9.6's "zero backend calls" check passes by
  construction. Backend stays a local-dev/validation/CI tool.
- **satellite.js 6.0.1, pinned CDN** (jsdelivr, like Cesium). `json2satrec(omm)` is
  its preferred init (9.2 already chose OMM for exactly this). Cross-validated
  **0.00 m vs our C++ SGP4** on identical elements — the browser renders what our
  engine computes.
- **Web worker for the catalog batch.** `propagation-worker.js` propagates all N
  sats off the main thread and returns a **transferable Float32Array** of ECEF
  positions (zero-copy). The main thread keeps the old speed-adaptive cadence +
  lerp — the worker just replaces the `fetch`. Measured **~25 ms per 11k batch**,
  so it sits idle most of the time. Float32 (~0.5 m resolution) is deliberate:
  below a pixel for the dots; precision-sensitive readouts use float64 on the main
  thread.
- **Dots render direct ECEF** (`eciToEcf` → ×1000 → Cesium `Cartesian3`), not
  lat/lon. Geodetic (`eciToGeodetic`) is computed only for the one selected sat's
  info panel.
- **Lazy labels above 400 sats.** `PointPrimitiveCollection` does 11k points in one
  draw call (known-good pattern), but `LabelCollection` rasterizes glyphs per label
  and does not. So labels are created on demand — selected sat + conjunction pairs
  (`ensureLabel`). The one visible behavior change at scale.
- **Trails stay on the existing TEME + `computeGmst` pipeline** — satellite.js
  `propagate` output *is* TEME (same as the old track API), so densify/re-rotate/
  dual-primitive code is untouched; only the source changed from `fetch` to a local
  loop over one satrec.
- **Metadata derived in-browser, not shipped.** period/apo/peri come from Kepler's
  3rd law on `MEAN_MOTION` with the **same WGS-72 constants as the backend**
  (`GM_EARTH=398600.8`, `R_EARTH=6378.135`) — verified bit-faithful to
  `_derive_orbit_params` across all 300 sats (Δ = the parquet rounding quantum).

---

## Implementation

| File | Change |
|------|--------|
| `frontend/js/snapshot-data.js` | **new** — data layer: load/index `snapshot.json`, `_deriveMetadata` (OMM→period/apo/peri/incl), `_adaptConjunction` (compact schema→UX field names), satrec cache, `computePositionGd`/`computeEcefAt`/`computeTrackTEME` single-sat helpers, `_isoToEcma` timestamp normalizer, `snapshotReady` promise |
| `frontend/js/propagation-worker.js` | **new** — web worker: `importScripts` pinned satellite.js, `json2satrec` per object with per-sat error sentinels (mirrors `propagate_batch`), batch → transferable Float32Array ECEF + Uint8 ok-mask |
| `frontend/js/satellites.js` | **rewritten** — worker round-trip replaces the API poll; same cadence + lerp; lazy labels (`ensureLabel`); time-jump snap; ok-mask handling |
| `frontend/js/info-panel.js` | position/speed/lat-lon via `computePositionGd`; trail via `computeTrackTEME`; nadir line guarded on `entry.ok` |
| `frontend/js/conjunctions.js` | events from snapshot; TCA orb via `computeEcefAt` (sat1→sat2 fallback); freshness line + validation-report link; header from `meta` |
| `frontend/js/controls.js` | null-label guard + ok-mask folded into visibility |
| `frontend/index.html` | + pinned satellite.js; **relative paths** (GitHub Pages subpath); snapshot-data in load order |
| `frontend/css/style.css` | `#conjunction-freshness` styling |

---

## Validation

**Cross-validation (the important part):**
- **satellite.js vs our C++ SGP4:** 0.00 m on identical OMM (`json2satrec`/`sgp4`
  same tsince).
- **`computePositionGd` vs the full backend** (C++ SGP4 + GMST + SPICE geodetic),
  4 sats @ a fixed instant: **alt Δ 0.0 m, lat/lon Δ 1.7e-5° (~2 m), speed Δ
  0.00 mm/s** — two independent geodetic implementations agreeing sub-pixel.
- **Metadata vs backend `_derive_orbit_params`** (all 300): period/apo/peri Δ =
  parquet rounding quantum; name/type/inclination identical.
- **Worker ≡ main-thread ECEF** within 0.2 m (float32 transfer quantum).

**In-browser (Playwright, served by a plain static server, no FastAPI running):**
- **Network tab: zero `/api/*` or CelesTrak calls** — only local files + pinned CDN + tiles.
- 300-sat: list/header/freshness correct; conjunction click → clock to TCA−5 min +
  orb + 2 trails + pair labels; sat click → correct panel (7.62 km/s, 43°,
  apo/peri); toggles + LIVE + focus-clear all pass; clean console (bar a favicon 404).
- **11k perf: 36 fps steady** on Intel UHD 620; **paused FPS = animating FPS**
  (our animation overhead is unmeasurable — ceiling is Cesium's base globe render);
  worker 167 ms init / ~25 ms per batch; select-at-11k 76 ms. **No object-budget
  cut needed** (the roadmap's "show fewer if choppy" lever went unused).

---

## Test coverage (Node harness — `test_9_3_data_layer.js`, session scratch)

Exercises the **real shipped sources** (`eval` with stubbed browser globals — no
reimplementation) against backend reference values. 65 checks:

| Section | What it locks |
|---------|---------------|
| `_isoToEcma` matrix | 10 timestamp shapes (6/1/0-digit × naive/Z/offset, 9-digit, garbage) → strict-ECMA form + instant shift < 1 ms |
| `loadSnapshot` | real file → all indexed, every EPOCH/tca/generated_at normalized + parseable |
| metadata vs backend | 300 sats bit-faithful to `_derive_orbit_params` |
| conjunction adapter | 71 events field-by-field + both pair ids resolvable (the 9.2 guarantee) |
| position cross-val | `computePositionGd` vs C+++SPICE: alt 0.0 m / latlon ~2 m / speed 0 mm/s + LEO sanity bounds |
| failure paths | unknown ids → null/[] everywhere; track = steps+1 pts at orbital radius |
| **real worker file** | drove its message protocol with a broken record → sentinel count, ok-mask, worker≡main ECEF |

**Mutation-checked:** reintroducing the Safari bug (`slice(0,3)`→`(0,6)`) → 13
failures; dropping the worker sentinel → the count check fails (and usefully
revealed defense-in-depth: `propagate` independently rejects the bad satrec, so the
ok-mask check still bit). Both reverted, 3× green.

---

## Lessons learned

- **The Safari/iOS timestamp trap (caught in review).** Our snapshot carries 6-digit
  microsecond timestamps (deliberate — python-sgp4's strict OMM loader requires the
  fractional field). WebKit's native `Date` parser rejects any fractional-second
  count that isn't **exactly 3 digits**, and **satellite.js's `json2satrec` parses
  `EPOCH` with `new Date` internally** — so an un-normalized snapshot would silently
  init every satrec with a NaN epoch on Safari desktop + all iOS browsers, producing
  an empty globe with no error (the `!pv.position` guards don't catch NaN-filled
  positions). Fix: `_isoToEcma` truncates EPOCH/tca/generated_at to millisecond
  precision at load (≤ 1 ms ≈ ≤ 8 m along-track, sub-pixel). **V8/Chrome is lenient
  here, so this is invisible in local dev — it only bites on WebKit.** Verify on a
  real iPhone after 9.4.
- **A worker-zero-filled slot must not enter the lerp.** The worker zero-fills failed
  sats; naively copying that into lerp targets makes a recovered dot *sweep from
  Earth's center*. `updatePositions` now keeps last-good on failure and snaps on
  recovery; the nadir line's `CallbackProperty` checks `entry.ok` (normalizing
  (0,0,0) is a divide-by-zero).
- **satellite.js `propagate` returns null for decayed/diverged sats but NOT for
  NaN-epoch satrecs** — it returns a truthy object of NaNs. Null-checks catch the
  former, not the latter; timestamp hygiene is the only guard for the latter.
- **Float32 for render, Float64 for readout.** The transferable batch is float32
  (fast, ~0.5 m, sub-pixel for dots); the info panel / trails / TCA orb recompute in
  float64 on the main thread so numbers are exact. Worker≡main agreement is 0.2 m,
  which is the float32 quantum, confirming the split is clean.
- **`opsmode` mismatch is harmless here.** Our C++ engine runs AFSPC `'a'`,
  satellite.js defaults to `'i'` (improved) — empirically 0.00 m on our LEO data,
  and display-vs-screen agreement isn't load-bearing (conjunctions are precomputed
  offline). Would matter for deep-space; our shell is LEO.
- **Relative paths are mandatory for GitHub Pages.** A project site serves under
  `user.github.io/OrbitWatch/`, so `/js/app.js` 404s — must be `js/app.js`.

---

## Function reference (`snapshot-data.js` public surface)

- `snapshotReady: Promise` — resolves when `snapshot.json` is loaded + indexed; the
  render modules `.then()` off it (each with its own `.catch()`).
- `getSatName(noradId) -> string` — display name, or `NORAD <id>` fallback.
- `getSatrec(noradId) -> Satrec|null` — cached satellite.js satrec (nulls cached too;
  callers null-check).
- `computePositionGd(noradId, timeMs) -> {lat, lon, alt_km, speed_km_s, epoch_age_days}|null`
- `computeEcefAt(noradId, timeMs) -> Cesium.Cartesian3(m)|null` — for the TCA orb.
- `computeTrackTEME(noradId, startMs, durationMin, steps) -> Cartesian3[](m)` — TEME
  orbit track (rotated to ECEF by the caller's single GMST).
- Globals populated on load: `snapshotMeta`, `snapshotSatellites`,
  `snapshotConjunctions`, `satelliteMetadata` (Map).

Worker protocol: `{type:'init', satellites}` → `{type:'ready', n, nFailed}`;
`{type:'compute', timeMs}` → `{type:'positions', timeMs, positions:Float32Array(3N),
ok:Uint8Array(N)}` (ok[i]=0 → propagation failed, slot zero-filled).

---

## Deferred / remaining risk

- **Real Safari/iOS verification** — fix follows documented WebKit behavior + the
  harness proves strict-ECMA output, but no WebKit on this machine.
- **Real ~11k `active` catalog perf** — measured on a synthetic 11k fixture; the real
  fetch happens in CI (9.5, VPN-free). Local verification used the cached LEO shell.
- **`suppressed_count`** — no longer in the header (not in the snapshot schema); add
  to `meta` in a future snapshot bump if wanted.
- **favicon.ico 404** — cosmetic; add in 9.4.
- **Lazy labels persist after deselect** — bounded by clicks, real names, harmless.
- **Rest-of-9.1 polish** (severity colors, TCA countdown, list sort) still open —
  independent of the snapshot cutover.
- **Harness promotion** — the Node harness could become a CI contract check in 9.5
  (would catch a snapshot-format regression before deploy); currently session-scratch.
