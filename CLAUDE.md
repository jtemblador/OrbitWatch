# OrbitWatch — AI Context

## What This Project Is
A satellite orbit tracker and collision predictor. Fetches real satellite TLE data, propagates orbits using SGP4, visualizes them on a Cesium.js 3D globe, detects close approaches between satellites, and classifies collision risk using ML.

## Who This Is For
Jose Temblador — CS honors student (CSUDH, graduating May 2026) building this as a portfolio project to stand out when applying to aerospace/defense companies in the South Bay LA area (SpaceX, Northrop Grumman, Boeing, Aerospace Corp, K2 Space, Hadrian).

## Tech Stack
- **Compute core:** C++ with pybind11 (SGP4 propagation + conjunction pair scanning)
- **Coordinate transforms:** GMST Z-rotation (TEME→ECEF) + SPICE recgeo (ECEF→geodetic)
- **Conjunction validation:** Orekit (ESA/CNES standard, Python bindings)
- **Backend:** Python, FastAPI, uvicorn
- **ML:** XGBoost or CatBoost (collision risk classifier)
- **Frontend:** Cesium.js (industry-standard 3D globe), vanilla JS
- **Data:** CelesTrak (OMM/JSON format, not legacy TLE), Space-Track.org (CDM conjunction data)
- **Storage:** Pandas, Parquet files
- **Deployment:** Static website — CI-built `snapshot.json` (active payloads ~11k, screened offline; debris deferred) on GitHub/Cloudflare Pages, client-side `satellite.js` propagation, + compressed snapshot archive (Docker dropped Jun 24; see roadmap Phase 9)

## Architecture
```
Cesium.js Frontend (3D Globe)
        ↕ REST API (JSON)
FastAPI Backend (Python)
   ├── TLE Fetcher (CelesTrak / Space-Track)
   ├── C++ Core (pybind11)
   │   ├── SGP4 Propagation Engine
   │   └── Conjunction Pair Scanner (coarse + medium filter)
   ├── Coordinate Transforms (TEME → GMST rotation → ECEF → SPICE geodetic)
   ├── Orekit Conjunction Validation
   └── ML Risk Classifier (XGBoost/CatBoost)
```

## Dataset Scaling Path
1. Space Stations (~30 objects) — Phase 1 (current)
2. Brightest/Visual (~150 objects) — Phase 2
3. Starlink (~6,000 objects) — Phase 3
4. Full catalog + debris (10,000+) — Phase 4

