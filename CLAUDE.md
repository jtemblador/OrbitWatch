# OrbitWatch — AI Context

## What This Project Is
A satellite orbit tracker and collision predictor. Fetches real satellite TLE data, propagates orbits using SGP4, visualizes them on a Cesium.js 3D globe, detects close approaches between satellites, and classifies collision risk using ML.

## Who This Is For
Jose Temblador — CS honors student (CSUDH, graduating May 2026) building this as a portfolio project to stand out when applying to aerospace/defense companies in the South Bay LA area (SpaceX, Northrop Grumman, Boeing, Aerospace Corp, K2 Space, Hadrian).

## Tech Stack
- **Compute core:** C++ with pybind11 (SGP4 propagation + conjunction pair scanning)
- **Coordinate transforms:** NASA SPICE toolkit via spiceypy
- **Conjunction validation:** Orekit (ESA/CNES standard, Python bindings)
- **Backend:** Python, FastAPI, uvicorn
- **ML:** XGBoost or CatBoost (collision risk classifier)
- **Frontend:** Cesium.js (industry-standard 3D globe), vanilla JS
- **Data:** CelesTrak (TLE), Space-Track.org (CDM conjunction data)
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
   ├── SPICE Coordinate Transforms (ECI → ECEF → lat/lon/alt)
   ├── Orekit Conjunction Validation
   └── ML Risk Classifier (XGBoost/CatBoost)
```

## Dataset Scaling Path
1. Space Stations (~15 objects) — Phase 1
2. Brightest/Visual (~150 objects) — Phase 2
3. Starlink (~6,000 objects) — Phase 3
4. Full catalog + debris (10,000+) — Phase 4

## Key Files
- **Project plan:** `PROJECT_PLAN.md`
- **Roadmap:** `progress/roadmap.md`
- **Weekly progress:** `progress/week0_setup.md`, etc.
- **C++ extension:** `orbitcore/` (CMakeLists.txt, src/, include/)
- **Backend entry point:** `backend/main.py`
- **Frontend entry point:** `frontend/index.html`

## Related Projects & Files
- **Resume:** `/home/j0e/Portfolio/JoseTrinidadTemblador_Resume.pdf`
- **NFL ML Project (similar pipeline pattern):** `/home/j0e/Projects/Sports Analyzer/`
- **Job Tracker:** `/home/j0e/Projects/Job Tracker/`

## Current Status
- **Phase:** Week 0–1 — Setup + C++ Foundation
- **Timeline:** Mar 20 – May 15, 2026 (8 weeks)
- **Next steps:** Create accounts, init repo, install C++ toolchain + pybind11, set up SPICE, verify TLE fetch

## Notes for Future Sessions
- Jose's ML experience is with CatBoost/XGBoost/LightGBM from his NFL prediction project — same pipeline pattern applies here
- He's comfortable with Python, Pandas, Parquet, REST APIs, and Streamlit but this is his first time using Cesium.js, FastAPI, C++/pybind11, and SPICE
- The project should be demoable and portfolio-ready by May 15
- Keep the code modular and well-structured — this will be shown to employers
- C++ and SPICE were chosen specifically to appeal to aerospace employers (SpaceX, K2 Space, Aerospace Corp, etc.)
- Orekit is used for cross-validation of conjunction results, not as the primary engine
