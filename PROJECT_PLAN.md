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
  - Phase 1: Space Stations (~30 objects) — ISS, Tiangong, crew vehicles, debris
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
  - `stations` — ISS, Tiangong, crew vehicles, debris (~30 objects) ← Phase 1
  - `visual` — Brightest satellites (~150 objects) ← Phase 2
  - `starlink` — Starlink constellation (~6,000) ← Phase 3
  - `active` — All active satellites (~10,000) ← Phase 4
- **Space-Track.org** — Official USSPACECOM source, free account required
  - Conjunction Data Messages (CDMs) for ML training data

**Data Format (OMM/JSON, not legacy TLE):**
We use CelesTrak's JSON/OMM format instead of legacy TLE because:
- TLE is limited to 5-digit NORAD catalog numbers (cap hit ~July 2026)
- JSON provides ISO 8601 dates (no Y2K epoch ambiguity)
- JSON includes all OMM fields in a structured, parseable format

**Tools:**
- `urllib` — Fetch OMM/JSON data via HTTP (stdlib, no `requests` dependency)
- `pandas` — Organize satellite metadata
- Parquet files for local storage (atomic writes, fast reads)

---

### Component 2: Orbital Propagation Engine (C++)
**What:** Given a TLE, compute where a satellite is (or will be) at any point in time.

**How it works:**
- OMM data + SGP4 algorithm = satellite position in TEME coordinates at time T
- GMST Z-rotation converts TEME → ECEF, then SPICE recgeo converts ECEF → geodetic (lat, lon, alt)
- SGP4 is the standard model used by NORAD/USSPACECOM
- Accounts for Earth's gravity, atmospheric drag, lunar/solar perturbations
- C++ implementation handles thousands of satellites per second

**Tools:**
- **C++ SGP4** — Vallado's SGP4.cpp wrapped via pybind11 into `orbitcore` module
- **GMST Z-rotation** — Custom TEME→ECEF transform (SPICE does NOT know the TEME frame)
- **SPICE / spiceypy** — ECEF → geodetic conversion only (`spice.recgeo()`)
- **pybind11** — Exposes C++ functions to Python seamlessly

**Key functions (exposed to Python via pybind11):**
- `orbitcore.sgp4init(whichconst, opsmode, satnum, epoch, bstar, ndot, nddot, ecco, argpo, inclo, mo, no_kozai, nodeo)` → `Satrec`
- `orbitcore.sgp4(satrec, tsince)` → `((x,y,z), (vx,vy,vz))` in TEME (km, km/s)
- `orbitcore.jday(yr,mo,dy,hr,mn,sec)` → `(jd, jdFrac)`
- `orbitcore.getgravconst(GravConst.WGS72)` → dict of gravity constants

---

### Component 3: FastAPI Backend
**What:** REST API serving satellite data to the Cesium.js frontend.

**Endpoints (implemented):**
| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `/api/health` | GET | Health check | ✅ |
| `/api/satellites` | GET | List all tracked satellites with metadata | ✅ |
| `/api/positions` | GET | Batch positions of all satellites at time T | ✅ |
| `/api/positions/{norad_id}` | GET | Single satellite position by NORAD ID | ✅ |
| `/api/positions/{norad_id}/track` | GET | Ground track points for orbit trail | ✅ |
| `/api/refresh` | POST | Re-fetch latest OMM data from CelesTrak | ✅ |

**Endpoints (planned):**
| Endpoint | Method | Description | Week |
|----------|--------|-------------|------|
| `/api/conjunctions` | GET | Predicted close approaches | 6 |
| `/api/conjunctions/{id}` | GET | Detail for a specific conjunction event | 6 |

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
| Coordinate transforms | **GMST rotation + SPICE** | TEME → ECEF (custom) → geodetic (SPICE recgeo) |
| Conjunction validation | **Orekit** (Python bindings) | Cross-check results against ESA/CNES standard |
| Frontend | Cesium.js | 3D globe, orbit rendering, time animation |
| Backend | FastAPI + uvicorn | REST API serving satellite data |
| Data fetch | urllib (stdlib) | Pull OMM/JSON from CelesTrak / Space-Track |
| Data storage | pandas, Parquet | Satellite catalog and conjunction records |
| Computation | scipy | Closest approach time refinement |
| ML | XGBoost or CatBoost | Collision risk classification |
| Deployment | **Docker** | Containerized app, one-command startup |

---

## Project Structure