## Key Files
- **Project plan:** `PROJECT_PLAN.md`
- **Roadmap:** `progress/roadmap.md`
- **Scaling tracker:** `progress/scaling_tracker.md` (Phase 3 perf items)
- **Weekly plans:** `progress/week{N}_plan.md`
- **Task logs:** `progress/task_logs/task_{N}_{slug}.md`
- **Key findings:** `progress/notes/key_information.md`
- **C++ extension source:** `orbitcore/` (CMakeLists.txt, src/, include/)
- **C++ compiled module:** `backend/orbitcore.cpython-312-x86_64-linux-gnu.so`
- **Backend entry point:** `backend/main.py`
- **API routes:** `backend/routers/satellites.py`
- **Core pipeline:** `backend/core/propagator.py` (+ `catalog_size()` for the screening cap), `tle_fetcher.py` (fetch + `slice_to_shell` + `build_starlink_shell`; 7.4 `_derive_object_type` from the CelesTrak name — gp.php omits OBJECT_TYPE), `coordinate_transforms.py`, `conjunctions.py` (ConjunctionScreener + run_screen + fine_filter; 7.2 SFS-ellipsoid path + co-located suppression + de-dupe; **7.3 `fine_filter_batch` — batched range-rate-Newton fine stage; fine_filter kept as oracle**), `screening_volumes.py` (7.2 — SFS Table 3 RTN ellipsoids + `regime_for`), `demo_seed.py` (synthetic crosser + dense shell generator), `socrates_fetcher.py` (**8.1** — SOCRATES validation data: bulk-CSV fetch + `top_n`/`by_name`/`by_catnr`/`between`), `http_fetch.py` (**8.1** — shared `download_text`, requests→curl-TLS-fallback, used by both fetchers), `socrates_compare.py` (**8.2** — `match_events` pure matcher + `compare_against_socrates` orchestrator + `epoch_ok` via DSE; **8.3** + `build_epoch_targets`), `spacetrack_fetcher.py` (**8.3** — Space-Track cookie auth + `gp_history`/`gp` fetch + `_coerce_numeric` for ST's all-string JSON + `BulkGPAdapter` epoch-matched/latest via the `fetch_by_catnr` seam), `socrates_report.py` + `socrates_plots.py` (**8.3** — pure Markdown formatters + matplotlib figures); propagator now exposes `build_satrecs_and_meta(df)` (**8.2** — GP frame → screener input)
- **Validation report runner:** `scripts/validate_socrates.py` (**8.3** — `--epoch-matched` / `--current-source {celestrak,spacetrack}`; writes `validation/socrates_report.md` + figures; auto-loads a gitignored `.env` for Space-Track creds)
- **Validation docs:** `validation/socrates_report.md` (generated, the SOCRATES match results) + `validation/sgp4_uncertainty.md` (**8.4** — hand-authored cited limits/uncertainty doc; report links it via a `## Limitations` footer)
- **Frontend entry point:** `frontend/index.html`
- **Frontend JS:** `frontend/js/app.js` (viewer), `clock.js` (simulated clock + time bar), `satellites.js` (points + labels + adaptive refresh), `info-panel.js` (click interaction + orbit trail + nadir line), `controls.js` (display toggles), `conjunctions.js` (conjunction list + connecting lines)
- **Pydantic schemas:** `backend/models/schemas.py` (10 response models)
- **Profiling harness:** `scripts/profile_screening.py` (7.1 — sweeps the cascade over the Starlink catalog; `run_screen(timings=…)` is the passive hook)
- **Tests:** `tests/` (541 tests across 14 test files)

## Related Projects & Files
- **Resume:** `/home/j0e/Portfolio/JoseTrinidadTemblador_Resume.pdf`
- **NFL ML Project (similar pipeline pattern):** `/home/j0e/Projects/Sports Analyzer/`
- **Job Tracker:** `/home/j0e/Projects/Job Tracker/`

## Current Status
- **Phase:** Final Sprint underway — Phase 6 COMPLETE (6.0–6.10); **Phase 7 COMPLETE (7.0–7.5)**: 7.0 (live data) + 7.1 (scale profile) + 7.2 (SFS ellipsoid volumes) + 7.3 (fine-stage batching + cap/threadpool) + 7.4 (type filters — object_type derived from CelesTrak name) + 7.5 (scale/perf regression tests). **Phase 8 nearly complete: 8.1 (fetcher) + 8.2 (comparison) + 8.3 (validation report, Stage A+B) + 8.4 (SGP4-uncertainty doc) + 8.5 (tests, per-task) done — only 8.6 (`cdm_public`, stretch) remains.** Also: interactive core of 9.1 (conjunction UX) pulled forward.
- **Timeline:** Mar 20 – Jul 10, 2026 (Weeks 0–5 ✅; Final Sprint = Phases 6–9, Jun 12 – Jul 10)
- **Completed:** Weeks 0–5 (setup, C++ SGP4 engine, coordinate transforms, GP fetcher, propagator wrapper, FastAPI backend with 6 endpoints, Pydantic response models, 82 API tests, Cesium.js globe, satellite points with interpolation, info panel with click interaction, orbit trail at orbital altitude via TEME API + GMST rotation, selection indicator, nadir line with real-time tracking, display controls panel with label toggle, simulated clock with play/pause/speed + adaptive refresh + TEME trail re-rotation)
- **Next steps:** Phase 8.6 (`cdm_public` cross-check, stretch) → Phase 9 (static website). **8.4 done** (`task_logs/task_8_4_sgp4_uncertainty.md`): `validation/sgp4_uncertainty.md` (linked from the report's `## Limitations` footer) — cited, evidence-backed limits doc separating implementation error (**<1 m** vs Vallado ref) from element accuracy (~1 km), age growth (~2–3 km/day), and **epoch drift** (the dominant term, *measured* in 8.3: current-feed Δmiss 1.1–2.6 km → **0.000** matched). Frames it as honest screening, not collision avoidance (no Pc). Citations verified (MathWorks Finder, NASA/SP-20205011318, Vallado AIAA 2006-6753 for *implementation* fidelity, Skyfield/CelesTrak). **8.3 done** (`task_logs/task_8_3_validation_report.md`): `scripts/validate_socrates.py` → `validation/socrates_report.md` + figures, 3 slices. **Stage A** current-GP baseline; **Stage B** = the `gp_history` lever (`spacetrack_fetcher.py` — Space-Track cookie auth/no-key, `_coerce_numeric` for ST's all-string JSON, `BulkGPAdapter` reuses the 8.2 `fetch_by_catnr` seam). **Live current→epoch-matched:** ISS 3/9→8/9, top-25 8/25→**25/25 (100%)**, Starlink-40 8/40→**40/40 (100%)**, every matched event **ΔTCA/Δmiss = 0.000** (byte-level same-method agreement); current-feed lowness is pure epoch drift (`epoch_ok=0`). Creds in gitignored `.env`; `--current-source spacetrack` bypasses VPN-blocked CelesTrak. +68 tests. **8.2 done** (`task_logs/task_8_2_socrates_compare.md`): `socrates_compare.py` — `match_events` (pure matcher: pair + TCA proximity → per-event TCA/miss deltas + reproduction rate bucketed by `DSE`; reused by Phase 9's badge) + `compare_against_socrates` (orchestrator: GP-per-object fetch with `max_objects` cap → `build_satrecs_and_meta` → 5 km Euclidean screen → match → `epoch_ok` via `DSE`). Live ISS: 3/9 reproduced, matched agree **TCA <5 s / miss <0.32 km**; `epoch_ok=0` (current `gp.php` rolled the epochs SOCRATES used) → misses cluster at higher `DSE` (the epoch-drift finding; `gp_history` backstop is the 8.3 lever). **8.1 done** (`task_logs/task_8_1_socrates_fetcher.md`): `SOCRATESFetcher` downloads the bulk **`sort-minRange.csv`** full run (~148 k conjunctions, RFC-4180 — the HTML-scrape plan was obsoleted by finding the official static CSV), parses to a typed frame (objects + both **`DSE`** + TCA + range + speed; drops `MAX_PROB`/`DILUTION`), Parquet-cached 8 h TTL; slices `top_n`/`by_name`/`by_catnr`/`between`. Shared `http_fetch.download_text` (requests→curl fallback) now used by both fetchers. Gotchas: trailing `[status]` name tag, **object 1/2 positional (not primary)**, `.str.contains` needs `regex=False`. Live: 147,814 rows, 0 nulls, closest 0.016 km. **7.5 done** (`task_logs/task_7_5_tests.md`): `TestScaleRegression` — 3 cross-task locks (deterministic + offline). (1) the 7.3 batched Newton fine stage reproduces the scipy oracle across **257 real windows** of a 300-sat shell (<10 m / <50 ms / <1 mm/s) — the "byte-identical on dense catalogs" claim, previously only on ~8 crosser windows; (2) a full shell screen is **byte-identical run-to-run**; (3) the 7.2 **no-skip wiring** (a spy confirms `run_screen(volumes=…)` passes `medium_filter` the largest semi-axis 51 km, not the radial axis or box corner). **Mutation-checked** so the green tests provably bite; 3× flake-clean. Durable gotcha → key_information: solver-vs-solver TCA equivalence must gate on a real crossing (`rel_speed>1`); a synthetic shell needs ~300 sats + 100 km pad for natural windows. **7.4 done** (`task_logs/task_7_4_object_types.md`): gp.php omits `OBJECT_TYPE`, so `object_type` was `None`→`"UNKNOWN"` everywhere and the (already-wired) `controls.js` type filters never rendered. Now derived from the CelesTrak name convention (`DEB`/`R/B` tokens, else PAYLOAD; token-matched) in `tle_fetcher`, filling only nulls across all read paths incl. `load_cached` (existing caches get types, no re-fetch). Default `stations` → PAYLOAD + DEBRIS; conjunction events read real types. SATCAT join = authoritative upgrade (deferred). **7.3 done** (`task_logs/task_7_3_fine_stage_batch.md`): the fine stage (82–87% of wall time per 7.1) is now `fine_filter_batch` — **Newton on the relative range-rate** (`t ← t − (Δr·Δv)/|Δv|²`, the operational TCA solve), all windows stepped together via `propagate_batch`, vectorized + chunked. **~3.7× faster fine stage** (same-machine A/B) with **byte-identical event counts** (124,810 full catalog); cross-validated vs the scipy oracle AND a brute-force grid. `/api/conjunctions` runs in `run_in_threadpool` under `_propagator_lock` (guards screen + position/track vs a Satrec-cache race — caught by adversarial review) + a `413` cap (`ORBITWATCH_MAX_SCREEN_SATS`, default 1500). Pure-Python, no `.so` rebuild. **Deferred:** #3 C++ coarse→medium memory fusion (cap removes its urgency), radial coarse-pad tightening, #7 fine-stage dict streaming (memory); full catalog stays batch-only (117 s / 5.2 GB). **demo operating point `MAX_SATS=300` ≈ 1.7 s @ 24 h** (was 2.6 s). **7.2** (`task_logs/task_7_2_screening_volumes.md`): SFS RTN ellipsoid volumes + co-located suppression + de-dupe — real Starlink (300) → 7 conjunctions; live stations → 0 events / 46 suppressed. ⚠ **The SFS default excludes the synthetic crosser** (radial-dominated), so a populated demo needs a **real Starlink shell**, not `stations` + seed.
- **Tests:** 541 passing + 4 skipped (opt-in live fetches) across 14 test files — suite runs offline/deterministic. Frontend JS has no automated tests
- **Dataset / demo modes:** default `stations` (tests stay ISS-based, cached). Env-selectable: `ORBITWATCH_DEMO_SEED=1` seeds a synthetic crosser; `ORBITWATCH_GROUP=<group>` picks the catalog; `ORBITWATCH_LIVE=1` fetches fresh on load (7.0); `ORBITWATCH_MAX_SATS=N` slices a dense shell in-app. **Live dense demo:** `ORBITWATCH_LIVE=1 ORBITWATCH_GROUP=starlink ORBITWATCH_MAX_SATS=300 ORBITWATCH_DEMO_SEED=1 python backend/main.py`. Offline fallback: `ORBITWATCH_GROUP=starlink_shell` (static snapshot, rebuild via `python -m backend.core.tle_fetcher starlink-shell [path.json]`). ⚠ "Live" = fresh-on-load + manual refresh; auto-refresh scheduler is Phase 9.7.
- **Conjunction-screening invariant (6.3):** the medium filter uses a velocity-aware interval bound — a crossing pair with an 8 km true miss samples at ~520 km on a 60 s grid, so plain distance thresholding misses real conjunctions. Any port/replacement must preserve an equivalent no-skip bound (see key_information.md)
- **Perf note (6.1, measured):** Python-loop-over-C++ sgp4 overhead is only ~5% — the conjunction medium filter (6.3) must keep its whole loop inside C++; `orbitcore.propagate_batch` provides batch semantics + per-sat error sentinels
- **Pivot (finalized Jun 11):** Dropped ML risk classifier (full CDM data requires an SSA Sharing Agreement — operators only; without it ML can't beat a threshold). Headline is now **conjunction screening validated against CelesTrak SOCRATES**. Scope narrowed further: **no Pc computation** (needs covariance we don't have) — this is an honest *geometric screener on SGP4*, not operational collision avoidance. Confirmed direction against Jose's targeted aerospace roles (all want C++/GNC/test-rigor, none want ML). See roadmap.

## Notes for Future Sessions
- Jose's ML experience is with CatBoost/XGBoost/LightGBM from his NFL prediction project — applies if ML is reintroduced with proper data access
- He's now comfortable with FastAPI, C++/pybind11, and SPICE — first time was this project. Still new to Cesium.js (Week 4)
- The project should be demoable and portfolio-ready by Jul 10
- Keep the code modular and well-structured — this will be shown to employers
- C++ and SPICE were chosen specifically to appeal to aerospace employers (SpaceX, K2 Space, Aerospace Corp, etc.)
- **No Pc computation** — deliberately out of scope (needs covariance data we don't have). Output is geometric: TCA, miss distance, relative speed, RTN components. Honest "screening, not collision avoidance" framing.
- Validation: CelesTrak SOCRATES is the primary anchor (open/no-auth, SGP4-based → same-method). Space-Track `cdm_public` is an optional Phase 8 stretch (SP-based cross-method check, detection-only). Orekit dropped.
- Stale ML references still in PROJECT_PLAN.md / requirements.txt / week0 docs / 1plan.md — scheduled for Phase 9.3 cleanup, not yet done.
- Data is fetched as OMM/JSON from CelesTrak (not legacy TLE format) — future-proofs against the NORAD 5-digit catalog number cap (~July 2026)
- SPICE does NOT know the TEME frame — we handle TEME→ECEF via GMST Z-rotation, then SPICE for geodetic only
- Phase 3 scaling items tracked in `progress/scaling_tracker.md` (C++ batch SGP4, background refresh, etc.)
