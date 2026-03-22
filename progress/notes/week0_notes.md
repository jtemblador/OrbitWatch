# Week 0–1 Notes: Setup + C++ Foundation

**Date:** Mar 20–21, 2026
**Focus:** Environment setup, C++ toolchain verification, SPICE configuration
**Tasks covered:** 0.1 (Accounts), 0.2 (Project Setup), 0.3 (C++ Toolchain), 0.4 (SPICE Setup)

---

## Key Decisions Made

### Tech Stack Refinement for Aerospace Appeal (Task 0.2)
**Decision:** Replaced pure Python SGP4 stack with C++ (pybind11) + SPICE + Orekit.

**Why:**
- SpaceX, K2 Space, Aerospace Corp all use C++ for flight software and orbit propagation
- SPICE is NASA/JPL-standard for coordinate transforms — name-drop on resume
- Orekit is ESA/CNES standard — signals astrodynamics knowledge
- Shows judgment: use right tool for right job (C++ for hot path, Python for API/ML)

**Impact:** 8-week timeline instead of 4 weeks, but much stronger portfolio signal.

---

## Technical Discoveries

### Task 0.3: pybind11 + CMake
- **Hello world extension**: Successfully compiled C++ function and called from Python
- **Key insight:** pybind11 is simpler than expected — no manual CFFI. Just declare functions in `bindings.cpp` and they're importable.
- **Build output:** Module name follows Python version: `orbitcore.cpython-312-x86_64-linux-gnu.so`
- **Placement:** Copy .so to `backend/` for easy imports

### Task 0.4: SPICE (NASA/NAIF toolkit)
- **Kernels needed:** Just 3 small files (5KB leap seconds, 129KB constants, 4.8MB orientation)
- **ECI → geodetic:** SPICE transforms work seamlessly. Test confirmed ECI [6700, 1200, 400] km → reasonable lat/lon/alt
- **SSL note:** Download required disabling SSL verification (temp workaround for dev environment)
- **Key functions learned:**
  - `sp.str2et()` — human time → ephemeris time (SPICE's time standard)
  - `sp.pxform()` — rotation matrix between frames (J2000 → ITRF93)
  - `sp.mxv()` — matrix-vector multiplication
  - `sp.recgeo()` — cartesian → geodetic (x,y,z → lon,lat,alt)

### Task 0.2: Environment & Dependencies
- System Python already has necessary tools (g++, CMake)
- `--break-system-packages` workaround avoids virtual env complexity
- All core deps installed successfully: fastapi, scipy, pandas, spiceypy, sgp4, xgboost

---

## Blockers & Resolutions

### Task 0.5 (Deferred): Orekit Python Wrapper Unavailable
**Blocker:** `pip install orekit` fails — no PyPI package.

**Resolution:** Orekit requires Java + separate installation. Deferred to Week 6 when actually needed (conjunction cross-validation). Not on critical path for Weeks 2–5.

**Lesson:** Java-based tools have friction in Python environments. Document setup instructions when we get there.

---

## Architecture Insights

### C++ Module as Shared Library
```
orbitcore/ (C++ source)
  ├── src/
  │   ├── hello.cpp (test function)
  │   ├── sgp4.cpp (to write)
  │   ├── conjunction.cpp (to write)
  │   └── bindings.cpp (pybind11 exposure)
  └── include/
      ├── hello.h
      ├── sgp4.h (to write)
      └── conjunction.h (to write)

build/ (CMake output)
  └── orbitcore.cpython-312-x86_64-linux-gnu.so

backend/ (copy for import)
  └── orbitcore.cpython-312-x86_64-linux-gnu.so ← import orbitcore
```

**Key insight:** Once .so is in `backend/`, it's importable anywhere in the app. No PYTHONPATH gymnastics.

---

## Learnings for Week 2

### SGP4 Implementation Plan (Prep for Task 2.3)
- C library `libsgp4` (native) wraps via pybind11
- Input: TLE lines → Output: (x, y, z) ECI at time T
- SPICE handles coordinate transforms after that
- Don't reinvent — leverage existing sgp4 crate or library

### TLE Parsing (Prep for Task 2.1)
- `sgp4` Python library already installed — can use for parsing, even if propagation moves to C++
- TLE format is fixed: 3 lines (name + 2 element lines), each character position matters

### Next Critical Test (Tasks 2.2 + 2.3)
Need to verify: **SGP4 + coordinate transforms together**
- Fetch real ISS data from CelesTrak (Task 2.1 — DONE)
- Run through SGP4 propagator → get TEME position
- Convert TEME → ECEF via GMST rotation → geodetic via SPICE recgeo (Task 2.2 — DONE)
- Compare against known ISS tracker (e.g., N2YO) — should match within 1–2 km

**Update (Mar 22):** Task 2.2 resolved this. SPICE does NOT know TEME — we use GMST Z-rotation instead. See `progress/task_logs/task_2_2_coordinate_transforms.md` for full details.

---

## Time Spent
- Environment setup: ~1 hour
- C++ toolchain: ~1 hour
- SPICE download + test: ~30 min
- Documentation: ~30 min
- **Total:** ~3 hours

**Status:** All critical items done. Ready for Week 2.