```
OrbitWatch/
├── PROJECT_PLAN.md
├── CLAUDE.md                        # AI context for Claude Code sessions
├── requirements.txt
├── orbitcore/                       # C++ extension module (source)
│   ├── CMakeLists.txt              # Build config for pybind11
│   ├── src/
│   │   ├── SGP4.cpp               # Vallado's SGP4 implementation (3,247 lines)
│   │   └── bindings.cpp           # pybind11 Python bindings
│   └── include/
│       └── SGP4.h
├── backend/
│   ├── main.py                     # FastAPI app entry point
│   ├── orbitcore.cpython-312-*.so  # Compiled C++ extension
│   ├── routers/
│   │   └── satellites.py          # All API endpoints (satellites, positions, refresh)
│   ├── models/
│   │   └── schemas.py            # 8 Pydantic response models (OpenAPI schema)
│   ├── core/
│   │   ├── tle_fetcher.py         # GPFetcher — OMM/JSON from CelesTrak + Parquet cache
│   │   ├── propagator.py          # SatellitePropagator — full pipeline orchestrator
│   │   └── coordinate_transforms.py  # TEME → ECEF → geodetic
│   └── data/
│       ├── tle/                   # Cached Parquet files (stations.parquet, etc.)
│       └── spice_kernels/         # SPICE kernels (leap seconds, Earth orientation)
├── frontend/                       # Cesium.js frontend (Week 4)
│   └── index.html
├── progress/                       # Documentation and tracking
│   ├── roadmap.md
│   ├── scaling_tracker.md         # Phase 3 performance items
│   ├── week{N}_plan.md
│   ├── task_logs/                 # Per-task completion logs
│   └── notes/
│       ├── week{N}_notes.md
│       └── key_information.md     # Durable findings and gotchas
└── tests/                          # 265 tests across 7 files
    ├── test_api.py                # 82 tests — FastAPI endpoints + schema validation
    ├── test_propagator.py         # 80 tests — full pipeline
    ├── test_sgp4_cpp.py           # 54 tests — C++ engine + Vallado verification
    ├── test_gp_fetcher.py         # 37 tests — data fetch + cache
    ├── test_coordinate_transforms.py  # 26 tests — TEME→ECEF→geodetic
    └── test_spice.py              # Kernel loading verification
```

**Files planned but not yet created:**
- `backend/routers/conjunctions.py` — Week 6
- `backend/core/conjunction_detector.py` — Week 6
- `backend/ml/risk_classifier.py` — Week 7
- `Dockerfile`, `docker-compose.yml` — Week 8

---

## Dataset Scaling Path

| Phase | Dataset | Objects | Purpose |
|-------|---------|---------|---------|
| 1 | CelesTrak `stations` | ~30 | Get everything working end-to-end |
| 2 | CelesTrak `visual` | ~150 | Test visualization at moderate scale |
| 3 | CelesTrak `starlink` | ~6,000 | Stress test conjunction detection (all similar altitudes) |
| 4 | CelesTrak `active` + debris | 10,000+ | Full production catalog |

---

## Key Risks & Mitigations

| Risk | Mitigation | Status |
|------|------------|--------|
| C++ / pybind11 build system | Started with minimal binding, got CMake + pybind11 compiling early | ✅ Resolved |
| SPICE kernel management | Only need 3 small kernels, downloaded once, paths in config | ✅ Resolved |
| SPICE TEME frame support | SPICE does NOT know TEME — built custom GMST Z-rotation instead | ✅ Resolved |
| Cesium.js learning curve | Cesium has excellent docs and Sandcastle examples gallery | Week 4 |
| Computation time at scale (Phase 3-4) | C++ pair scanning + coarse altitude band filtering. Tracked in scaling_tracker.md | Week 6–8 |
| TLE accuracy degrades over time | `epoch_age_days` surfaced in API responses. Auto-refresh via POST /api/refresh | ✅ Mitigated |
| Cesium rendering performance at 6k+ objects | Use Cesium's `PointPrimitiveCollection` (GPU-accelerated) instead of individual entities | Week 5/8 |
| ML training data for collision risk | Use Space-Track CDM data. Supplement with synthetic conjunctions if needed | Week 7 |
| Scope creep | Stick to the phase plan. Each phase is a working, demoable product | Ongoing |

---

## Setup Checklist

- [x] Set up the project repo with git
- [x] Install C++ toolchain (g++/clang, CMake, pybind11)
- [x] Install Python dependencies: fastapi, uvicorn, scipy, pandas, spiceypy
- [x] Download SPICE kernels (naif0012.tls, pck00011.tpc, earth_latest_high_prec.bpc)
- [x] Build and test pybind11 C++ extension (orbitcore)
- [ ] Create a free Space-Track.org account (needed for CDM historical data — Week 7)
- [ ] Get a free Cesium Ion access token (for terrain/imagery tiles — Week 4)
- [ ] Install Orekit Python wrapper (Week 6)
- [ ] Install Docker (Week 8)
