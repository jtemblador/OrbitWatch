# OrbitWatch — Satellite Orbit Tracker + Collision Predictor
**Status:** In Progress
**Timeline:** 8 weeks (Mar 20 – May 15, 2026)

---

## The Problem

There are 10,000+ active satellites and 30,000+ pieces of tracked debris orbiting Earth. Collisions are a real and growing threat — the 2009 Iridium-Cosmos collision created 2,000+ debris fragments. Companies like SpaceX (Starlink has 6,000+ satellites), Aerospace Corp, and Northrop Grumman actively work on space situational awareness. This project builds a system that tracks real satellites, visualizes their orbits, and predicts potential close approaches.

---

## What We're Building

A web-based dashboard that:
1. Fetches real satellite orbit data (TLE) from public sources
2. Propagates orbits to compute satellite positions at any point in time
3. Renders satellites on an interactive Cesium.js 3D globe in real-time
4. Detects and predicts close approaches (conjunctions) between satellites
5. Classifies risk levels and generates alerts for dangerous passes
6. Lets users search/filter by satellite name, type, orbit altitude, country

---

## Decisions Made

- **Visualization:** Cesium.js (industry-standard 3D globe, used by AGI/DoD)
- **Compute core:** C++ with pybind11 bindings (orbit propagation + conjunction detection)
- **Coordinate transforms:** NASA SPICE toolkit via spiceypy
- **Conjunction validation:** Orekit (ESA/CNES standard astrodynamics library)
- **Backend:** Python (FastAPI) serving orbital data to the Cesium.js frontend
- **Deployment:** Docker
- **Project name:** OrbitWatch
- **Dataset scaling path:**
  - Phase 1: Space Stations (~15 objects) — ISS, Tiangong, etc.
  - Phase 2: Brightest/Visual satellites (~150 objects)
  - Phase 3: Starlink constellation (~6,000 objects)
  - Phase 4: Full catalog + debris (10,000+)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Cesium.js Frontend                 │
│  3D Globe • Orbit Trails • Click Info • Time Slider │
└──────────────────────┬──────────────────────────────┘
                       │ REST API (JSON)
┌──────────────────────▼──────────────────────────────┐
│                  FastAPI Backend (Python)           │
│  /satellites • /positions • /conjunctions • /alerts │
└──────┬───────────┬──────────────┬───────────────────┘
       │           │              │
┌──────▼──┐ ┌──────▼─────────┐ ┌─▼────────────────────┐
│  Data   │ │  C++ Core      │ │  ML Risk Classifier  │
│  Layer  │ │  (pybind11)    │ │  (XGBoost/CatBoost)  │
│ (TLE)   │ │  SGP4 + SPICE  │ │  + Orekit validation │
└─────────┘ │  + Conjunction │ └──────────────────────┘
            └────────────────┘
```

---

## Main Components

### Component 1: Data Ingestion Layer
**What:** Fetch and parse Two-Line Element (TLE) data — the standard format that describes satellite orbits.

**Data Sources (free, no heavy downloads):**
- **CelesTrak** (celestrak.org) — Curated TLE sets, no account needed, REST API
  - `stations` — ISS, Tiangong (~15 objects) ← Phase 1
  - `visual` — Brightest satellites (~150 objects) ← Phase 2
  - `starlink` — Starlink constellation (~6,000) ← Phase 3
  - `active` — All active satellites (~10,000) ← Phase 4
- **Space-Track.org** — Official USSPACECOM source, free account required
  - Conjunction Data Messages (CDMs) for ML training data

**TLE Format:**
```
ISS (ZARYA)
1 25544U 98067A   24050.53073472  .00016717  00000+0  10270-3 0  9017
2 25544  51.6400 208.5513 0005678  35.5106 324.6267 15.49560479434601
```

**Tools:**
- `requests` — Fetch TLE data via HTTP
- `sgp4` — Parse TLE format natively
- `pandas` — Organize satellite metadata
- Parquet files for local storage

---

### Component 2: Orbital Propagation Engine (C++)
**What:** Given a TLE, compute where a satellite is (or will be) at any point in time.

**How it works:**
- TLE data + SGP4 algorithm = satellite position in ECI coordinates at time T
- SPICE converts ECI → ECEF → geodetic (lat, lon, alt) using NASA-standard transformations
- SGP4 is the standard model used by NORAD/USSPACECOM
- Accounts for Earth's gravity, atmospheric drag, lunar/solar perturbations
- C++ implementation handles thousands of satellites per second

**Tools:**
- **C++ SGP4** — Core propagation algorithm, compiled as a Python extension via pybind11
- **SPICE / spiceypy** — NASA-standard coordinate frame transformations (ECI → ECEF → lat/lon/alt)
- **pybind11** — Exposes C++ functions to Python seamlessly

**Key functions (exposed to Python via pybind11):**
- `propagate_satellite(tle, time)` → (x, y, z) in ECI, then SPICE converts to (lat, lon, alt)
- `propagate_batch(tle_list, time_range)` → positions for all satellites over a time window
- `get_ground_track(tle, duration_hours)` → path a satellite traces over Earth
- `get_orbit_path(tle, periods)` → full 3D orbital ellipse for Cesium rendering

---

### Component 3: FastAPI Backend
**What:** REST API serving satellite data to the Cesium.js frontend.

**Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/satellites` | GET | List all tracked satellites with metadata |
| `/api/satellites/{id}` | GET | Detail for one satellite |
| `/api/positions?time=T` | GET | Current positions of all satellites at time T |
| `/api/orbit/{id}?periods=N` | GET | Orbital path for rendering in Cesium |
| `/api/conjunctions?hours=H` | GET | Predicted close approaches in next H hours |
| `/api/conjunctions/{id}` | GET | Detail for a specific conjunction event |
| `/api/refresh` | POST | Re-fetch latest TLE data from CelesTrak |

