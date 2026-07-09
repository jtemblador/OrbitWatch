# Key Information & Actionable Findings

**Purpose:** Quick-reference list of critical facts, decisions, and gotchas discovered during research. Consult this before and during each task to avoid mistakes.

---

## Critical Rules

1. **TLE mean elements MUST be propagated with SGP4/SDP4 — no exceptions.** Using TLEs with any other propagator (numerical integrator, different analytical model) gives WORSE results, not better. The encoding (element fitting) and decoding (propagation) are mathematically coupled. (Source: STR#3, p.1)

2. **SGP4 outputs TEME coordinates, not J2000 or ECEF.** TEME (True Equator Mean Equinox) is an approximate frame that doesn't rotate with Earth and isn't aligned to a standard inertial axis. Must convert before use.

3. **SPICE does NOT know the TEME frame.** Tested: `sp.pxform('TEME', 'J2000', et)` → `SPICE(UNKNOWNFRAME)`. We handle TEME→ECEF ourselves via GMST rotation, then hand off to SPICE for ECEF→geodetic only.

4. **Use WGS-72 constants for SGP4, WGS-84 for geodetic.** Different purposes:
   - WGS-72: gravity model used by NORAD when fitting TLE elements → must match for SGP4
   - WGS-84: Earth's physical shape → used for ECEF→lat/lon/alt conversion (same as GPS)

5. **CelesTrak rate limiting is strict.** Data updates every 2 hours max. Do NOT retry on 403/404 — they will IP-block. **250 MB/IP/day cap** (verified Jun 2026 — was 100 MB; also: >50 HTTP 301/403/404 errors in 2 h → firewall; re-downloading identical data before an update → 403). Our GPFetcher already enforces the 2-hour cache + no-retry-on-403/404, so we're compliant. **CSV is now the gp.php default (since 2026-05-09)** — we explicitly pass `FORMAT=json`, so unaffected. No server-side orbital-parameter filter exists (query only by CATNR/INTDES/GROUP/NAME/SPECIAL) → to get one shell, fetch the full group and slice client-side. (Source: celestrak.org/NORAD/documentation/gp-data-formats.php)

6. **Near-Earth vs Deep-Space split at 225 minutes orbital period.** Period < 225 min = SGP4 (near-Earth). Period >= 225 min = SDP4 (deep-space, adds lunar/solar perturbations). Modern implementations merge both under "SGP4" automatically. The Python `sgp4` library handles this transparently.

7. **gp.php omits `OBJECT_TYPE`** (it's a SATCAT field, not an OMM/GP one) — so the fetched `object_type` is `None` for every object and the frontend type filters never render. **Phase 7.4 derives it from the CelesTrak NAME convention** (`_derive_object_type` in `tle_fetcher.py`): a standalone `DEB` token → DEBRIS, an `R/B`-prefixed token → ROCKET BODY, else PAYLOAD — token-matched so `DEBUT`/`ARABSAT` aren't mislabeled. `_ensure_object_type(df)` fills **only nulls** (a real `OBJECT_TYPE` from sup-gp/Space-Track is preserved) and runs on **every read path incl. `load_cached`**, so existing caches get types with no re-fetch. `(name or "").upper()` is a NaN trap (`float('nan')` is truthy → `.upper()` crashes) — gate with `isinstance(name, str)`. Authoritative typing = a CelesTrak **SATCAT** join (deferred).

---

## Coordinate Transform Pipeline (RESOLVED)

**What we originally planned:**
```
SGP4 (TEME) → precession/nutation → J2000 → SPICE → ITRF93 → geodetic
```

**What we actually built (simpler, sufficient accuracy):**
```
SGP4 (TEME) → GMST Z-rotation → ECEF → SPICE recgeo → geodetic
```

**Why this works:** TEME and ECEF share the same Z-axis (Earth's pole). The only difference is Earth's spin angle (GMST). One matrix multiply converts between them. The original plan required precession + nutation + Earth rotation — three steps with more error surface. Going TEME→ECEF directly is one step.

**What we skip (and why it's fine):**
- Polar motion corrections: ~10m error (SGP4 is ~1 km)
- Equation of equinoxes (GMST→GAST): ~30m error (SGP4 is ~1 km)
- Both corrections are dwarfed by SGP4's inherent accuracy limit

**Velocity transform:** Includes the ω×r correction (Earth's angular velocity × position). Without this, ECEF velocities would be wrong by ~0.46 km/s at LEO.

**Implemented in:** `backend/core/coordinate_transforms.py`
**Tested with:** ISS, CSS Tianhe, FREGAT DEB (eccentric), HTV-X1, Crew Dragon — all 30/30 stations pass

---

## Resources On Disk

| What | Path | Use For |
|------|------|---------|
| **Vallado's C++ SGP4** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/` | Reference implementation for our C++ propagation engine (Task 2.3) |
| **SGP4 test cases** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/` (sgp4-ver.tle) | Validation test suite — input TLEs + expected output positions |
| **Original STR#3 PDF** | `misc/spacetrk/spacetrk.pdf` | Mathematical reference for SGP4 equations |
| **Original FORTRAN** | `misc/spacetrk/SGP4.FOR`, `SDP4.FOR`, `DEEP.FOR` | Math reference only — do NOT compile (1980 FORTRAN IV) |
| **Revisiting STR#3 paper** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-Rev3.pdf` | Bug fixes, corrections, technical details |
| **STR#3 summary** | `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753-summary.pdf` | Change notes per language, build instructions |
| **SPICE kernels** | `backend/data/spice_kernels/` | naif0012.tls, pck00011.tpc, earth_latest_high_prec.bpc |
| **SFS Handbook (Spaceflight Safety)** | `progress/week6and7_planning/SFS_Handbook_For_Operators_V1.7.pdf` + copy in `misc/` | Source of RTN screening volumes (Phase 7) + Pc method (de-scoped) — 18/19 SDS operator doc |

---

## Vallado C++ Source (Wrapped in Task 2.3)

**Source:** `misc/Revisiting Spacetrack Report #3/AIAA-2006-6753/sgp4/cpp/SGP4/SGP4/SGP4.cpp` (3,247 lines)
**Wrapped into:** `orbitcore/src/SGP4.cpp` + `orbitcore/include/SGP4.h`
**Bindings:** `orbitcore/src/bindings.cpp` (pybind11)

The consolidated `SGP4.cpp` contains everything (sgp4unit + sgp4ext + sgp4io merged into one file under `SGP4Funcs` namespace). We do NOT use `twoline2rv()` — we init from OMM fields directly via `sgp4init()`.

Key functions exposed to Python:
- `orbitcore.sgp4init(whichconst, opsmode, satnum, epoch, bstar, ndot, nddot, ecco, argpo, inclo, mo, no_kozai, nodeo)` → `Satrec`
- `orbitcore.sgp4(satrec, tsince)` → `((x,y,z), (vx,vy,vz))` in TEME (km, km/s)
- `orbitcore.jday(yr,mo,dy,hr,mn,sec)` → `(jd, jdFrac)`
- `orbitcore.getgravconst(GravConst.WGS72)` → dict of gravity constants

All bug fixes in the code are marked with the comment keyword **`sgp4fix`** — search for it to see every correction vs original STR#3.

**⚠️ `SGP4.cpp` is invisible to plain `grep`:** the file contains a stray binary byte (plus CRLF
line endings), so `grep` silently treats it as binary → zero matches even for text that's there.
Use **`grep -a`**. (Cost ~10 min of "where is the error handling?!" confusion in Task 6.1.)

**`sgp4()` clears `satrec.error` at the start of every call** (SGP4.cpp:1779) — a failed
propagation (decay, bad elements) does NOT poison later calls on the same Satrec. Load-bearing for
batch sentinel semantics.

---

## Batch Propagation — `orbitcore.propagate_batch` (Task 6.1)

```python
results = orbitcore.propagate_batch(satrecs, tsince_list)
# -> list[ ((x,y,z),(vx,vy,vz)) | None ]   TEME, km / km·s
```

- `tsince_list` is **per-satellite** minutes from each sat's own epoch (epochs differ!):
  `tsince_k = (jd_target − (jdsatepoch_k + jdsatepochF_k)) × 1440`
- `None` at an index = that satellite failed (e.g. decayed); neighbors unaffected; the failed
  Satrec is reusable at other times (error flag resets per call).
- Items are passed **by reference** — caller's Satrecs mutate (`t`, `error`), same as single `sgp4()`.
- Errors at the boundary: length mismatch → `ValueError`; non-Satrec item → `TypeError` naming the index.

**⚠️ pybind11 None→nullptr gotcha:** `obj.cast<T*>()` converts `None` to `nullptr` WITHOUT
throwing (reference casts throw `cast_error` instead). Any pointer-cast in bindings must
nullptr-check or it segfaults. Caught + regression-tested in Task 6.1.

**Perf reality (measured, min-of-3):** batch ≈ **1.05×** a Python loop over `orbitcore.sgp4()`
(2.08 vs 2.20 ms / 1,000 props) — sgp4 compute dominates and Python tuple construction remains
per-sat. Python-facing batching is NOT where the scale win is; keep hot loops (conjunction medium
filter) entirely inside C++. Future levers if Python-facing throughput matters: NumPy array
output + GIL release.

---

## Coarse Conjunction Filter — `orbitcore.coarse_filter` (Task 6.2)

```python
pairs = orbitcore.coarse_filter(periapsis_km, apoapsis_km, pad_km)
# -> list[(i, j)]  i<j, row-major. Touching bands pair; gap <= pad_km pairs.
```

- Bands come from `Satrec.alta/altp × Satrec.radiusearthkm` **or** the parquet
  `apoapsis`/`periapsis` columns — verified consistent to ~0.5 km (different Earth-radius
  constants; pad absorbs it).
- NaN bands match nothing (IEEE); length mismatch / negative pad → `ValueError`.

**⚠️ pybind11 boundary-conversion cost (measured at 6,000 sats):** the O(N²) scan is **40 ms**,
but returning 5.4M survivor pairs costs **~2 s** (378 ns/pair tuple conversion). Rule: don't
materialize huge C++ result sets as Python objects if they're headed back into C++ — the medium
filter (6.3) should run the coarse cut internally at scale.

**Catalog-shape caveat:** "coarse filter kills most pairs" holds for mixed-altitude catalogs
(LEO→GEO), NOT within a single constellation shell (one shared band → ~100% survival).

---

## Medium Conjunction Filter — `orbitcore.medium_filter` (Task 6.3)

```python
rows = orbitcore.medium_filter(satrecs, pairs, jd_start, jd_end, step_sec, threshold_km)
# -> list[(i, j, jd, distance_km)]  one row per close-approach window per pair
```

- **Time-major scan**: each sat propagated once per step (positions/velocities cached), pair
  distances from cache. 300 sats / 44,850 pairs / 24 h @ 60 s = **0.68 s**. Phase-7 dense ≈ 70 s.
- **Per-sat epoch handled internally** — pass absolute JDs; tsince computed per satrec. The
  per-sat-epoch gotcha lives in ONE place.
- Row jd/d = best **sampled** point of the window; true min within ±1 step (fine-filter bracket).
  Crossing pairs repeat (one row per node-pass encounter). GIL released during the scan.

**⚠️ THE detection trap (why naive time-stepping is wrong):** a crossing pair at v_rel ~12–15 km/s
can have a true miss of 8 km yet sample at **520/200 km** on a 60 s grid — plain `d < threshold`
silently misses the most dangerous conjunctions. medium_filter flags interval [t_k, t_k+1] when
`min(d_k, d_k+1) − v̂_rel·(Δt/2) − 3.3e−4·Δt² < threshold` (v̂ from endpoint velocities; margin =
gravity-gradient curvature). Adaptive: co-orbital neighbors (v_rel≈0) aren't spam-flagged.
Anything replacing/porting this filter must preserve an equivalent no-skip bound.

**Fixture for crossing encounters** (used in tests, found by brute-force search): ISS TLE vs clone
with **MA +180.2°, RAAN +180°** → true miss 8.0 km at tsince 122.717 min, v_rel 12.0 km/s; 8
windows per 6 h. Search trick: `propagate_batch([sat]*K, times)` = full track in one call.

---

## RTN Transform — `teme_to_rtn` (Task 6.5)

```python
r_km, t_km, n_km = teme_to_rtn(pos_primary, vel_primary, pos_secondary)  # TEME, same instant
```

- Vallado **RSW** frame = the RTN frame real CDMs use: `R̂ = r/|r|` (radial out),
  `N̂ = r×v/|r×v|` (cross-track), `T̂ = N̂×R̂` (in-track). Right-handed `R̂×T̂ = N̂`.
- Orthonormal ⇒ `r²+t²+n² = |Δr|²` (cheap downstream sanity check).
- T̂ equals v̂ exactly only for circular orbits (v has a radial component when e ≠ 0) — don't
  write tests expecting along-v̂ offsets to be *purely* T.
- Position-only; for RTN relative velocity (CDM fields), project Δv onto the same basis.

## Fine Filter — `fine_filter` (Task 6.6)

```python
from core.conjunctions import fine_filter
out = fine_filter(satrec_a, satrec_b, jd_lo, jd_hi)
# dict: jd_tca, tca_utc, miss_km, rel_speed_km_s, pos/vel TEME states (both sats)
```

- Bracket = medium_filter row's jd ± one step. Edge-minimum → auto-widens once.
- Optimizes minutes-from-bracket-start (NOT raw JD — 2.46e6-scale variables have awkward
  tolerance/float behavior). Tolerance 0.6 ms.
- States feed `teme_to_rtn` directly.

**⚠️ Sampled distances overstate the miss for fast crossers:** d(t) ≈ √(d_min² + (v_rel·Δt)²) —
a 1 s grid at 12 km/s closing speed reported 8.14 km where the true minimum is 6.60 km. Any
validation (Phase 8 SOCRATES deltas) must compare **refined** minima, never grid samples.

**`invjday` can return sec == 60.0** at minute rollovers — route minutes+seconds through
`timedelta` instead of constructing `datetime(..., second=int(sec))`. (Factored into `_jd_to_utc`
in 7.3; `run_screen` calls it lazily, only for events that survive the report cut.)

## Batched Fine Stage — `fine_filter_batch` (Phase 7.3)

```python
from core.conjunctions import fine_filter_batch
out = fine_filter_batch(satrecs, rows, step_sec)   # rows = medium_filter output
# list aligned to rows: geometry dict (jd_tca, miss_km, rel_speed_km_s, pos/vel TEME) or None
```

- **What `run_screen` uses in production now** (7.3). `fine_filter` (scipy) is kept only as the
  **validation oracle** — the batch is cross-validated against it AND a 0.01 s brute-force grid.
- **TCA = root of the range-rate, solved by Newton.** The closest-approach instant is where
  `d(d²)/dt = 2·Δr·Δv = 0`. With `g'(t) = |Δv|² + Δr·Δa ≈ |Δv|²` (gravity-gradient `Δa` ~1e-4 of
  `|Δv|²` near an encounter), the step is `t ← t − (Δr·Δv)/|Δv|²` **seconds** (note units: `g` is
  km²/s, `g'` km²/s²; convert to days `/86400` before adding to a JD). Starts at the medium-filter
  sample (within one step of TCA), converges quadratically in ~3 steps; **5 fixed steps** used.
- **Why it's fast:** all windows take the same fixed step count → vectorizable → **one
  `propagate_batch` crossing per step** (GIL released) instead of per-window scipy. `propagate_batch`
  returns `Δr` AND `Δv`, so the derivative is free. Fewer propagations than scipy (~6 vs ~15–50).
  Measured **~3.7× faster fine stage** (same-machine A/B), **byte-identical event counts** at every
  scale — *the speedup changes the speed, not the answers*.
- **Gotchas:** `np.where(g/gprime, ...)` still evaluates `g/gprime` on decayed (NaN) windows and
  warns — use `np.divide(out=…, where=gprime>1e-9)`. Co-moving pairs (`|Δv|≈0`, docked) don't step
  (any instant equally close) → finite ~0 miss, handled downstream by co-located suppression. Decayed
  pair → `None` (the per-window drop seam run_screen skips). Chunked by `_FINE_CHUNK=20000` to bound
  memory (`scaling_tracker #7`: it holds a dict per non-decayed window before the cut → +0.7 GB at
  full catalog).
- **Don't just "batch scipy":** scipy is sequential per window (each eval picks the next) and can't
  vectorize across windows — reformulating the minimum as a *root-find on range-rate* is what makes
  the whole catalog step in lockstep.
- **Solver-vs-solver equivalence is only meaningful where the TCA is well-defined (Phase 7.5).** On a
  co-moving pair (`|Δv|≈0`) the distance objective is near-flat with many near-equal minima, so the
  scipy oracle and the batched Newton solve can land on *different* TCAs without either being wrong.
  Any oracle-vs-batch test must gate on a real crossing (`rel_speed_km_s > 1`) — comparing TCAs on a
  co-mover is a false-flake generator. The scale lock (`TestScaleRegression`) does this and is
  **mutation-checked** (inject +100 ms → all windows fail) so the green test provably bites. A
  *synthetic* shell also needs density (~300 sats + 100 km pad) to produce **natural** windows — at
  120 sats a single shell is too sparse and only a seeded crosser closes.

## Conjunction Screener + Endpoint — `run_screen` / `/api/conjunctions` (Task 6.7)

```python
from core.conjunctions import run_screen, ConjunctionScreener
events = run_screen(satrecs, meta, start_utc, duration_hours, threshold_km,
                    step_sec=60.0, pad_km=None)   # sorted ascending by miss
events = ConjunctionScreener(propagator).screen(start_utc, duration_hours, threshold_km)

GET /api/conjunctions?time=&duration_hours=&threshold_km=&step_sec=
  -> { count, screening_start, duration_hours, threshold_km, events: [ConjunctionEvent] }
```

- **Index alignment is THE contract.** `medium_filter` returns positional `(i, j)`, NOT NORAD IDs.
  `propagator.get_all_satrecs() -> (satrecs, meta)` builds both in one ordered pass so a result
  index maps to identity via `meta[i]`. `run_screen` guards `len(satrecs)==len(meta)` — a mismatch
  would silently attach the wrong satellites to a real event. `meta[k]` = `{norad_id, name,
  object_type, epoch_age_days, periapsis_km, apoapsis_km}`.
- **One threshold for medium-gross AND final report — safe by design.** Relies on 6.3's
  velocity-aware no-skip bound: nothing sub-threshold is dropped at that threshold, so `fine_filter`
  refines and we keep `miss < threshold`. Don't "inflate" the medium threshold.
- **`pad_km` defaults to `threshold_km`** for the coarse cut: `miss ≥ radial separation`, so a band
  gap > threshold can never produce a sub-threshold miss; a smaller pad would drop real conjunctions.
- **Failure handling:** sub-step window → `medium_filter` `ValueError` → endpoint 422; decayed sat
  in a fine bracket → `RuntimeError` caught per-row in `run_screen` (one bad sat can't kill a
  screen); empty/NaN-band catalog → no pairs → `count: 0` (not an error).
- **Perf baseline:** 25 stations, 24 h @ 60 s = **134 ms**. Fine stage batched in 7.3 (~3.7×; see
  `fine_filter_batch` above). Remaining scaling item: `scaling_tracker #3` (coarse→medium C++ memory
  fusion, deferred — the cap below removes its urgency).
- **Concurrency (7.3):** `/api/conjunctions` runs the screen in `run_in_threadpool` (medium_filter +
  propagate_batch release the GIL → the C++ scan runs on the worker thread, event loop stays free),
  guarded by a **413 cap** (`ORBITWATCH_MAX_SCREEN_SATS`, default 1500 — a full-catalog sync screen
  is 258 s and would hang the worker). **⚠ Shared-Satrec race:** `sgp4()` mutates each Satrec's
  `t`/`error`, and the position/track endpoints share the same `_satrec_cache`. A single module-level
  `_propagator_lock` (asyncio) serializes **all** propagation endpoints — not just screen-vs-screen
  but screen-vs-position (the frontend polls `/api/positions` during a screen). Adding concurrency
  means auditing *every* reader of the mutated cache, not only the path you moved off the event loop.
- **⚠ Live `stations` returns ~82 events dominated by docked ISS modules** (Soyuz/Dragon/Progress/
  Nauka at ~0 km, v_rel ~0). Real co-located objects, not a bug — co-orbital neighbors aren't
  spam-flagged by the adaptive bound, they're genuinely within threshold. Phase 7's asymmetric RTN
  screening volumes (+ a small min-miss floor) filter these. This is *why* RTN exists.

## Dataset Wiring + Demo Seed (Task 6.8/6.9)

- **Dataset is env-selectable** (so the test suite stays on the ISS-bearing `stations` group):
  `ORBITWATCH_GROUP` (default `stations`) + `ORBITWATCH_DEMO_SEED=1`. Run modes:
  `ORBITWATCH_DEMO_SEED=1 python backend/main.py` (stations + crosser, works out of the box) or
  `ORBITWATCH_GROUP=starlink_shell ORBITWATCH_DEMO_SEED=1 python backend/main.py` (dense shell).
- **`slice_to_shell(df, max_sats=300)`** (`tle_fetcher.py`): picks the single densest
  (inclination, mean-altitude) bucket — one real shell where coarse-filter survival ≈100% so the
  medium filter does the real work. Build the dev set with
  `python -m backend.core.tle_fetcher starlink-shell` → caches `starlink_shell.parquet`.
  **Requires network** (one live Starlink fetch; ~7–8k objects, well under the 250 MB cap).
- **`append_demo_crosser(df)`** (`demo_seed.py`): clones row 0 (any catalog) with RAAN+180°,
  MA+180.2° → a guaranteed crossing partner (NORAD `9900001`, "CROSSER (DEMO)"). Idempotent,
  no-op on empty. Applied in `propagator._ensure_data` when `seed_demo=True`. Why needed: real
  catalogs rarely have a *visibly dramatic* close approach in a given window (genuine misses are
  small; constellations are station-kept), so the seed guarantees one for the demo.
  **Measured:** on live stations the crosser hits **~8 km vs ISS at 12 km/s** (vs the 2024-fixture's
  6.6 km — differs because it clones the *current* ISS TLE). Exact miss is epoch-dependent → the
  viz uses a generous 100 km threshold; tests pin determinism on the fixed fixture instead.
- **Frontend `conjunctions.js`:** one fetch → top-left list (pair/miss/TCA) + orange connecting
  lines (CallbackProperty between the pair's live points). A conjunction line at TCA is invisible
  (objects are close by definition), so lines are drawn for the **widest-separation** flagged pairs
  (crossing geometry, currently far apart) while the list shows closest-first.
  `CONJ_MIN_VISIBLE_KM=0.05` km skips docked artifacts (<5 m) but keeps real sub-km conjunctions.

**⚠ Data freshness model (important — two distinct things):**
- The **2 h interval is a cache-TTL / rate-limit guard, NOT auto-refresh.** `GPFetcher.fetch()`
  serves cache if <2 h old (CelesTrak only publishes every ~2 h) and re-downloads only when called
  with a staler cache. **Nothing calls `fetch()` automatically** — the only trigger is manual
  `POST /api/refresh`. So data ages until someone refreshes; the app has no scheduler yet.
- **`starlink_shell` never refreshes at all** (cache-only derived snapshot; even `POST /api/refresh`
  re-serves the cached parquet). Rebuild via `python -m backend.core.tle_fetcher starlink-shell`.
- Fix path = **roadmap 7.0** (scheduled auto-refresh + screen the live `starlink`/`active` group
  directly) + `scaling_tracker.md #2, #5`. Prerequisite for Phase 8's epoch-matched validation.

**Live mode (7.0, DONE):** `SatellitePropagator(live=True, max_sats=N)` → `_ensure_data` calls
`fetch()` (fresh-if->2h-stale, cache fallback) instead of `load_cached`, then `slice_to_shell(N)`.
`ORBITWATCH_LIVE=1` + `ORBITWATCH_MAX_SATS=N` (defaults keep tests on cached `stations`).
`data_freshness()` → `{last_fetched, max_epoch_age_days}`, surfaced on `/api/conjunctions`.
⚠ **"Live" = fresh-on-load + manual refresh, NOT continuous** — `_ensure_data` caches `self._df`, so
it fetches once per reload; the auto-refresh scheduler is **9.7**. The freshness fields make the
aging visible. `_ensure_data` now `reset_index(drop=True)` after slice/seed so `iloc[label]` lookups
stay correct.
- **Downloader (data-layer fix):** `_download` uses requests+certifi, falls back to `curl` on
  SSL/connection error; 4xx → `urllib.error.HTTPError` (preserves 403/404 no-retry). A VPN can
  break the TLS handshake to CelesTrak (`UNEXPECTED_EOF_WHILE_READING`). Offline escape hatch:
  `build_starlink_shell(json_path=…)` parses a browser-saved JSON.

---

## Conjunction Screening at Scale — Profile (Task 7.1)

`run_screen(..., timings=dict)` is a **passive** profiling hook — pass a dict and it fills
`n_sats / n_pairs / n_windows / n_events` + `t_coarse / t_medium / t_fine`; `None` (default,
and what `/api/conjunctions` passes) is a byte-identical no-op. Driver:
`scripts/profile_screening.py` (sweeps N over the real Starlink parquet; `--source synth`
offline; `--full` for the whole catalog). It's also 7.3's before/after benchmark.

**Measured on the full 10,544-sat Starlink catalog. Three findings that reorder Phase 7:**

1. **⚠ Coarse altitude-band filtering barely culls within a constellation** — 45 % of all
   55.6 M pairs survive at a 25 km pad, **49 % at 50 km** (the endpoint default), only down to
   19 % at a tight 5 km. It's **inclination-blind**: Starlink stacks its 43°/53°/97° shells
   into ~475 km, so co-altitude survival is high regardless of plane. (The roadmap's
   "coarse eliminates the large majority" assumption only holds for *mixed-altitude*
   catalogs, LEO→GEO — not a dense LEO constellation.) The 25 M survivor tuples are the
   **memory** driver (4.5 GB peak), which is what the 7.3 C++ fusion (#3) buys back.

2. **⚠ The fine stage is the time bottleneck — 82–87 % of wall time at every scale**, driven
   by window count (300 sats → 14.7 k windows; full catalog → **1.43 M**), each window a
   Python `scipy.minimize_scalar`. The C++ medium scan is cheap by comparison (≤ 28 s even at
   full scale). So **7.2 (de-dupe + co-located/persistent-proximity suppression) is a perf
   lever**, not just output cleanup — fewer windows → less fine work — and 7.3's fine-stage
   batching is the *primary* speedup, not the footnote it reads as in the roadmap.

3. **Operating point: `MAX_SATS=300` = 2.6 s @ 24 h/50 km** (the demo default, validated).
   ~500 sats = 7.5 s (click-and-wait ceiling); the **full catalog = 258 s / 4.5 GB → batch
   only** until 7.2 + 7.3. **Footgun:** a plain `ORBITWATCH_GROUP=starlink` run with no
   `MAX_SATS` screens all 10,544 → that 258 s / 4.5 GB hang on the first `/api/conjunctions`
   (synchronous — scaling_tracker #4).

**Lesson:** *profile before optimizing.* The tracked scale item (#3 boundary) was the memory
driver; the wall time lived in the fine stage — a place the plan had as a one-line aside.

---

## Industry Screening Volumes — SFS Handbook V1.7 (Phase 7.2)

Authoritative model from a full re-read of `SFS_Handbook_For_Operators_V1.7.pdf` (HAC vs HAC = our
SGP4 self-screen). Full notes + page cites: `progress/week6and7_planning/sfs_handbook_summary.md`
(re-read addendum).

- **⚠ The screening volume is an RTN ELLIPSOID, not a box** (p.10: "ellipsoid and covariance
  screenings"). In-volume test on the fine-stage miss vector:
  ```
  (r / R)^2 + (t / T)^2 + (n / N)^2  <=  1
  ```
  The earlier "asymmetric box" framing was wrong. (The *reporting* criteria, Table 6, use a box —
  distinct from the screening *volume*.)
- **HAC Table 3 regimes** — keyed by **perigee**, all gated on **eccentricity < 0.25**. Semi-axes
  R/T/N (km); `gross` = largest semi-axis (see no-skip below):

  | Regime | Perigee | R | T | N | gross |
  |--------|---------|---|---|---|-------|
  | **LEO 1** | ≤ 500 km | 0.4 | 44 | 51 | **51** |
  | LEO 2 | 500–750 | 0.4 | 25 | 25 | 25 |
  | LEO 3 | 750–1200 | 0.4 | 12 | 12 | 12 |
  | LEO 4 | 1200–2000 | 0.4 | 2 | 2 | 2 |
  | Deep Space | 1300<P<1800 min, inc<35° | 10 | 10 | 10 | 10 |

  Our Starlink/stations data is **LEO 1**. Radial (0.4 km) is tight/well-determined; along- &
  cross-track (44/51) are loose (timing-dominated) — *why a single Euclidean threshold is wrong*.
- **⚠ No-skip invariant (medium filter):** the gross threshold passed to `medium_filter` must be the
  **largest semi-axis** = `max(R, T, N)` (51 km for LEO 1) — any point inside an ellipsoid has
  Euclidean norm ≤ its largest semi-axis. This is the box-corner `√(R²+T²+N²)` ≈ 67 km only if it
  were a box; it is not. Below 51 km, an in-ellipsoid event with a large along-track offset would be
  silently skipped.
- **Coarse pad shrinks → perf win:** coarse is altitude-band (radial), and the radial semi-axis is
  only 0.4 km, so `pad ≈ R + SGP4 drift margin` (a few km) — far tighter than the old flat 50 km,
  cutting survivors before the fine stage (the 7.1 bottleneck). Still no-skip: an in-ellipsoid event
  needs the two altitude bands to coincide within ~R.
- **Co-located suppression has direct SDS precedent:** 19 SDS **does not compute Pc when the
  relative speed is "too small"** (user-settable — Annex A, p.45). **Conservative-drop** policy:
  suppress if `v_rel < V_floor AND (miss < MIN_MISS_FLOOR OR shared launch designator)`. Same-launch
  objects share the **`YYYY-DDD`** international-designator prefix (the `object_id` column) — our
  parked-formation signal.
- **De-dupe to unique pairs:** `medium_filter` emits one row per flagged window per pair (a crossing
  repeats per node-pass), so collapse to one event per unordered pair = the closest approach.
  Reported `count` = at-risk pairs.
- **Output is geometric, never Pc:** TCA, RTN miss vector, relative speed, in-ellipsoid flag +
  regime label. No covariance → no Pc (project scope).
- **Frame:** **RTN = RIC = UVW** (handbook, Annex A): U = Radial, V = Transverse/In-track,
  W = Normal/Cross-track. Matches our `teme_to_rtn` (Vallado RSW). Collision plane (⊥ relative
  velocity) is Pc-only — not needed for the volume test.
- **Units when comparing to real CDMs/SOCRATES (Phase 8):** a CDM is in **meters / m·s⁻¹**; our API
  is **km / km·s⁻¹**. Pc method in CDMs is `FOSTER-1992`. Annex C has a real worked example,
  **STARLINK-61 vs COSMOS 1408 DEB** (a strong demo/validation narrative).

**Implemented (7.2, `backend/core/screening_volumes.py` + `conjunctions.py`):**
```python
regime_for(perigee_km, eccentricity, period_min) -> ScreeningVolume   # SFS Table 3
vol.contains(r, t, n)            # (r/R)^2+(t/T)^2+(n/N)^2 <= 1  (ellipsoid)
vol.circumscribing_radius()      # = max semi-axis = medium no-skip gross
# SFS path is the default when volumes given / threshold_km is None:
run_screen(..., volumes=[ScreeningVolume,...], suppress=True)         # ellipsoid + suppress + de-dupe
ConjunctionScreener(prop).screen(start, hours)                        # builds volumes from meta
GET /api/conjunctions            # no threshold_km -> SFS; a value -> legacy Euclidean
#   -> { count(=at-risk pairs), suppressed_count, threshold_km|null, events:[…, screening_regime] }
```
- **Conservative-drop constants** (`conjunctions.py`, tunable): `_V_REL_FLOOR_KM_S = 0.5`,
  `_MIN_MISS_FLOOR_KM = 0.05`. Suppress iff `v_rel < 0.5 AND (miss < 0.05 OR same YYYY-DDD launch)`.
- **Legacy Euclidean path preserved** (scalar `threshold_km`, no suppression/de-dupe) for the API
  override + the 18 pre-7.2 tests. `meta` now also carries `object_id`/`eccentricity`/`period_min`.
- **⚠ The SFS volume is SELECTIVE — it finds different, often FEWER conjunctions than a Euclidean
  cut.** Validated: real Starlink shell (300) → **7** unique pairs, all radially tight (r ≈ ±0.3 km)
  with ~26 km in-track — real co-altitude crossings at 26–36 km *Euclidean* miss that a 25 km
  Euclidean cut **misses**. Live stations → **0 events, 46 suppressed** (docked modules gone, was 57).
  The synthetic demo crosser is **excluded** (3.6 km radial ≫ 0.4 km axis) → the default `stations`
  demo shows 0 conjunctions; use a **real Starlink shell** for a populated demo (or `?threshold_km=`
  for the legacy view). "Close" means close *radially*, not in raw range.
- **Coarse pad stays = circumscribing radius (51 km, no-skip).** Tighter *radial* pad (≈ R + drift)
  = a 7.3 perf item (needs a measured SGP4 radial-drift bound). MEO/HEO default to LEO 1 (pragmatic,
  not a no-skip bound — out of scope; our data is LEO).

---

**⚠️ Mocking lesson (6.0 escape, caught in 6.5):** "mocked the class" ≠ "mocked every call site."
One refresh test mocked `reload_data` but not `fetcher.fetch` — it stayed offline only while the
2 h cache was fresh, then silently went live mid-session (24 s network hang per run). Cache-TTL
crossings make network deps **time-dependent**: a suite can pass offline-clean for hours and then
start fetching. When auditing for network isolation, grep every call site, not every class.

---

---

## Python sgp4 Library (Already Installed)

The `sgp4` Python library by Brandon Rhodes wraps Vallado's C implementation. Key usage:

```python
from sgp4.api import Satrec, WGS72

# Initialize from OMM fields (no TLE string parsing needed!)
sat = Satrec()
sat.sgp4init(
    WGS72,              # gravity model (use WGS72, not WGS84)
    'i',                # improved mode ('i') vs afspc mode ('a')
    norad_cat_id,       # NORAD catalog number
    epoch_jd - 2433281.5,  # epoch in days since 1949 Dec 31
    bstar,              # drag term
    mean_motion_dot / (1440.0 * 2),  # convert to internal units
    0.0,                # mean_motion_ddot (not used)
    eccentricity,
    arg_of_pericenter * (pi/180),  # radians!
    inclination * (pi/180),         # radians!
    mean_anomaly * (pi/180),        # radians!
    mean_motion * (2*pi/1440),      # rad/min (convert from rev/day)
    ra_of_asc_node * (pi/180),      # radians!
)

# Propagate to a Julian date
e, r, v = sat.sgp4(jd_whole, jd_fraction)
# e = error code (0 = success)
# r = (x, y, z) position in km (TEME frame!)
# v = (vx, vy, vz) velocity in km/s (TEME frame!)
```

**Important:** All angular inputs to `sgp4init()` must be in **radians**. Our JSON data is in **degrees**. Must convert.

**mean_motion_dot conversion:** CelesTrak gives rev/day². The sgp4 library expects it divided by `(1440 * 2)` — this is the "ndot over 2" convention from the TLE format.

---

## Known Bugs We Must Avoid

These were fixed in Vallado's code but are present in many online SGP4 implementations:

1. **Kepler solver infinite loop** — high-eccentricity orbits can fail to converge. Vallado's code has iteration limits.
2. **Lyddane discontinuity** — position jumps at certain angles in deep-space orbits. Fixed with proper atan2 handling.
3. **Negative inclination at GEO** — low-inclination GEO satellites can get negative inclination from lunar/solar perturbations, causing position step-functions.
4. **Backwards propagation breaks** — original integrator only worked with increasing time. Vallado's code restarts from epoch each call.

**These are all fixed in the Python `sgp4` library and Vallado's C++ code.** Only relevant if we write our own implementation from scratch (which we should NOT do).

---

## SGP4 Accuracy Expectations

| Time from Epoch | Expected Error |
|-----------------|---------------|
| At epoch        | ~1 km         |
| 1 day           | ~5-10 km      |
| 3 days          | ~15-30 km     |
| 7 days          | ~50-100+ km   |

**Use the freshest TLE available.** CelesTrak updates every ~2 hours. Our 2-hour cache interval is correct.

For conjunction screening, accuracy matters most at close approach time. Always re-fetch TLEs before critical calculations.

---

## Data Quality & Validation (Implemented in GPFetcher)

**Records are skipped (not crashed) if:**
- `MEAN_MOTION <= 0` — physically impossible, would cause division by zero
- `ECCENTRICITY < 0` or `>= 1` — not a valid orbit (parabolic/hyperbolic)
- `EPHEMERIS_TYPE != 0` — non-SGP4 elements, incompatible with our propagator
- `DECAYED == 1` — re-entered objects produce underground positions
- Missing required fields (KeyError) — malformed CelesTrak record

**Epoch staleness (`epoch_age_days`):**
- Computed at fetch time: `(now - epoch).total_seconds() / 86400`
- Downstream code should flag objects with epoch_age > 3-5 days as unreliable
- For conjunction screening, always re-fetch before computing miss distances

**Cache safety:**
- Empty CelesTrak responses do NOT overwrite valid cached data
- Parquet writes are atomic (write to temp file, then rename)
- `fetch_time` column stored in UTC for consistent freshness checks

---

## JSON vs TLE: What We Chose and Why

| Concern | TLE | JSON/OMM |
|---------|-----|----------|
| Catalog numbers > 99999 | Breaks (~July 2026) | Supported |
| Numeric precision | Fixed-width truncation | Full floating point |
| Date format | 2-digit year + fractional day | ISO 8601 |
| Parsing complexity | Column-position dependent | Standard JSON |
| SGP4 compatibility | Direct input | Extract fields → `sgp4init()` |
| CelesTrak recommendation | Legacy | **Recommended for new code** |

**We use JSON.** Already implemented in `backend/core/tle_fetcher.py` (`GPFetcher` class).

---

## API Quick Reference

### Simple fetch (what we use now)
```
GET https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=json
```

### Single satellite by catalog number
```
GET https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json
```

### Advanced queries (useful for Phase 3-4 filtering)
```
GET https://celestrak.org/NORAD/elements/sup-gp.php?OBJECT_TYPE=PAYLOAD&PERIOD=<225&FORMAT=json
GET https://celestrak.org/NORAD/elements/sup-gp.php?EPOCH=>now-1&FORMAT=json
GET https://celestrak.org/NORAD/elements/sup-gp.php?CATNR=25544&FORMAT=TLE
```

No authentication needed for CelesTrak. Space-Track requires login (optional `cdm_public` cross-check in Phase 8).

---

## Conjunction Data Sources — SOCRATES + Space-Track (Phase 8)

**SOCRATES-Plus (CelesTrak) — PRIMARY validation source. Open access, NO account.**

- Computed with **SGP4 — the same propagator we use** → apples-to-apples, not a method mismatch.
  Specifically: **STK/CAT** (commercial) on STK's NORAD SGP4, via the **Alfano "smart sieve"**
  (perigee-apogee → path → time filter → fine TCA) + Alfano MaxProb (fixed covariance).
- Current run: all active payloads × full catalog → **148,008 conjunctions** (Jun 15, 2026), refreshed
  **3×/day**, **7-day** forward window, flags everything **within 5 km at TCA**.
- **How ours compares (Jun 24 research):** same SGP4, same perigee-apogee idea (`coarse_filter`), same
  time-step + fine-TCA. We **lack the smart-sieve "path filter"** (orbit-geometry min-distance pre-cut)
  — a *perf* gap (we time-step some pairs they skip), not a miss gap. We built our **own C++ SGP4 +
  screening** (vs. buying STK) — a strength. SOCRATES = simple **5 km sphere**; we have **both** 5 km
  Euclidean (matches SOCRATES) **and** SFS RTN-ellipsoid (matches 19 SDS) modes. We emit **no Pc**
  (SOCRATES's MaxProb uses *assumed*, not measured, covariance → our narrowing is honest, not a gap).
- **Bulk CSV = the canonical source (what we use, 8.1).** Each full run is published as a static
  **RFC-4180 CSV with a header row**: `https://celestrak.org/SOCRATES/sort-minRange.csv` (sorted closest
  first; siblings `sort-maxProb.csv`, `sort-TCA.csv`, `sort-relSpeed.csv`, `sort-SSC.csv` are the same run,
  re-sorted). One ~16 MB download = the entire ~148 k-row run; parse with `pd.read_csv`. 11 columns
  (verified Jun 24 vs `socrates-format.php`):
  `NORAD_CAT_ID_1, OBJECT_NAME_1, DSE_1, NORAD_CAT_ID_2, OBJECT_NAME_2, DSE_2, TCA, TCA_RANGE, TCA_RELATIVE_SPEED, MAX_PROB, DILUTION`
- **Query endpoint = useful *semantics*, not a transport we use.** `table-socrates.php?{Q}=…&ORDER=…&MAX=…`:
  `NAME=a,b` (substrings; **two values = pairwise "between A and B"**, e.g. `starlink,flock`), `CATNR=id,`
  (one object's conjunctions), `INTDES=yyyy-nnn` (by launch). `ORDER` ∈ {MINRANGE, MAXPROB, TCA, RELSPEED,
  SSC}, **`MAX` ≤ 1000**. ⚠ **Returns HTML only — `FORMAT=csv` is ignored** (confirmed); one request per
  slice. So we **don't query**: we download the bulk CSV once and reproduce the useful semantics locally —
  `top_n` (MINRANGE head), `by_name`, **`by_catnr`** (= CATNR), **`between`** (= NAME=a,b pairwise). The
  only thing **only** the query can do is **`INTDES` (launch filter)** — the intl-designator is **not a CSV
  column** — and we don't need it.
- ⚠ **`OBJECT_NAME` carries a bracketed operational-status suffix** (`[+]` active / `[-]` inactive / `[P]`
  partial / also `[?]`, `[B]`, `[X]` seen live) — strip the **trailing** bracket for the clean name (keep
  internal parens like `SHIYAN-21 (SY-21)`); it doubles as an active/inactive signal. `DSE` (per object) =
  *days from the GP epoch used to the TCA* (our epoch-match key). `TCA_RANGE` km, `TCA_RELATIVE_SPEED` km/s,
  `MAX_PROB` from a fixed 100/300/100 m covariance ellipse (confirmed — not real Pc), `DILUTION` km.
- ⚠ **Object 1 / object 2 are POSITIONAL, not primary/secondary** (format doc: *"the first of the two
  objects ... not what might be considered the primary"*). **Don't assume object 1 is the active payload** —
  either side can be the debris. All our filters check both positions.
- **We use:** both NORAD IDs, both `DSE`, `TCA`, `TCA_RANGE` (miss distance km), `TCA_RELATIVE_SPEED`.
  **We ignore:** `MAX_PROB`, `DILUTION` (Pc/covariance — deliberately out of scope).
- **Implemented (8.1):** `backend/core/socrates_fetcher.py` (`SOCRATESFetcher` — bulk CSV → Parquet cache,
  **8 h TTL**, `top_n`/`by_name`/`by_catnr`/`between`), on the shared `backend/core/http_fetch.py`
  downloader (requests→curl-TLS-fallback, also used by `GPFetcher`). Live: 147,814 conjunctions, 11.9 s
  fetch / 0.03 s cached. Don't fetch per-request — SOCRATES only updates ~3×/day.

**⚠️ Epoch-matching gotcha — and the fix (`DSE`, Jun 24 research):** SOCRATES screens *near-future*
windows from a specific TLE epoch (~3×/day). If we screen the same objects using TLEs from a
*different* epoch, the TCA and miss distance disagree from epoch drift alone (~5–10 km/day) — not from
any real method difference. **The fix is in SOCRATES's own output:** each conjunction reports
**`DSE` (days-since-epoch)** = the age of the elements it used to that TCA. So we fetch current
CelesTrak GP, compute *our* element's age at the SOCRATES TCA, and **compare to `DSE` → the epoch match
is verified, not assumed** (zero auth; filter/segment to small-`DSE` for tight agreement). Backstop for
the few that drift: **Space-Track `gp_history`** (query by epoch; ⚠ rate-limited **~30 req/min · 300/hr**,
verified Jun 24 → batch with comma-delimited `CATNR`, targeted pulls only, never bulk). The agreement-vs-`DSE` degradation curve is itself a Phase-8
finding (feeds the SGP4-uncertainty doc 8.4).

**Comparison logic — `socrates_compare.py` (8.2) + the live finding.** Two layers: **`match_events`**
(pure, reusable — Phase 9's badge) pairs our `run_screen` output to SOCRATES rows by `frozenset({id1,id2})`
+ TCA proximity (10-min match window; the reported Δ is the real agreement), → per-event TCA/miss deltas +
reproduction rate **bucketed by `DSE`**; **`compare_against_socrates`** (orchestrator) fetches GP per
object by `CATNR` (`max_objects=200` cap — `gp.php` has no multi-id, so a broad slice would fire thousands
of requests), builds satrecs via `propagator.build_satrecs_and_meta(df)`, screens the slice's TCA window in
**legacy 5 km Euclidean** (not SFS — SFS would suppress events SOCRATES lists), and sets `epoch_ok` per
event (our element age at TCA == `DSE`, both objects). **Live ISS result (the honest finding):** 3/9
reproduced; matched events agree **TCA <5 s / miss <0.32 km** (same-method SGP4 confirmed), but
**`epoch_ok=0`** — current `gp.php` has *already rolled* the epochs SOCRATES used (their `DSE` was 2.5–5.3 d),
so we can't byte-match the snapshot from the free feed. Misses cluster at higher `DSE` → reproduction
degrades with element age. **To lift reproduction: pull the exact older snapshot via `gp_history`** (8.3).
⚠ "Extra" our-events (e.g. secondary-vs-secondary when screening a primary + its secondaries) are *other
crossings*, NOT false positives. ⚠ object 1/2 are positional → matcher keys on a `frozenset`.

**The `gp_history` epoch-matched lever — 8.3, IMPLEMENTED & it works.** `backend/core/spacetrack_fetcher.py`
pulls each object's *epoch-matched* historical elset from Space-Track `gp_history` (the elset whose epoch
≈ `TCA − DSE`, i.e. the one SOCRATES actually screened from), so we reproduce SOCRATES on the **same
elements**. **Live, current-GP → epoch-matched (3 slices):** ISS 3/9 (33 %) → 8/9 (89 %); top-25-closest
8/25 (32 %) → **25/25 (100 %)**; Starlink-40 8/40 (20 %) → **40/40 (100 %)** — and **every matched event
agrees to ΔTCA = 0.000 s, Δmiss = 0.000 km** (byte-level same-method agreement — the headline Phase-8
result). Current-feed reproduction is low *purely from epoch drift*; the by-`DSE` curve confirms it (at
>3 d age current collapses while epoch-matched holds ~full). Key facts:
- **Space-Track auth = cookie session, NO API KEY.** POST `identity=…&password=…` to
  `https://www.space-track.org/ajaxauth/login`, reuse the cookie (`chocolatechip`). Creds from
  **`SPACETRACK_USER` / `SPACETRACK_PASS`** in a **gitignored `.env`** (the runner has a tiny built-in
  `.env` loader — quote-aware, strips unquoted `# inline comments`, never overrides a set env var). Never
  logged, never committed. Rate limits **<30 req/min, <300/hr** (a min-interval throttle; one bulk query
  per slice). `gp_history` is immutable → **Parquet-cache forever** ("1/lifetime" in their bandwidth table
  = pull-once-and-cache, NOT a one-shot quota).
- ⚠⚠ **Space-Track `format/json` encodes EVERY OMM field as a STRING** (`"MEAN_MOTION": "15.49"`), unlike
  CelesTrak which sends real JSON numbers. `GPFetcher._parse_json` does numeric comparisons, so the
  fetcher **must coerce** the numeric fields first (`_coerce_numeric`). The load-bearing one:
  `EPHEMERIS_TYPE` arrives `"0"`, and `"0" != 0` is `True` → **without coercion every record is silently
  dropped** (mutation-checked: 0 rows vs 1). Same field names/values otherwise (it IS the OMM standard),
  so the coerced records feed `_parse_json` unchanged → identical GP-frame schema.
- **`gp_history` query:** comma-delimited `NORAD_CAT_ID` **AND** a short `EPOCH/{d0}--{d1}` window (their
  guideline — never an unbounded history pull) + `format/json`. One bulk request covers a whole slice.
- **`BulkGPAdapter`** serves a pre-fetched frame through `compare_against_socrates`'s `fetch_by_catnr(nid)`
  seam so the 8.2 orchestrator runs **unchanged**: with epoch targets → each object's *nearest-epoch* elset
  (the lever); without → its *latest* (a Space-Track-sourced current baseline, the `--current-source
  spacetrack` path — resilient when **CelesTrak's TLS is blocked**, e.g. on a VPN, which it was during dev).
- **Runner:** `scripts/validate_socrates.py --epoch-matched [--current-source celestrak|spacetrack]`
  produces `validation/socrates_report.md` + figures, three slices (ISS / top-N closest / Starlink), each
  led by the current-vs-epoch-matched lift table + a grouped reproduction-by-`DSE` chart.

**Space-Track `cdm_public` — OPTIONAL stretch (Phase 8.6). Account required (Jose has one).**

- Real operational CDMs from the 18/19 SDS **SP (Special Perturbations)** pipeline — *higher
  fidelity than SGP4*, emergency/high-Pc events only.
- Fields: `CDM_ID, CREATED, TCA, MIN_RNG, PC, SAT_1_ID, SAT_1_NAME, SAT1_OBJECT_TYPE, SAT1_RCS,
  SAT_1_EXCL_VOL, SAT_2_ID, …` (mirrored for SAT_2).
- **Value:** a *cross-method* check (their SP vs our SGP4). If we also flag a `cdm_public` event,
  that's a strong "we catch real threats" story. **Detection-only — we do NOT use the `PC` field.**
- Also the better source for full-catalog/debris TLEs later (Phase 4 territory).

**Method provenance:** Our screening geometry (asymmetric RTN **ellipsoidal** screening volumes —
see "Industry Screening Volumes" section above) comes from the **Spaceflight Safety Handbook for
Operators** (a.k.a. SFS Handbook), the 18/19 SDS operator doc — on disk (see Resources table).
Summary in `progress/week6and7_planning/sfs_handbook_summary.md`.

---

## FastAPI Backend (Week 3)

**TestClient lifespan gotcha:** `TestClient(app)` at module level does NOT trigger lifespan events. Must enter context manager (`__enter__()`) for `app.state` to be populated. This affects any test that accesses endpoints depending on lifespan-initialized state.

**gp.php omits `OBJECT_TYPE`:** The simple `gp.php` endpoint does not return the `OBJECT_TYPE` field for any satellite. Only `sup-gp.php` or Space-Track provide it. API defaults to `"UNKNOWN"`.

**Stations group includes debris:** CelesTrak's "stations" group is not limited to crewed LEO stations. Includes rocket bodies and debris (e.g., `FREGAT DEB` at 2263 km apoapsis). Do not assume tight LEO altitude bounds for the entire group.

**`epoch_age_days` recomputation:** The value cached in Parquet is computed at fetch time and goes stale. API endpoints recompute it from `utcnow()` on each request.

**Scaling tracker:** `progress/scaling_tracker.md` centrally tracks all `iterrows()` and other Phase 3 performance items. Add entries there whenever flagging code with `# ⚠ PERF`.

**`+` in query strings decoded as space:** `?time=2026-03-24T12:00:00+00:00` breaks because `+` becomes ` `. Use `Z` suffix for UTC or `%2B` URL encoding. This affects any endpoint accepting ISO 8601 time params.

**Propagator `RuntimeError` must be caught at API layer:** Any endpoint calling propagator methods (`get_position_by_norad_id`, `get_positions_at_times`) must catch `RuntimeError` — SGP4 propagation can fail for decayed orbits. Batch endpoint handles this internally via `get_all_positions()` error collection; single and track endpoints need explicit try/except.

**`get_all_positions()` returns `(results, errors)` tuple:** Changed from returning just a list. Callers must unpack: `results, errors = propagator.get_all_positions(utc_dt)`.

**Fetch/serve separation:** GET endpoints always serve from local Parquet cache. Only `POST /api/refresh` triggers a CelesTrak fetch. No client request in the GET path ever directly contacts CelesTrak. Phase 3 will move the fetch to a background task (202 Accepted) + scheduled auto-refresh.

---

## Testing Gotchas (Catalog Coupling)

**[FIXED in Phase 6.0]** `TestRefresh` (test_api.py) used to hit LIVE CelesTrak and overwrite the
real `stations.parquet` — the refresh tests called `POST /api/refresh` unmocked, so every full-suite
run did a real network fetch and mutated production data (non-deterministic, network-dependent, risked
CelesTrak rate-limiting/IP-block). **Fix:** `_offline_fetch_patch()` + a `setUp` mock make the suite
run offline/deterministic; `test_refresh_makes_no_network_call` enforces the invariant; opt-in
`TestRefreshLive` (env `RUN_NETWORK_TESTS`) keeps a real end-to-end check. Suite no longer touches the
network or rewrites the Parquet (md5 verified unchanged). See `task_logs/task_6_0_mock_refresh_fetcher.md`.

**Tests must be robust to live-catalog churn.** The CelesTrak "stations" group changes membership
and freshness over time, and includes inactive debris/rocket bodies with older epochs. Two tests
were authored against a March snapshot and broke against the live June catalog:
- Cross-validation (`test_all_stations_match_python_sgp4`) propagated to a hardcoded past date;
  back-propagating a decayed object (`ISS OBJECT XY`) throws SGP4 error 6. → **Skip objects that
  fail to propagate** (a decayed object has no position to cross-validate); assert ≥1 validated.
- Epoch freshness (`test_epoch_age_is_reasonable`) required *every* object < 30 days. → Assert the
  *freshest* object is recent (proves the cache is live) and allow marginally future-dated epochs.

**Lesson:** don't hardcode dates or assume specific catalog contents in tests that run against live
data. Assert on invariants (implementation agreement, cache freshness), not on which objects happen
to be present today.

---

## Orbit Trail Rendering (Week 4 — Critical Gotcha)

**ECEF orbit trails visibly "bend" because Earth rotates under the satellite.** During one 93-minute LEO orbit, Earth rotates ~23° in longitude. If the track API only returns geodetic positions (lat/lon/alt), each point is in ECEF at its own time — a rotating frame that warps the orbital ellipse into a helix.

**Solution: return TEME positions from the track API.** TEME is an inertial frame — the orbit traces a clean near-ellipse with no Earth rotation warping. The frontend computes ONE GMST angle for "now" and rotates all TEME points to the current ECEF frame:

```javascript
// Julian Date from Unix timestamp
const jdNow = Date.now() / 86400000 + 2440587.5;
const T = (jdNow - 2451545.0) / 36525.0;
const gmstSec = 67310.54841
  + (876600.0 * 3600.0 + 8640184.812866) * T
  + 0.093104 * T * T
  - 6.2e-6 * T * T * T;
const gmst = (gmstSec * Math.PI / 43200.0) % (2.0 * Math.PI);
const cosG = Math.cos(gmst);
const sinG = Math.sin(gmst);

data.track.map(pt => {
  const x = pt.teme_x * 1000; // km → meters
  const y = pt.teme_y * 1000;
  const z = pt.teme_z * 1000;
  return new Cesium.Cartesian3(
     cosG * x + sinG * y,
    -sinG * x + cosG * y,
    z
  );
});
```

**Why this works:** A single GMST rotation places all TEME points into the same ECEF frame (Earth's current orientation). Since all points share the same rotation angle, no per-point Earth rotation correction is needed — the orbital geometry is preserved exactly as SGP4 computed it.

**Previous approach (replaced):** Per-point ECEF de-rotation (each point rotated by `dt × ω_earth` to undo Earth spin). This approximated the fix but introduced ~30 km error at the trail seam due to J2 precession not being accounted for. The TEME approach avoids this by never baking Earth rotation into the positions in the first place.

**Why the trail still won't perfectly close:** J2 perturbation causes RAAN and argument of perigee to precess (~0.3°/orbit, ~30 km at LEO). This is a real orbital effect that SGP4 models — the orbit genuinely doesn't close. The gap is invisible at normal zoom levels.

**GMST formula duplication:** The IAU 1982 GMST formula exists in both `coordinate_transforms.py` (backend) and `info-panel.js` (frontend). Both must use the identical formula. If one is updated, update the other.

**Dual-primitive rendering:** The orbit ring is rendered as two Cesium Primitives with the same positions — one with depth test ON (bright near-side arc, 0.8 alpha) and one with depth test OFF (faint far-side ghost, 0.2 alpha). This shows the full ring while making the front vs back visually distinct.

**Client-side densification:** 360 API track points are densified 10x to ~3600 points via spherical interpolation (lerp + normalize-to-radius). This keeps each Cartesian chord at ~12 km with <1 m sag, eliminating visible straight-line artifacts when using `arcType: NONE`.

---

## Cesium.js Frontend (Week 4)

**StaticFiles catch-all changes HTTP status codes:** Mounting `StaticFiles(directory="frontend", html=True)` at `/` means undefined routes return 404 from the static mount instead of 405 from FastAPI's router. This affects any test asserting 405 Method Not Allowed. Accept both 404 and 405 in tests.

**Use `PointPrimitiveCollection`, NOT Entity API:** Cesium Entity API has per-object overhead (picking, labels, property evaluation). AstriaGraph uses Entity + CallbackProperty for 17K objects and is laggy. `PointPrimitiveCollection` batches all points into a single GPU draw call. trackthesky.com uses this pattern for 9K+ satellites successfully.

**Cesium Ion token is client-side:** Unlike backend `.env` secrets, the Ion token is embedded in frontend JS (same as Google Maps API keys). Restrict by domain in Ion dashboard for production. Stored in gitignored `config.js` with committed `config.example.js` template.

**UHD 620 performance settings:** `terrain: undefined` (ellipsoid only), `resolutionScale: 1.0`, all default UI widgets disabled. These are the three biggest GPU savers for integrated graphics.

**Cesium label `FILL_AND_OUTLINE` causes distortion.** Text outline rasterization on label textures produces artifacts at oblique angles. Use `FILL` style with `showBackground: true` (translucent dark) for clean rendering. Also: `disableDepthTestDistance: Number.POSITIVE_INFINITY` defeats globe occlusion — remove it so labels behind Earth are hidden.

**`Cartesian3.fromDegrees(lon, lat, height)` — height is in meters.** API returns `alt_km`. Must multiply by 1000. Longitude is the first argument (not latitude).

**Cesium position setter copies the value.** A scratch `Cartesian3` can be reused across all primitives in a loop — Cesium copies on assignment, doesn't store the reference. Safe for lerp loops with a single scratch object.

**Cesium `PolylineFade` material does NOT exist.** The generic `Fade` material works on `PolylineCollection` via `materialInput.st` (s = 0→1 along length), but makes trailing portions fully transparent. For orbit trails, use solid `Color` material instead.

**`ScreenSpaceEventHandler` + `scene.pick()` for satellite click detection.** Returns the `PointPrimitive` with its `id` property (set to `norad_id`). Check `satellites.has(picked.primitive.id)` to confirm it's a satellite and not another primitive.

**Orbit trails render at orbital altitude using dual Primitives.** Near-side primitive (depth test ON, bright) shows only the camera-facing arc. Far-side primitive (depth test OFF, faint ghost) shows the full ring. This replaced the original surface projection approach (`clampToGround: true`) which didn't show the 3D orbital geometry. Client-side densification (360 → ~3600 points) eliminates chord artifacts from `arcType: NONE`.

**Track API returns TEME positions for orbit trail rendering.** The `teme_x/y/z` fields in the track response are raw SGP4 output in km (inertial frame). The frontend applies a single GMST rotation to place them in the current ECEF frame. See "Orbit Trail Rendering" section above for the full approach and GMST formula.

**Geodetic altitude varies ~18-19 km per orbit for nearly circular LEO.** Combination of orbital eccentricity (~7-14 km from apogee/perigee) and WGS-84 ellipsoid shape (~12 km, Earth flatter at poles). This is physically correct and verified by checking orbital radius at each track point.

**Data pipeline verified against public sources.** ISS position matches python-sgp4 reference to sub-millimeter. Speed matches wheretheiss.at API to 3 decimal places (7.657 vs 7.658 km/s). GMST (IAU 1982) matches Meeus formula to 0.00 arcseconds.

---

## Static-Site Frontend — client-side SGP4 (Phase 9.3)

The deployed frontend reads one file (`snapshot.json`) and propagates all sats in-browser with **satellite.js 6.0.1** (pinned CDN) — **no `/api/*` calls per visit**. Local FastAPI stays a dev/CI tool. Durable findings:

**satellite.js `json2satrec(omm)` ≡ our C++ SGP4 to 0.00 m** on identical elements (same Vallado math). `computePositionGd` (satellite.js `eciToGeodetic`) ≡ the backend's C+++SPICE pipeline to ~2 m lat/lon, 0.0 m alt, 0.00 mm/s speed. So the two engines are interchangeable for display; conjunctions are precomputed offline by the C++ engine (the authoritative one).

**⚠ WebKit/Safari `Date` rejects non-3-digit fractional seconds → silent NaN epochs.** The snapshot ships **6-digit-µs** timestamps (python-sgp4's strict OMM loader requires the fractional field). Safari desktop + **all iOS browsers** (WebKit-bound) return `Invalid Date` for any fractional-second count ≠ 3 digits — and **satellite.js `json2satrec` parses `EPOCH` with `new Date` internally**. Result: every satrec inits with a NaN epoch, the globe is empty, and **no error is thrown** (`propagate` returns a truthy object full of NaNs, so `!pv.position` null-checks DON'T catch it). **V8/Chrome is lenient → invisible in local dev.** Fix: `_isoToEcma` in `snapshot-data.js` truncates EPOCH/tca/generated_at to strict 3-digit ECMA at load (≤ 1 ms ≈ ≤ 8 m along-track, sub-pixel). Any new `Date.parse` on snapshot timestamps must go through it.

**`propagate()` returns null for decayed/diverged sats but NOT for NaN-epoch satrecs** (returns NaN-filled object). Null-checks guard the former; timestamp hygiene is the only guard for the latter.

**Web worker for the catalog batch.** `propagation-worker.js` propagates all N sats off-thread → **transferable Float32Array** ECEF (zero-copy) + Uint8 ok-mask. Main thread keeps the old speed-adaptive lerp; the worker just replaces `fetch`. **~25 ms per 11k batch** (167 ms one-time init). Float32 (~0.5 m) is deliberate for dots (sub-pixel); info-panel/trails/orb recompute in **float64** on the main thread. Worker≡main ECEF agreement = 0.2 m (the float32 quantum) confirms the split.

**A worker-zero-filled failed slot must NOT enter the lerp.** On propagation failure the worker leaves that sat's 3 floats at (0,0,0). Copying that into lerp targets makes a recovered dot **sweep from Earth's center**. `updatePositions` keeps last-good on `ok=0` and snaps on recovery; the nadir line's `CallbackProperty` checks `entry.ok` (normalizing (0,0,0) is a divide-by-zero).

**Lazy labels above 400 sats.** `PointPrimitiveCollection` does 11k points in one draw call, but `LabelCollection` rasterizes glyphs per-label and tanks at scale. So `ensureLabel(noradId)` creates labels on demand (selected + conjunction pairs only); the Labels toggle governs those. **Measured: 11k sats = 36 fps steady on Intel UHD 620, animation overhead unmeasurable** (paused FPS == animating FPS → the ceiling is Cesium's base globe render, not our code). The roadmap's "show fewer if choppy" lever went unused.

**Relative asset paths are mandatory for GitHub Pages.** A project site serves under `user.github.io/OrbitWatch/`, so absolute `/js/app.js` 404s — must be `js/app.js` (and `snapshot.json`, not `/snapshot.json`).

**opsmode mismatch is harmless for LEO display.** Our C++ engine runs AFSPC `'a'`; satellite.js defaults to `'i'` (improved). Empirically 0.00 m on our LEO shell; would diverge (~km) for deep-space, but display-vs-screen agreement isn't load-bearing (conjunctions precomputed).

---

## Deploy + display rework (Phase 9.4)

**Live at https://jtemblador.github.io/OrbitWatch/** — static, snapshot-driven, zero backend/CelesTrak calls per visit. Durable findings:

**A Cesium page runs hot because of the RENDER LOOP, not the object count.** By default Cesium redraws the globe at ~60 fps forever (pins GPU + a core → fans), even when nothing moves and even while paused. Fix: `requestRenderMode: true` + `maximumRenderTimeChange: Infinity` (we animate off our own simClock, not Cesium's clock), driven by a self-throttled 30 fps `requestAnimationFrame` loop (`animationTick` in satellites.js) that calls `scene.requestRender()` **only while playing AND the tab is visible** → the GPU goes fully idle on pause / tab-hide (headless-verified: 0 renders while paused). An empty globe would run just as hot, so filters/culling are a *secondary* lever.

**⚠ The `requestRenderMode` tax:** with on-demand rendering, ANY scene mutation that can happen while the animation loop isn't running (i.e. while paused) is NOT drawn unless something explicitly calls `viewer.scene.requestRender()` afterward. Every paused-time interaction needs one: select/deselect, group + label toggles, the trail checkbox, conjunction-focus clear. Camera drags and `flyTo` self-render. Two review rounds both circled missing-`requestRender` bugs — it's the easy one to miss.

**Display groups (9.4b):** 5 mutually-exclusive groups classified at load in snapshot-data.js from name (Starlink / stations / GNSS) then orbit regime (`_regime` mirrors backend `screening_volumes`: ecc ≥ 0.25 → HEO, 1300 < period < 1800 → GEO, perigee > 2000 → MEO, else LEO). Hiding a group sets `point.show=false` and `animationTick` skips it → a filter is a real per-frame perf dial. The worker still propagates hidden groups (~25 ms/5 s, negligible) so re-show is instant; on the hidden→shown transition `applyVisibilityState` snaps the point to `lerp(start,target,lerpFactor)` because the animation loop (which writes `.position`) is skipped while hidden and doesn't run while paused.

**CI screening capacity — the deploy's binding constraint.** The full active catalog is ~16k (15,913 fetched, ~12k screenable). The O(N²) SFS screen over that ran **20+ min at 24 h / 38+ min at 72 h on a shared 4-vCPU GitHub runner** — impractical for a repeating deploy. Capped at `--max-sats 5000 --hours 24` (5000 sats / 367 conj / 6.3 min / 0.32 MB gz), keeping **display == screen** so the "fully screened" claim stays complete. The lever to lift it is the **Phase-10 geometric path filter** (free, algorithmic — cuts the work, not just adds cores) or a self-hosted-runner cron; NOT a bigger Actions runner (self-hosted runners on a PUBLIC repo are a security hole — a fork PR can run code on your box).

**Deploy workflow reuse-vs-rebuild.** `deploy.yml` defaults to REUSE: `curl -fsSL` the live `snapshot.json` into `frontend/` and redeploy (~1 min, no C++/screen) — right for a CSS/JS change. REBUILD (~8 min: build `.so` + fetch + screen) is explicit via `workflow_dispatch rebuild_snapshot=true`, or forced when the reuse curl fails (first deploy). `curl -f` fails on HTTP errors AND truncated transfers → a corrupt snapshot can't ship. 9.5's cron plugs into the rebuild path. **`pyarrow` must be in requirements.txt** (the Parquet cache engine) — it was only in the local venv, so the CI build failed on `df.to_parquet` until added. The snapshot build is **SPICE-kernel-free** (uses `teme_to_rtn`, numpy — verified with kernels hidden), so CI needs no kernels.

**Cesium Ion token on a public site:** domain-restrict it to the bare **origin** (`https://jtemblador.github.io`), NOT a path (`.../OrbitWatch`) — browsers send only the origin as the cross-origin `Referer`, so path-scoping 401s. Inject from a repo secret at build time (env var, not string-interpolation). In practice Ion is never hit (base layer = CartoDB, terrain off), so any non-placeholder token renders — a domain-restricted token even works on localhost.

---

## Robot job + snapshot archive (Phase 9.5)

The scheduled CI job that keeps the site fresh and archives every published snapshot. All in `deploy.yml`. Durable findings:

**`git fetch origin <branch>` does NOT create `refs/remotes/origin/<branch>` under a narrow refspec** — which is exactly what `actions/checkout@v4` leaves (it fetches only the triggering ref). So `git worktree add -B data … origin/data` fails with "invalid reference." **Base the worktree on `FETCH_HEAD`** (a fetch of an explicit ref always sets it). ⚠ This bug is **invisible to a single smoke test** — the first run creates the branch (orphan path), and only the *second* run (branch now exists) hits the broken `origin/data` path. Reproduced locally with a narrow-refspec clone before trusting the fix.

**`git ls-remote --exit-code`: `0` = ref found, `2` = query OK but no such ref, anything else = a real error** (network/auth/DNS). Branch on all three — do NOT collapse "absent" and "error", or a transient blip is treated as "first run", fabricates a fresh orphan, and the no-force push is rejected as non-fast-forward. The error branch aborts (`exit 1`) with git's captured stderr (`err=$(git ls-remote … 2>&1 >/dev/null)` — the `2>&1 >/dev/null` order captures stderr while discarding the ref list).

**Orphan branch, version-portably:** `git worktree add --detach "$WT"` then `git -C "$WT" checkout --orphan data` + `git rm -rf .` — creates a true orphan (no `main` history → the append-only archive never bloats the code branch) without touching the main checkout's `frontend/`. (`git worktree add --orphan` only exists on git ≥ 2.42; the runner has it, local 2.39 didn't — the detach+checkout form works on both.) The archive filename is the snapshot's own `meta.generated_at` with colons→dashes (Windows-safe).

**A secondary/archival step must never gate the primary deliverable.** The archive is `continue-on-error: true` so a transient `data`-push failure can't block the live-site refresh (the good snapshot still ships). Decouple with `continue-on-error`, **not** step ordering — a failed job skips `deploy` (`needs: build`) regardless of where the step sits. A `continue-on-error` failure is *masked* (`conclusion: success`); surface it by gating a follow-up step on `steps.<id>.outcome == 'failure'` → `::warning::`.

**Scheduled-workflow gotchas:** (1) `schedule:` triggers fire **only from the workflow file on the default branch** — the cron does nothing until merged to `main`. (2) Cron is **UTC and best-effort**; **avoid `:00`** (GitHub's most-congested/most-delayed minute) — offset to `:17` etc. (3) GitHub **auto-disables** a repo's scheduled workflows after **60 days with no commits** — *silently* (no failed run). (4) A `GITHUB_TOKEN` push to a **non-default** branch (our `data`) does **not** re-trigger the workflow → no infinite loop.

**Job-level `permissions` REPLACE the top-level set** (they don't merge). Top-level `contents: read`; the `build` job re-lists `contents: write` + `pages: write` + `id-token: write` (least privilege — the `deploy` job inherits read-only). **Public repo = unlimited Actions minutes** (private = 2,000/mo, which 3×/day × ~8 min would blow) — a second reason the repo stays public.

**SOCRATES cadence for scheduling:** SOCRATES screens the same GP catalog we do **3×/day** (historically documented runs ~01:30 / 12:30 UTC; current exact times aren't published and CelesTrak is VPN-blocked locally). We trail it — the "Log SOCRATES run time" step curls the SOCRATES CSV's `Last-Modified` header each rebuild so the slots can be tuned to SOCRATES's *actual* observed upload times once the CI logs accumulate. The runner can reach CelesTrak (the local block was the VPN).

---

## Live-QA UI round (Phase 9.6)

User QA of the live site drove a big frontend round (search, conjunction-first views, heat fix #2). Durable findings:

**`requestRenderMode` ladder, rung 2 — gate `requestRender()` on actual visible change.** The 9.4 fix (30 fps rAF loop that idles on pause/tab-hide) is NOT enough: calling `requestRender()` on frames where nothing visible moved still redraws the whole globe — an **empty globe ran the fans hot on Chrome while playing** (Firefox is more forgiving). `animationTick` now sets a `moved` flag in the lerp loop and only requests a render if some visible point advanced. Measured: empty globe ≈ 1 render/1.5 s (was 30/s); with sats visible, normal. The full heat ladder: (1) requestRenderMode + idle on pause/hide → (2) render only on visible change.

**Masked worker batches × the ok-sentinel — three interacting gotchas.** The propagation worker takes an optional `mask` (Uint8Array) to skip sats (used by the "All conjunctions" view: 4,999→557 propagations/batch). But a masked-out sat comes back `ok=0`, indistinguishable from a decayed one, and `show = groupVisible && entry.ok !== false` — so: (1) **every mode *exit* must request one unmasked batch or re-enabled sats stay invisible**; (2) that request must go through **even while paused** (`refreshSatellites(force)` — propagating at a frozen sim time is valid and cheap); (3) a forced request arriving while `workerBusy` must be **queued, not dropped** (`pendingForcedRefresh`, re-fired when the in-flight batch lands) or a paused double-toggle strands stale `ok` until unpause. All three were review findings with repro-verified fixes.

**`contenteditable` Escape-cancel needs a flag consumed in the blur handler.** blur always fires (and commits); restoring the field *before* blur is gated off by the same `document.activeElement` check that prevents the ticker from fighting the user's typing. Escape sets `cancelTimeEdit=true` + blurs; the blur handler consumes the flag and restores instead of committing.

**Every view-mode transition must tear down cross-mode visuals symmetrically.** `setConjOnly` clears any active conjunction-focus visuals on *every* transition — the round-1 bug (enter Top-20 with a focus active → orb/trails float over the arcs) is the same class as 9.4's narrow-teardown lesson, in the enter direction instead of exit.

**Batched `Primitive` picking:** give each `GeometryInstance` an `id` object (`{conjEvent: e}`) → `scene.pick()` returns it → ~40 clickable arcs in ONE draw call. And **a single depth-tested polyline gets Earth occlusion for free** — the old two-primitive near/far "ghost ring" trail (depth-test-off far side) was clutter and cost; the globe's own depth buffer hides the far arc.

**Verification workflow that worked:** serve an isolated copy of `frontend/` + the *live* `snapshot.json` on `127.0.0.1`, drive it with **Playwright MCP** (`browser_evaluate` on the app's own globals — `satellites`, `conjEvents`, `setConjOnly` — plus screenshots for the visual calls), user eyeballs the same URL between rounds. Feature checks, perf counters (renders/s, propagations/batch), a 7-state mode-transition matrix, and a primitive-leak sweep all ran against the real data with zero test scaffolding in the repo.

---

## Phase 10.0 measurement gate — path filter vs time filter (Jul 8)

One day of NumPy against real catalogs re-scoped Phase 10 before any C++ was written. Durable findings:

**The path filter cannot cut a megaconstellation.** Two near-circular co-altitude orbits in different planes *intersect* at their mutual node line (both at ~the same radius on the same line) — their paths genuinely touch, their conjunctions are real, and no orbit-geometry test can drop them. That's the dominant pair class in any Starlink-heavy catalog. Measured (conservative bound, realistic margins): **0.002% / 1.9% / 0.4%** of coarse survivors dropped on Starlink-10,544 / active-CI-4,821 / active-full-15,708. The path filter only uniquely cuts eccentric-vs-circular pairs (bands overlap, node radii differ) — a minority of active payloads.

**The time filter (H-C-R Filter III) is the real sieve.** Approaches require *both* objects inside small angular windows `|sin u| ≤ D_eff/(r_p·sin I_R)` around the node line simultaneously; intersecting per-object transit-time intervals leaves **0.31–1.86%** of medium pair-step work (measured ceiling, ~54–320×).

**No-skip is an EVENT-level contract, not flag-level.** `medium_filter` flags on a conservative interval bound (subtracts `v̂·dt/2` — hundreds of km for fast pairs), so it routinely flags pairs with no true sub-threshold approach. A geometric filter dropping such a pair changes zero events. The gate's bound: **0 event-level violations everywhere** (4,246 medium-flagged pairs among its drops — every one fine-refined to miss > gross). Spec/validate Stage 1 against *events*, never *flags*.

**Production cascade shape (SFS / 24 h / 30 s, local):** 4,821 sats → 157 s = medium 81 + fine 74 (**~50/50**), 1.7 GB. 9,795 → 852 s = 368 + **479** (fine 56% and growing), **8.7 GB**. Full 15,708: 48.1 M coarse survivors ≈ 8.7 GB of pair tuples alone, total **~25 GB extrapolated → cannot fit the 16 GB CI runner** (the 9.4 "38 min" was memory pressure, not CPU). Consequences: the sieve alone caps at ~2× (fine is untouched); cap-lift needs the fine stage in C++ too (GIL-free, OpenMP, streamed — post-sieve fine is ~90% of a full screen).

**Mean-element geometry must be advanced to screen time.** Parquet elements are per-sat epoch snapshots; Starlink RAAN precesses ~5°/day and cache epochs were ~11 days old (~55° stale). Secularly advance Ω/ω (J2 rates, or the satrec's own `nodedot`/`argpdot` in C++) before any node geometry. Margins that made the bound safe: D_eff += ~10 km mean-vs-osculating (J2 short-period) + drift pad; node windows += `|Ω̇₁−Ω̇₂|·T·(1+2·sin i/sin I_R)` + 1% advance-model error; ν intervals += `|ω̇|·T`.

**Tools:** the prototype/oracle lives in `progress/week10_planning/path_gate.py` (+ `gate_sanity.py`, 6 hand-built geometry cases); `scripts/profile_screening.py` now profiles the exact CI operating point (`--source active --mode sfs --step 30 --hours 24 --start <now> [--fused]` — head-slice + screenable filter, matching `build_snapshot.py`, NOT the densest-shell slice).

**10.1b time-filter gate — the four ways the naive filter fails (all measured; the cures are the validated construction).** The H-C-R time filter was proven no-skip on 1.4 M real events (0 uncovered @ 0.25° margin, mutation bites) ONLY after fixing: **(1) the correctness contract is TCA-coverage, never flag-time** — medium flags are sampled steps ~1 step from the true crossing, so even an exact predicate "fails" there 85 k times; pad windows ±1 step in TIME. **(2) Anchor mean elements at screen start, never at epoch** — SGP4's drag-secular t² terms compound (35° along-track at a 21.8-day epoch age), and the satrec's own `mdot/argpdot/nodedot` do NOT fix it (the *local* rate at a stale age ≠ the at-epoch rate; hypothesis tested and disproved). Use chord finite-difference rates over the scan window from propagated states. **(3) The osculating (ω, M) split is ill-conditioned for near-circular orbits** (J2's forced ecc-vector wobble ~1e-3 ≈ e; an ordinary e=0.0011 sat broke the M-unwrap with margin-independent misses) — chord the equinoctial mean longitude λ=ω+M; keep the split only inside the equation-of-center, where error ≤ 2e. **(4) Flat margins can't handle reentering sats** (143 km perigee, ndot 0.127 rev/day² needs ~6°; normal sats ~0.1°) — measure each sat's drift curvature with a midpoint anchor (second difference; exact for isimp=1's quadratic drift). Debugging meta-lesson: every breakthrough came from chasing WHY violations clustered (one sat → conditioning; one pair → reentry) — aggregate counts alone say "widen the margin", the wrong fix. Oracle + spec: `progress/week10_planning/time_filter_gate.py` + `stage1b_timefilter_spec.md`. ⚠ For 10.2: per-step membership checks cost as much as the distance check they replace — the C++ must precompute per-pair TIME intervals.

**10.1a fused stage + the memory breakdown (measured — plan the rest of Phase 10 from this).** `orbitcore.screen_pairs(satrecs, periapsis, apoapsis, pad, jd_start, jd_end, step, threshold) -> (n_pairs, rows)` fuses the coarse cut and the medium scan in one GIL-released C++ call; the survivor pairs live only in C++, never as Python tuples. `run_screen(fused=True)` wires it; **byte-identical events** (proven on a real active slice, not just structurally). The validated 6.3 scan is now a shared `run_medium_scan` helper (medium_filter + screen_pairs both call it) — the refactor's safety net is medium_filter's own 143 tests. **The peak-RSS breakdown that matters for Stage 2:** the coarse→medium Python pair list is NOT the bulk of memory — measured ~0.35 GB (CI slice) / ~2.16 GB (10k) / ~5.4 GB (full 16k), while the whole screen is 1.7 / 8.7 / ~25 GB. The **bigger** term is the fine stage's per-window result dicts (`scaling_tracker #7`: ~5.36 M dicts at 10k). So `fused=True` gives 8.71→**6.55 GB** at 10k (−2.16 GB) and 1.71→1.34 GB at the CI slice — real, but the full catalog still needs the C++/streamed fine stage (10.4) to fit 16 GB. Lesson (again): measure the thing before claiming the win — the plan assumed the pair list dominated; it doesn't.

---

## Cesium CallbackProperty for Real-Time Tracking

**Use `CallbackProperty` when a visual element must track a satellite's interpolated position every frame.** The standard approach (updating in the 5-second fetch cycle) creates visible lag because the satellite moves between refreshes via lerp interpolation. `CallbackProperty` evaluates a function every render frame, reading the `PointPrimitive.position` directly — zero API calls, zero timing issues.

**Pattern (nadir line example):**
```javascript
positions: new Cesium.CallbackProperty(() => {
  const entry = satellites.get(noradId);
  if (!entry) return [];
  const satPos = entry.point.position;
  // ... compute derived positions from satPos ...
  return [groundPoint, satPos];
}, false)
```

The `false` parameter means the callback is NOT constant (positions change every frame). Cesium will re-evaluate it on each render.

**When to use:** Any Entity property that must track a moving satellite smoothly — nadir lines, range rings, connecting lines between objects, etc.

**When NOT to use:** Orbit trails (static geometry, refreshed every 30s) or anything that doesn't need per-frame updates.

---

## Simulated Clock and Speed-Adaptive Refresh

**Simulated time uses drift-free anchor arithmetic:** `baseSimTime + (wallElapsed × speed)`. Must re-anchor (`baseSimTime = getTimeMs(); baseWallTime = Date.now()`) before every state change (pause, resume, speed change) — otherwise the new speed/state applies retroactively to all elapsed time.

**Refresh interval must scale with clock speed.** At 60x, a fixed 5s interval means 300 simulated seconds between fetches. Linear lerp over 300s of curved orbital arc (~2,250 km for LEO) visibly cuts across the orbit. `max(5000/speed, 500)` keeps the simulated gap ≤30s at any speed. Use `setTimeout` loops (not `setInterval`) so the interval adapts when speed changes.

**Static ECEF primitives drift from live positions at high speed.** Orbit trail bakes a GMST angle at render time. As simulated time advances, Earth rotates but the trail doesn't. Fix: cache TEME positions from the last trail fetch and re-rotate with the current GMST on a throttled timer (500ms). Cost: ~3600 rotations + 2 primitive rebuilds — no API call.

**Single-GMST vs per-point GMST trade-off:**
- Single GMST: all trail points share one rotation angle → clean closed orbital ring, but rotates with Earth as time advances
- Per-point GMST: each point uses its own timestamp → static trail, satellite follows it, but trail doesn't close (~23° gap for LEO)
- ICRF rendering (future): camera in inertial frame, Earth rotates → clean ring + satellite follows + no drift. Requires TEME positions for all rendering.

---

## Display Controls and Visibility State

**`applyVisibilityState()` must be called after every position refresh.** Without this hook, satellites that were hidden via toggles reappear after each 5-second fetch because `updatePositions()` doesn't know about toggle state. The hook in `satellites.js` re-applies current toggle settings after every refresh.

**Type filters should be gated by data, not by phase number.** Check `satelliteMetadata` for meaningful types at runtime. This avoids hardcoded phase checks and automatically enables filters when new data arrives.

---

## Task Checklist

### Task 2.1 (GP Data Fetcher) — DONE
- GPFetcher implemented with JSON/OMM format
- Caching, rate limiting, error handling all in place
- Data validation: skips malformed records, decayed objects, non-SGP4 ephemeris types
- Derived orbital params computed: period, semimajor_axis, apoapsis, periapsis
- Epoch staleness tracked (`epoch_age_days`)
- Atomic cache writes, empty response guard
- 37/37 tests passing

### Task 2.2 (Coordinate Transforms) — DONE
- SPICE TEME support tested → NOT available (UNKNOWNFRAME)
- GMST Z-rotation approach implemented: TEME → ECEF → geodetic
- Velocity transform includes ω×r Earth rotation correction
- Tested with 5 diverse satellites (LEO, eccentric, different inclinations)
- ISS ground track verified over 7 days — lat bounded by inclination, alt stable
- 26/26 tests passing

### Task 2.3 (C++ SGP4 Engine) — DONE
- Wrapped Vallado's `SGP4.cpp` (3,247 lines) via pybind11 into `orbitcore/`
- Chose Option A (own C++ wrapper) over Option B (Python sgp4 library) for portfolio value + conjunction scanner integration
- Exposes: `sgp4init()`, `sgp4()`, `jday()`, `invjday()`, `getgravconst()`, `Satrec` class, `GravConst` enum
- Used WGS-72 constants, AFSPC opsmode
- Back-computes `jdsatepoch` from epoch parameter (Vallado's `sgp4init` doesn't set it — only `twoline2rv` does)
- Validated: 32/33 Vallado test sats match Python sgp4 to sub-micrometer
- 54/54 tests passing (including end-to-end C++ SGP4 → coordinate transforms → geodetic)

### Task 2.4 (Propagator Wrapper) — DONE
- Full pipeline: GPFetcher → unit conversion → C++ SGP4 → coordinate transforms → result dict
- 80/80 tests passing
- Cross-validated all 30 stations against Python sgp4 to sub-meter

### Task 2.5 (Tests) — DONE
- 197/197 tests passing across all Week 2 test files

### Task 3.1 (FastAPI Skeleton) — DONE
- FastAPI app with CORS, lifespan-based shared propagator, health check
- 6/6 tests passing

### Task 3.2 (Satellite List) — DONE
- `GET /api/satellites` returns 30 stations with metadata from cached Parquet
- `epoch_age_days` recomputed per-request, `object_type` defaults to `"UNKNOWN"`
- 16/16 tests passing

### Task 3.4 (Data Refresh) — DONE
- `POST /api/refresh` triggers CelesTrak fetch + propagator reload
- Status detection via `fetch_time` comparison (no private method access needed)
- `reload_data()` only called on "fetched" — preserves satrec cache on rate-limited calls
- All fetcher exceptions caught at API boundary → 502 Bad Gateway
- 15/15 new tests passing (68 total API tests, 265 total project tests)

### Task 3.6 (Unit Tests) — DONE
- All 10 checklist items covered by 82 API tests across Tasks 3.1–3.5 — no additional tests needed
- Week 3 complete

### Task 3.5 (Pydantic Response Models) — DONE
- 8 Pydantic models in `backend/models/schemas.py`, `response_model=` on all 6 endpoints
- OpenAPI at `/openapi.json` includes all models with typed fields
- `errors` field is `list[PositionError] | None = None` — Pydantic v2 uses `anyOf` (not `default`) in OpenAPI
- 14/14 new tests passing (82 total API tests)

### Task 3.3 (Position Endpoints) — DONE
- Three endpoints: batch, single (by NORAD ID), ground track
- `iterrows()` eliminated from all production code (replaced with `iloc` + vectorized `dict(zip(...))`)
- `get_all_positions()` returns `(results, errors)` tuple, errors surfaced in API response
- `RuntimeError` from SGP4 failure caught on single + track endpoints (422, not 500)
- 53/53 API tests passing (33 new for Task 3.3)
- 250/250 total tests passing

### Task 4.1 (Cesium.js Setup) — DONE
- Cesium 1.139.1 via jsDelivr CDN, no bundler
- Viewer with terrain disabled (UHD 620), all default UI stripped, resolutionScale 1.0
- Token in gitignored `config.js`, template in `config.example.js`, missing-token guard in `app.js`
- FastAPI StaticFiles mount at `/` after API routes (html=True)
- 82/82 API tests passing

### Task 4.2 (Satellite Points on Globe) — DONE
- `PointPrimitiveCollection` + `LabelCollection` for GPU-batched rendering
- Smooth interpolation at ~20fps between 5-second API refreshes
- CartoDB dark tiles (`dark_all`) for base map — country borders on dark background
- Label style: FILL only (FILL_AND_OUTLINE causes rendering artifacts)
- `Cartesian3.fromDegrees` height in meters — `alt_km * 1000`
- 279/279 tests passing (no regressions)

### Tasks 4.3+4.4 (Info Panel + Orbit Trail) — DONE
- Click handler via `ScreenSpaceEventHandler` + `scene.pick()` on satellite points
- Bottom-left fixed info panel with position data (live) + orbital params (cached at startup)
- Auto-refresh every 5 seconds while satellite is selected
- Orbit trail at orbital altitude: 360 API points → ECEF de-rotation (removes Earth rotation) → densify 10x to ~3600 pts → dual Primitives (near bright + far faint ghost)
- Trail toggle checkbox in panel, race condition guard on async fetch
- Selection indicator: enlarged point (10px) + cyan outline ring (3px)
- `satelliteMetadata` Map added to `satellites.js` — caches `/api/satellites` at startup
- Data pipeline cross-verified against python-sgp4 (sub-mm) and wheretheiss.at API
- Frontend-only changes, no backend modifications

### Tasks 5.1+5.3 (Nadir Line + Display Controls) — DONE
- Nadir line via CallbackProperty Entity — tracks satellite's interpolated position every render frame
- Always on when selected (no toggle), cyan theme matching orbit trail
- Ground point projected via equatorial radius (~7 km error at poles, imperceptible)
- Display controls panel (top-right): label toggle + type filter checkboxes
- Type filters gated by data — only shown when meaningful (non-"UNKNOWN") types exist
- `applyVisibilityState()` hook in `satellites.js` maintains toggle state across refreshes
- Auto-deselect when hiding currently selected satellite via type filter
- Frontend-only changes, no backend modifications

### Task 5.2 (Time Controls) — DONE
- `clock.js` IIFE: simulated clock with `getTime()`, `getTimeMs()`, `isPaused()`, `togglePause()`, `setSpeed()`
- Time bar UI: pause/play, UTC display (250ms tick), speed buttons (1x/10x/60x)
- All API calls pass `?time=` with simulated time; pause freezes all fetches and lerp
- Speed-adaptive refresh: `max(5000/speed, 500)` — 500ms at 60x (30s sim gap), 5s at 1x
- `setTimeout` loops replace `setInterval` for real-time interval adaptation
- Orbit trail: cached TEME + client-side re-rotation every 500ms at speed > 1
- `computeGmst()` extracted as shared helper (IAU 1982 formula)
- Script load order: app → clock → satellites → info-panel → controls
- Frontend-only changes, no backend modifications
- Week 5 complete — all tasks done
