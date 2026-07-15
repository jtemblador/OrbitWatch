# OrbitWatch — Satellite Orbit Tracker + Conjunction Screener
**Status:** COMPLETE and LIVE (Phase 10 done, Jul 15 2026). Screens the full ~16k active catalog, gated byte-identical in CI. https://jtemblador.github.io/OrbitWatch/
**Timeline:** Mar 20 – Jul 15, 2026

> **Note:** this is the original planning doc, updated to reflect the finalized shape.
> The project is a **geometric conjunction screener validated against CelesTrak SOCRATES**,
> not an ML collision predictor — the ML risk classifier, Orekit, and Docker were dropped
> (see *Decisions Made*). For the current state see `README.md`; `CLAUDE.md` → Key Files
> is the living file map, and `progress/roadmap.md` is the phase-by-phase log.

---

## The Problem

There are 10,000+ active satellites and 30,000+ pieces of tracked debris orbiting Earth. Collisions are a real and growing threat — the 2009 Iridium-Cosmos collision created 2,000+ debris fragments. Companies like SpaceX (Starlink has 6,000+ satellites), Aerospace Corp, and Northrop Grumman actively work on space situational awareness. This project builds a system that tracks real satellites, visualizes their orbits, and predicts potential close approaches.

---

## What We're Building

A web-based dashboard that:
1. Fetches real satellite orbit data (TLE) from public sources
2. Propagates orbits to compute satellite positions at any point in time
3. Renders satellites on an interactive Cesium.js 3D globe in real-time
4. Detects close approaches (conjunctions) between satellites
5. Reports each conjunction's geometry — TCA, miss distance, relative speed, RTN — and validates it against CelesTrak SOCRATES *(a geometric screener; no collision-probability/ML — see Decisions)*
6. Lets users search/filter by satellite name, type, orbit altitude, country

---

## Decisions Made

- **Visualization:** Cesium.js (industry-standard 3D globe, used by AGI/DoD)
- **Compute core:** C++ with pybind11 bindings (orbit propagation + conjunction detection)
- **Coordinate transforms:** NASA SPICE toolkit via spiceypy
- **Conjunction validation:** CelesTrak **SOCRATES** (open, SGP4-based → same method) + Space-Track `gp_history` epoch-matching *(Orekit was evaluated and dropped)*
- **Backend:** Python (FastAPI) serving orbital data to the Cesium.js frontend *(local dev; the deployed site is a static export)*
- **Deployment:** **Static website** — CI-built `snapshot.json` on GitHub Pages, client-side propagation *(Docker dropped Jun 24)*
- **Scope (finalized Jun 11):** dropped the ML risk classifier and Pc computation — a real risk model needs operator-only CDM covariance (SSA agreement); the honest, higher-value headline is a **SOCRATES-validated geometric screener**
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
│  Data   │ │  C++ Core      │ │  SOCRATES Validation │
│  Layer  │ │  (pybind11)    │ │  (offline: fetch →   │
│(GP/OMM) │ │  SGP4 + SPICE  │ │  screen → match →    │
└─────────┘ │  + Conjunction │ │  epoch_ok via DSE)   │
            │  cascade       │ └──────────────────────┘
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
- **Space-Track.org** — Official USSPACECOM source, free account (cookie auth, no API key)
  - **`gp_history`** — historical elsets for epoch-matched validation (Phase 8.3). `cdm_public` (real operational CDMs) is an optional cross-method check (8.6 stretch)

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

### Component 5: Conjunction Screening (C++ cascade)
**What:** Identify when two satellites pass close, and report the geometry — TCA, miss distance, relative speed, RTN components. A *screener*, not collision avoidance (no probability of collision).

**Algorithm (the screening cascade):**
1. **Coarse filter (C++):** altitude-band pre-cut — only check pairs whose perigee/apogee shells overlap. Reduces the O(n²) work.
2. **Medium filter (C++, GIL-released):** time-step each surviving pair over the window with a **velocity-aware no-skip interval bound** — a fast crosser samples far from its true miss on a coarse grid, so plain distance thresholding would miss real conjunctions.
3. **Fine filter:** Newton on the relative range-rate (`t ← t − (Δr·Δv)/|Δv|²`) to nail the exact TCA + minimum distance; `fine_filter_batch` steps all windows together (7.3). A scipy oracle is kept for cross-validation.
4. **Validation:** reproduce **CelesTrak SOCRATES** on the same objects/window and check TCA/miss agreement, with the epoch match *verified* via `DSE` (Phase 8).

**Tools:**
- **C++ (pybind11)** — coarse + medium filters (the O(n²) hot path, GIL released)
- `numpy` (vectorized Newton) + `scipy.optimize` (oracle only) — fine-stage TCA refinement
- **CelesTrak SOCRATES + Space-Track `gp_history`** — same-method validation anchor *(replaced the originally-planned Orekit cross-check)*

---