**Tools:**
- `FastAPI` — Async Python web framework
- `uvicorn` — ASGI server
- Serves static Cesium.js frontend files as well

---

### Component 4: Cesium.js 3D Frontend
**What:** Interactive 3D globe showing satellites orbiting Earth in real-time.

**Why Cesium:**
- Industry standard for geospatial 3D visualization
- Used by AGI (Analytical Graphics Inc), the same company that makes STK — the tool Aerospace Corp and the DoD actually use
- Built-in support for satellite orbits, time animation, and CZML (Cesium's animation format)
- Free for non-commercial use (Ion account for terrain/imagery tiles)

**Visual features:**
- Earth globe with satellite dots colored by type (station=red, active=blue, debris=gray)
- Click a satellite → popup with name, altitude, speed, country, orbit type
- Orbit trail rendering — see the full orbital path
- Ground track line — see where the satellite passes over Earth
- Time controls — play/pause, speed up, scrub forward/backward
- Toggle constellation groups on/off
- Conjunction visualization — highlight two satellites approaching each other with a connecting line that turns red as they get closer

**Tech:**
- `Cesium.js` via CDN or npm
- `CZML` format for streaming time-dynamic satellite positions
- Vanilla JS or lightweight framework (no need for React overhead)
- Calls FastAPI backend for data

---

### Component 5: Conjunction Detection (C++) + Collision Prediction
**What:** Identify when two satellites will pass dangerously close to each other.

**Algorithm:**
1. **Coarse filter (C++):** Group satellites by orbital altitude band (LEO, MEO, GEO). Only check pairs in similar altitude ranges. Reduces O(n²) to manageable subsets.
2. **Medium filter (C++):** For each time step (e.g., every 60 seconds over 24-72 hours), compute distance between satellite pairs in the same band. Flag any pair within threshold (e.g., 50 km).
3. **Fine filter:** For flagged pairs, use `scipy.optimize.minimize_scalar` to find the exact time and minimum distance of closest approach.
4. **Validation:** Cross-check results against **Orekit** conjunction analysis for accuracy.
5. **Risk classification:** ML model classifies as LOW / MEDIUM / HIGH / CRITICAL.

**Tools:**
- **C++ (pybind11)** — Coarse and medium filter pair scanning (the O(n²) hot path)
- `scipy.optimize` — Closest approach time refinement (called only on flagged pairs)
- **Orekit** — Cross-validates conjunction results against industry-standard astrodynamics
- `XGBoost` or `CatBoost` — Risk classification model

---

### Component 6: ML Risk Classifier
**What:** Predict collision risk level for detected conjunctions.

**Training data:**
- Space-Track CDM (Conjunction Data Message) historical data
- If insufficient, generate synthetic conjunctions with known parameters

**Features:**
- Miss distance (km)
- Relative velocity (km/s)
- Object A size / type (payload, rocket body, debris)
- Object B size / type
- TLE age for both objects (older = more positional uncertainty)
- Orbit type (LEO-LEO, LEO-debris, etc.)
- Altitude of conjunction

**Target:** Risk level (LOW / MEDIUM / HIGH / CRITICAL) or collision probability

**This ties the project to your ML resume strengths** — same pipeline pattern as the NFL project (feature engineering → model training → prediction → actionable output).

---

### Component 7: Alert & Search Interface
**What:** Sidebar/panel in the dashboard for exploring data and viewing alerts.

**Alert table:**
- Satellite A, Satellite B, Time of Closest Approach, Miss Distance, Velocity, Risk Level
- Sortable by any column
- Click a row → Cesium camera flies to the conjunction location and shows the event
- Export to CSV/JSON

**Search & filters:**
- Search by satellite name
- Filter by orbit type: LEO / MEO / GEO
- Filter by type: payload / rocket body / debris / station
- Filter by constellation (Starlink, GPS, OneWeb, etc.)
- Filter by country/operator

---

## Tech Stack Summary

| Layer | Tool | Purpose |
|-------|------|---------|
| Compute core | **C++ (pybind11)** | SGP4 propagation + conjunction pair scanning |
| Coordinate transforms | **SPICE / spiceypy** | NASA-standard ECI → ECEF → geodetic conversion |
| Conjunction validation | **Orekit** (Python bindings) | Cross-check results against ESA/CNES standard |
| Frontend | Cesium.js | 3D globe, orbit rendering, time animation |
| Backend | FastAPI + uvicorn | REST API serving satellite data |
| Data fetch | requests | Pull TLE from CelesTrak / Space-Track |
| Data storage | pandas, Parquet | Satellite catalog and conjunction records |
| Computation | scipy | Closest approach time refinement |
| ML | XGBoost or CatBoost | Collision risk classification |
| Deployment | **Docker** | Containerized app, one-command startup |

---

## Project Structure

```
OrbitWatch/
├── PROJECT_PLAN.md
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── orbitcore/                       # C++ extension module
│   ├── CMakeLists.txt              # Build config for pybind11
│   ├── src/
│   │   ├── sgp4.cpp                # SGP4 propagation implementation
│   │   ├── conjunction.cpp         # Pair scanning (coarse + medium filter)
│   │   └── bindings.cpp            # pybind11 Python bindings
│   └── include/
│       ├── sgp4.h
│       └── conjunction.h
├── backend/
│   ├── main.py                     # FastAPI app entry point
│   ├── routers/
│   │   ├── satellites.py           # /api/satellites endpoints
│   │   ├── positions.py            # /api/positions endpoints
│   │   └── conjunctions.py         # /api/conjunctions endpoints
│   ├── core/
│   │   ├── tle_fetcher.py          # Fetch TLE from CelesTrak / Space-Track
│   │   ├── tle_parser.py           # Parse TLE into satellite objects
│   │   ├── propagator.py           # Calls C++ SGP4 + SPICE transforms
│   │   └── conjunction_detector.py # Calls C++ scanner + scipy refinement
│   ├── ml/
│   │   ├── risk_classifier.py      # ML risk classification model
│   │   └── train.py                # Training script
│   └── data/
│       ├── tle/                    # Raw TLE files
│       ├── spice_kernels/          # SPICE kernel files (Earth, leap seconds)
│       ├── conjunctions/           # Detected conjunction records
│       └── models/                 # Trained ML models
├── frontend/
│   ├── index.html                  # Main page
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js                  # Main Cesium app logic
│       ├── satellite_layer.js      # Satellite rendering on globe
│       ├── orbit_renderer.js       # Orbit trail / ground track drawing
│       ├── conjunction_viz.js      # Conjunction event visualization
│       ├── time_controls.js        # Play/pause/scrub time
│       └── sidebar.js              # Search, filters, alert table
└── tests/
    ├── test_propagation.py
    ├── test_conjunction.py
    └── test_api.py
```

---

## Dataset Scaling Path

| Phase | Dataset | Objects | Purpose |
|-------|---------|---------|---------|
| 1 | CelesTrak `stations` | ~15 | Get everything working end-to-end |
| 2 | CelesTrak `visual` | ~150 | Test visualization at moderate scale |
| 3 | CelesTrak `starlink` | ~6,000 | Stress test conjunction detection (all similar altitudes) |
| 4 | CelesTrak `active` + debris | 10,000+ | Full production catalog |

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| C++ / pybind11 build system | Start with a minimal "hello world" binding in Week 0. Get CMake + pybind11 compiling before writing real logic. |
| Cesium.js learning curve | Cesium has excellent docs and a Sandcastle examples gallery. Many satellite demos exist to reference. |
| Computation time at scale (Phase 3-4) | C++ pair scanning + coarse altitude band filtering. Conjunction scan runs as background task, not blocking UI. |
| TLE accuracy degrades over time | Display TLE age as a quality indicator in the UI. Auto-refresh daily. |
| Cesium rendering performance at 6k+ objects | Use Cesium's `PointPrimitiveCollection` (GPU-accelerated) instead of individual entities. Only render orbit trails for selected satellites. |
| ML training data for collision risk | Use Space-Track CDM data. Supplement with synthetic conjunctions if needed. |
| SPICE kernel management | Only need 2-3 small kernels (leap seconds, Earth orientation). Download once, commit paths to config. |
| Scope creep | Stick to the phase plan. Each phase is a working, demoable product. |

---

## TODO Before Starting

- [ ] Create a free Space-Track.org account (needed for CDM historical data)
- [ ] Get a free Cesium Ion access token (for terrain/imagery tiles)
- [ ] Set up the project repo with git
- [ ] Install C++ toolchain (g++/clang, CMake, pybind11)
- [ ] Install Python dependencies: fastapi, uvicorn, scipy, pandas, xgboost, requests, spiceypy
- [ ] Download SPICE kernels (naif0012.tls, pck00011.tpc, earth_latest_high_prec.bpc)
- [ ] Install Orekit Python wrapper
- [ ] Install Docker
- [ ] Build and test a minimal pybind11 "hello world" extension
