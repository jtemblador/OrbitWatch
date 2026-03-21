# Week 0–1 — Setup + C++ Foundation (Due: Apr 2, 2026)

## Action Items

### Accounts to Create
- [x] Create free Space-Track.org account (https://www.space-track.org/auth/createAccount)
  - Needed for Conjunction Data Messages (CDM) — ML training data
  - Use your personal email, takes a few minutes to approve
- [x] Get free Cesium Ion access token (https://ion.cesium.com/signup)
  - Needed for 3D globe terrain and imagery tiles
  - Free tier is more than enough for this project

### Project Setup
- [x] Initialize git repo in `/home/j0e/Projects/OrbitWatch/`
- [x] Install Python dependencies: fastapi, uvicorn, scipy, pandas, numpy, xgboost, requests, spiceypy, sgp4
- [x] Scaffold project folder structure (backend/, frontend/, orbitcore/, tests/)
- [x] Create requirements.txt

### C++ Toolchain Setup (Cannot Rush This)
- [x] Install C++ compiler (g++ v12.2.0)
- [x] Install CMake (v3.25.1)
- [x] Install pybind11 (v3.0.2)
- [x] Create minimal pybind11 "hello world" extension
  - [x] Wrote simple C++ function in hello.cpp
  - [x] Created bindings.cpp to expose to Python
  - [x] Compiled and tested — works from Python ✓
- [x] Set up CMakeLists.txt in orbitcore/ directory

### SPICE Setup
- [x] Install spiceypy (v8.0.2)
- [x] Download required SPICE kernels from NAIF:
  - [x] `naif0012.tls` — leap seconds (5.2 KB)
  - [x] `pck00011.tpc` — planetary constants (129 KB)
  - [x] `earth_latest_high_prec.bpc` — Earth orientation (4.8 MB)
- [x] Test: load kernels, convert ECI → lat/lon — verified ✓

### Orekit Setup
- [ ] Install Orekit Python wrapper (requires Java — defer to Week 6 when actually needed)
- [ ] Download Orekit data files (orekit-data.zip)
- [ ] Verify import works and data loads
**Note:** Orekit is only used in Week 6 for cross-validating conjunction results. Safe to defer.

### Learning / Research
- [ ] Browse Cesium.js Sandcastle examples (https://sandcastle.cesium.com/) — satellite/orbit demos
- [ ] Read pybind11 docs — focus on building with CMake and passing arrays
- [ ] Skim CelesTrak API docs (https://celestrak.org/NORAD/documentation/gp-data-formats.php)
- [ ] Verify CelesTrak TLE fetch works (no account needed — just a GET request)
- [ ] Look at a few TLE files to understand the format

**Status:** Self-directed learning items — do at your own pace before Week 2.

### Nice to Have (Not Blocking)
- [ ] Pick a color scheme for the UI
- [ ] Find a reference project or screenshot to use as visual inspiration
- [ ] Install Docker (can also do this in Week 8)

---

## Status
**Mostly complete** — Mar 21, 2026

**Critical path done:**
- ✓ Python environment (system Python with deps installed)
- ✓ Git repo initialized
- ✓ Project structure scaffolded
- ✓ C++ toolchain verified (g++, CMake, pybind11, hello world)
- ✓ SPICE kernels downloaded and tested
- ✓ TLE parsing ready (sgp4 library installed)

**Deferred (not blocking):**
- Orekit setup → defer to Week 6 (requires Java, only needed for validation)
- Learning tasks → self-directed before Week 2
