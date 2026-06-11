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
- **Core pipeline:** `backend/core/propagator.py`, `tle_fetcher.py`, `coordinate_transforms.py`
- **Frontend entry point:** `frontend/index.html`
- **Frontend JS:** `frontend/js/app.js` (viewer), `clock.js` (simulated clock + time bar), `satellites.js` (points + labels + adaptive refresh), `info-panel.js` (click interaction + orbit trail + nadir line), `controls.js` (display toggles)
- **Pydantic schemas:** `backend/models/schemas.py` (8 response models)
- **Tests:** `tests/` (279 tests across 7 test files)

## Related Projects & Files
- **Resume:** `/home/j0e/Portfolio/JoseTrinidadTemblador_Resume.pdf`
- **NFL ML Project (similar pipeline pattern):** `/home/j0e/Projects/Sports Analyzer/`
- **Job Tracker:** `/home/j0e/Projects/Job Tracker/`

## Current Status
- **Phase:** Final Sprint underway — Phase 6 (Conjunction Screening), starting task 6.0
- **Timeline:** Mar 20 – Jul 10, 2026 (Weeks 0–5 ✅; Final Sprint = Phases 6–9, Jun 12 – Jul 10)
- **Completed:** Weeks 0–5 (setup, C++ SGP4 engine, coordinate transforms, GP fetcher, propagator wrapper, FastAPI backend with 6 endpoints, Pydantic response models, 82 API tests, Cesium.js globe, satellite points with interpolation, info panel with click interaction, orbit trail at orbital altitude via TEME API + GMST rotation, selection indicator, nadir line with real-time tracking, display controls panel with label toggle, simulated clock with play/pause/speed + adaptive refresh + TEME trail re-rotation)
- **Next steps:** Phase 6.2 (C++ coarse filter — altitude-band pair screening). Full breakdown in `progress/week6_plan.md`. (6.0 test-hygiene + 6.1 batch SGP4 done.)
- **Tests:** 293 passing + 1 skipped (opt-in live fetch) across 7 test files — suite runs offline/deterministic. Frontend JS has no automated tests
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
