# OrbitWatch

A real-time satellite orbit tracker and collision prediction system. Fetches live TLE data from CelesTrak, propagates orbits using a custom C++ SGP4 engine, and visualizes satellite positions on an interactive 3D globe — with conjunction detection and ML-based collision risk classification in progress.

> **Status:** Active development — Weeks 0–5 complete (core engine, backend API, 3D globe), Week 6+ targeting conjunction detection and ML risk scoring.

---

## What It Does

- **Tracks real satellites** — fetches OMM/JSON data from CelesTrak (space stations, visual sats, Starlink constellation)
- **Propagates orbits in C++** — Vallado SGP4 engine compiled via pybind11 for fast batch position computation
- **Converts coordinates** — TEME → ECEF (GMST rotation) → geodetic lat/lon/alt (NASA SPICE toolkit)
- **Serves a REST API** — FastAPI backend with endpoints for satellite metadata, current positions, and orbit trail data
- **Renders a 3D globe** — Cesium.js viewer with satellite points, labels, orbit trails at correct altitude, ground tracks, and time animation controls
- **Detects conjunctions** *(in progress)* — C++ pair scanning with altitude-band coarse filter + time-stepped medium filter + scipy fine filter
- **Classifies collision risk** *(planned)* — XGBoost/CatBoost model trained on conjunction geometry features

---

## Architecture

```
Cesium.js Frontend (3D Globe)
        ↕ REST API (JSON)
FastAPI Backend (Python)
   ├── TLE Fetcher          — CelesTrak OMM/JSON, Parquet cache
   ├── C++ Core (pybind11)
   │   ├── SGP4 Propagator  — batch position computation
   │   └── Conjunction Scanner (coarse + medium filter) [Week 6]
   ├── Coordinate Transforms — TEME → ECEF → geodetic (SPICE)
   ├── Orekit Validation    — ESA/CNES cross-validation [Week 6]
   └── ML Risk Classifier   — XGBoost/CatBoost [Week 7]
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Orbital engine | C++ (Vallado SGP4) + pybind11 |
| Coordinate transforms | Custom GMST rotation + NASA SPICE (spiceypy) |
| Backend | Python, FastAPI, uvicorn |
| Data | CelesTrak OMM/JSON, pandas, Parquet |
| ML | XGBoost, scipy |
| Frontend | Cesium.js, vanilla JS |
| Validation | Orekit (ESA/CNES Python bindings) |
| Deployment | Docker *(planned Week 8)* |

---

## Features Completed

- C++ SGP4 propagation engine with pybind11 Python bindings
- Full coordinate transform pipeline: TEME → ECEF → geodetic via GMST + SPICE
- FastAPI backend with 6 REST endpoints and full Pydantic OpenAPI schema
- Interactive Cesium.js globe: satellite points, labels, click info panel, selection indicator
- Orbit trail visualization reconstructed from TEME positions at real orbital altitude
- Nadir line (satellite ground track point) with real-time tracking
- Simulated clock: play/pause/speed controls, adaptive refresh based on simulation rate
- Display controls panel: toggle labels, trails, nadir lines
- 279 automated tests across 7 test files (82 API + 197 backend/engine)

---

## Project Structure

```
orbitcore/          C++ SGP4 engine (CMake + pybind11)
backend/
  main.py           FastAPI app entry point
  routers/          API route handlers
  core/             Propagator, TLE fetcher, coordinate transforms
  models/           Pydantic schemas
frontend/
  index.html        Cesium.js viewer
  js/               app.js, clock.js, satellites.js, info-panel.js, controls.js
tests/              pytest suite (7 files, 279 tests)
progress/           Weekly plans, task logs, roadmap, notes
```

---

## Running Locally

```bash
# Install Python dependencies
pip install -r requirements.txt

# Build the C++ extension
cd orbitcore && mkdir build && cd build
cmake .. && make
cp orbitcore*.so ../../backend/

# Start the backend
cd backend && uvicorn main:app --reload

# Open frontend/index.html in a browser (requires a Cesium ion token)
```

---

## Dataset Scaling Path

| Phase | Catalog | Objects |
|---|---|---|
| 1 (current) | Space stations | ~30 |
| 2 (next) | Visual/brightest | ~150 |
| 3 | Starlink | ~6,000 |
| 4 | Full catalog + debris | 10,000+ |

---

## Roadmap

- **Week 6** — Conjunction detection: C++ coarse + medium filter, Python fine filter (scipy), `/api/conjunctions` endpoint
- **Week 7** — ML risk classifier: feature engineering from conjunction geometry, XGBoost/CatBoost training
- **Week 8** — Docker deployment, portfolio cleanup, final demo

Portfolio target: **May 15, 2026**