### Component 6: Validation Against SOCRATES *(replaced the original ML risk classifier)*
**What:** Prove the screener is correct by reproducing an established open service.

**Why not ML:** the original plan here was an XGBoost/CatBoost collision-risk classifier. Dropped Jun 11 — a real risk model needs covariance / Pc from operational CDMs, which require an SSA Sharing Agreement (operators only). Without it, ML can't beat a distance threshold, and none of the target aerospace roles asked for it. The honest, higher-value headline is a **validated geometric screener**.

**What we built instead (Phase 8):** fetch the SOCRATES bulk run → run our screener on the same flagged objects/window → match by object-pair + TCA → report reproduction rate + TCA/miss deltas **segmented by element age (`DSE`)**, with the epoch match verified via `DSE` and lifted with Space-Track `gp_history`.

**Result:** **100% reproduction on epoch-matched elements, ΔTCA / Δmiss = 0.000** (byte-level same-method agreement). See `validation/socrates_report.md`; honest limits in `validation/sgp4_uncertainty.md`.

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
| Conjunction validation | **CelesTrak SOCRATES** + Space-Track `gp_history` | Same-method reproduction check, epoch-matched via `DSE` |
| Frontend | Cesium.js | 3D globe, orbit rendering, time animation |
| Backend | FastAPI + uvicorn | REST API serving satellite data (local dev) |
| Data fetch | requests → curl fallback (`http_fetch`) | Pull OMM/JSON from CelesTrak / Space-Track |
| Data storage | pandas, Parquet | Satellite catalog and conjunction records |
| Computation | numpy + scipy | Vectorized fine-stage TCA (scipy oracle for cross-val) |
| Reporting | matplotlib | Validation-report figures |
| Deployment | **Static site** (GitHub Pages) | CI-built `snapshot.json`, client-side propagation |

---

## Project Structure

*(Illustrative — see `CLAUDE.md` → Key Files for the current, maintained file map.)*

```
OrbitWatch/
├── PROJECT_PLAN.md
├── CLAUDE.md                        # AI context for Claude Code sessions
├── requirements.txt
├── orbitcore/                       # C++ extension module (source)
│   ├── CMakeLists.txt              # Build config for pybind11
│   ├── src/
│   │   ├── SGP4.cpp               # Vallado's SGP4 implementation (third-party)
│   │   ├── screening.cpp          # Conjunction-screening engine (pure C++)
│   │   ├── bindings.cpp           # pybind11 module entry (calls the binders)
│   │   ├── bind_satrec.cpp        # Bindings: satellite record + init
│   │   ├── bind_propagation.cpp   # Bindings: propagation + time conversion
│   │   └── bind_screening.cpp     # Bindings: the conjunction screen boundary
│   └── include/
│       ├── SGP4.h
│       ├── screening.h
│       └── bindings.h
├── backend/
│   ├── main.py                     # FastAPI app entry point
│   ├── orbitcore.cpython-312-*.so  # Compiled C++ extension
│   ├── routers/
│   │   └── satellites.py          # All API endpoints (satellites, positions, refresh)
│   ├── models/
│   │   └── schemas.py            # 10 Pydantic response models (OpenAPI schema)
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
└── tests/                          # 541 tests across 14 files (SGP4, transforms, API,
    │                               #   propagator, conjunctions, SOCRATES/Space-Track, …)
    └── …                           # see CLAUDE.md → Tests for the current list
```

*(The conjunction pipeline — `backend/core/conjunctions.py`, `screening_volumes.py`, the
SOCRATES/Space-Track modules, `scripts/validate_socrates.py`, and `validation/` — all exist
now; see `CLAUDE.md` → Key Files. The originally-planned `backend/ml/risk_classifier.py`,
`Dockerfile`, and `docker-compose.yml` were **dropped** with the ML/Docker pivots.)*

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
| ~~ML training data for collision risk~~ | **Dropped (Jun 11)** — Pc/covariance needs operator-only CDMs (SSA agreement); pivoted to a SOCRATES-validated geometric screener | ✅ Resolved (pivot) |
| Scope creep | Stick to the phase plan. Each phase is a working, demoable product | Ongoing |

---

## Setup Checklist

- [x] Set up the project repo with git
- [x] Install C++ toolchain (g++/clang, CMake, pybind11)
- [x] Install Python dependencies: fastapi, uvicorn, scipy, pandas, spiceypy
- [x] Download SPICE kernels (naif0012.tls, pck00011.tpc, earth_latest_high_prec.bpc)
- [x] Build and test pybind11 C++ extension (orbitcore)
- [x] Create a free Space-Track.org account (used for `gp_history` epoch-matched validation, Phase 8.3)
- [ ] Get a free Cesium Ion access token + domain-restrict it (for the deployed globe — Phase 9.4)
- [x] ~~Install Orekit Python wrapper~~ — **dropped** (SOCRATES is the validation anchor)
- [x] ~~Install Docker~~ — **dropped** (static-website deploy, Phase 9)
