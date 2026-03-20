# Week 0–1 — Setup + C++ Foundation (Due: Apr 2, 2026)

## Action Items

### Accounts to Create
- [ ] Create free Space-Track.org account (https://www.space-track.org/auth/createAccount)
  - Needed for Conjunction Data Messages (CDM) — ML training data
  - Use your personal email, takes a few minutes to approve
- [ ] Get free Cesium Ion access token (https://ion.cesium.com/signup)
  - Needed for 3D globe terrain and imagery tiles
  - Free tier is more than enough for this project

### Project Setup
- [ ] Initialize git repo in `/home/j0e/Projects/OrbitWatch/`
- [ ] Create Python virtual environment
- [ ] Install Python dependencies: fastapi, uvicorn, scipy, pandas, xgboost, requests, spiceypy
- [ ] Scaffold project folder structure (backend/, frontend/, orbitcore/, tests/)
- [ ] Create requirements.txt

### C++ Toolchain Setup (Cannot Rush This)
- [ ] Install C++ compiler (g++ or clang)
- [ ] Install CMake
- [ ] Install pybind11 (`pip install pybind11` + system headers)
- [ ] Create minimal pybind11 "hello world" extension
  - Write a simple C++ function, compile it, call it from Python
  - This verifies the entire build pipeline works before writing real code
- [ ] Set up CMakeLists.txt in orbitcore/ directory

### SPICE Setup
- [ ] Install spiceypy (`pip install spiceypy`)
- [ ] Download required SPICE kernels from NAIF:
  - `naif0012.tls` — leap seconds
  - `pck00011.tpc` — planetary constants (Earth shape)
  - `earth_latest_high_prec.bpc` — Earth orientation
- [ ] Write a quick test: load kernels, convert a known ECI position to lat/lon, verify result

### Orekit Setup
- [ ] Install Orekit Python wrapper
- [ ] Download Orekit data files (orekit-data.zip)
- [ ] Verify import works and data loads

### Learning / Research
- [ ] Browse Cesium.js Sandcastle examples (https://sandcastle.cesium.com/) — satellite/orbit demos
- [ ] Read pybind11 docs — focus on building with CMake and passing arrays
- [ ] Skim CelesTrak API docs (https://celestrak.org/NORAD/documentation/gp-data-formats.php)
- [ ] Verify CelesTrak TLE fetch works (no account needed — just a GET request)
- [ ] Look at a few TLE files to understand the format

### Nice to Have (Not Blocking)
- [ ] Pick a color scheme for the UI
- [ ] Find a reference project or screenshot to use as visual inspiration
- [ ] Install Docker (can also do this in Week 8)

---

## Status
**Not started** — beginning Mar 20, 2026
