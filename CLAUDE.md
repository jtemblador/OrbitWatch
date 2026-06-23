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
- **Deployment:** Docker

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
- **Core pipeline:** `backend/core/propagator.py` (+ `catalog_size()` for the screening cap), `tle_fetcher.py` (fetch + `slice_to_shell` + `build_starlink_shell`), `coordinate_transforms.py`, `conjunctions.py` (ConjunctionScreener + run_screen + fine_filter; 7.2 SFS-ellipsoid path + co-located suppression + de-dupe; **7.3 `fine_filter_batch` — batched range-rate-Newton fine stage; fine_filter kept as oracle**), `screening_volumes.py` (7.2 — SFS Table 3 RTN ellipsoids + `regime_for`), `demo_seed.py` (synthetic crosser + dense shell generator)
- **Frontend entry point:** `frontend/index.html`
- **Frontend JS:** `frontend/js/app.js` (viewer), `clock.js` (simulated clock + time bar), `satellites.js` (points + labels + adaptive refresh), `info-panel.js` (click interaction + orbit trail + nadir line), `controls.js` (display toggles), `conjunctions.js` (conjunction list + connecting lines)
- **Pydantic schemas:** `backend/models/schemas.py` (10 response models)
- **Profiling harness:** `scripts/profile_screening.py` (7.1 — sweeps the cascade over the Starlink catalog; `run_screen(timings=…)` is the passive hook)
- **Tests:** `tests/` (421 tests across 9 test files)

## Related Projects & Files
- **Resume:** `/home/j0e/Portfolio/JoseTrinidadTemblador_Resume.pdf`
- **NFL ML Project (similar pipeline pattern):** `/home/j0e/Projects/Sports Analyzer/`
- **Job Tracker:** `/home/j0e/Projects/Job Tracker/`

## Current Status
- **Phase:** Final Sprint underway — Phase 6 COMPLETE (6.0–6.10); **Phase 7 underway: 7.0 (live data) + 7.1 (scale profile) + 7.2 (SFS ellipsoid volumes) + 7.3 (fine-stage batching + cap/threadpool) done**, next 7.4 (type filters) / 7.5 (tests). Also: interactive core of 9.1 (conjunction UX) pulled forward.
- **Timeline:** Mar 20 – Jul 10, 2026 (Weeks 0–5 ✅; Final Sprint = Phases 6–9, Jun 12 – Jul 10)
- **Completed:** Weeks 0–5 (setup, C++ SGP4 engine, coordinate transforms, GP fetcher, propagator wrapper, FastAPI backend with 6 endpoints, Pydantic response models, 82 API tests, Cesium.js globe, satellite points with interpolation, info panel with click interaction, orbit trail at orbital altitude via TEME API + GMST rotation, selection indicator, nadir line with real-time tracking, display controls panel with label toggle, simulated clock with play/pause/speed + adaptive refresh + TEME trail re-rotation)
- **Next steps:** Phase 7.4 (enable type filters) / 7.5 (tests). **7.3 done** (`task_logs/task_7_3_fine_stage_batch.md`): the fine stage (82–87% of wall time per 7.1) is now `fine_filter_batch` — **Newton on the relative range-rate** (`t ← t − (Δr·Δv)/|Δv|²`, the operational TCA solve), all windows stepped together via `propagate_batch`, vectorized + chunked. **~3.7× faster fine stage** (same-machine A/B) with **byte-identical event counts** (124,810 full catalog); cross-validated vs the scipy oracle AND a brute-force grid. `/api/conjunctions` runs in `run_in_threadpool` under `_propagator_lock` (guards screen + position/track vs a Satrec-cache race — caught by adversarial review) + a `413` cap (`ORBITWATCH_MAX_SCREEN_SATS`, default 1500). Pure-Python, no `.so` rebuild. **Deferred:** #3 C++ coarse→medium memory fusion (cap removes its urgency), radial coarse-pad tightening, #7 fine-stage dict streaming (memory); full catalog stays batch-only (117 s / 5.2 GB). **demo operating point `MAX_SATS=300` ≈ 1.7 s @ 24 h** (was 2.6 s). **7.2** (`task_logs/task_7_2_screening_volumes.md`): SFS RTN ellipsoid volumes + co-located suppression + de-dupe — real Starlink (300) → 7 conjunctions; live stations → 0 events / 46 suppressed. ⚠ **The SFS default excludes the synthetic crosser** (radial-dominated), so a populated demo needs a **real Starlink shell**, not `stations` + seed.
- **Tests:** 421 passing + 1 skipped (opt-in live fetch) across 9 test files — suite runs offline/deterministic. Frontend JS has no automated tests
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
