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
- **Frontend JS:** `frontend/js/app.js` (viewer), `satellites.js` (points + labels), `info-panel.js` (click interaction + orbit trail)
- **Pydantic schemas:** `backend/models/schemas.py` (8 response models)
- **Tests:** `tests/` (265 tests across 7 test files)

## Related Projects & Files
- **Resume:** `/home/j0e/Portfolio/JoseTrinidadTemblador_Resume.pdf`
- **NFL ML Project (similar pipeline pattern):** `/home/j0e/Projects/Sports Analyzer/`
- **Job Tracker:** `/home/j0e/Projects/Job Tracker/`

## Current Status
- **Phase:** Week 4 — Cesium.js Globe (Apr 17–23, 2026)
- **Timeline:** Mar 20 – May 15, 2026 (8 weeks)
- **Completed:** Weeks 0–3 + Week 4 Tasks 4.1–4.4 (setup, C++ SGP4 engine, coordinate transforms, GP fetcher, propagator wrapper, FastAPI backend with 6 endpoints, Pydantic response models, 82 API tests, Cesium.js globe, satellite points with interpolation, info panel with click interaction, orbit trail)
- **Next steps:** Week 4 Task 4.5 (static files + layout polish), then Week 5 (globe polish — time controls, toggle groups)
- **Tests:** 265 passing across 7 test files (82 API + 183 backend/engine) — frontend JS has no automated tests

## Notes for Future Sessions
- Jose's ML experience is with CatBoost/XGBoost/LightGBM from his NFL prediction project — same pipeline pattern applies here
- He's now comfortable with FastAPI, C++/pybind11, and SPICE — first time was this project. Still new to Cesium.js (Week 4)
- The project should be demoable and portfolio-ready by May 15
- Keep the code modular and well-structured — this will be shown to employers
- C++ and SPICE were chosen specifically to appeal to aerospace employers (SpaceX, K2 Space, Aerospace Corp, etc.)
- Orekit is used for cross-validation of conjunction results, not as the primary engine
- Data is fetched as OMM/JSON from CelesTrak (not legacy TLE format) — future-proofs against the NORAD 5-digit catalog number cap (~July 2026)
- SPICE does NOT know the TEME frame — we handle TEME→ECEF via GMST Z-rotation, then SPICE for geodetic only
- Phase 3 scaling items tracked in `progress/scaling_tracker.md` (C++ batch SGP4, background refresh, etc.)
