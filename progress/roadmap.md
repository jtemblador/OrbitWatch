# OrbitWatch — Roadmap (8 Weeks)
**Timeline:** Mar 20 – May 15, 2026

## Week 0–1: Setup + C++ Foundation (Mar 20 – Apr 2)
- Create accounts (Space-Track, Cesium Ion)
- Init repo, virtual env, Python dependencies
- Install C++ toolchain (g++/clang, CMake, pybind11)
- Build and test a minimal pybind11 "hello world" extension
- Install spiceypy, download SPICE kernels, verify coordinate conversion works
- Install Orekit Python wrapper
- Verify CelesTrak TLE fetch works
- Research Cesium.js Sandcastle examples

## Week 2: TLE Data + C++ SGP4 Propagation (Apr 3 – Apr 9)
- Build TLE fetcher (CelesTrak stations group)
- Implement SGP4 propagation in C++, expose via pybind11
- Use SPICE for ECI → ECEF → lat/lon/alt coordinate transforms
- Verify ISS position matches known trackers
- Unit tests for propagation accuracy

## Week 3: FastAPI Backend (Apr 10 – Apr 16)
- FastAPI app skeleton with uvicorn
- `/api/satellites` and `/api/positions` endpoints
- Wire propagator.py to call C++ extension + SPICE
- Serve Phase 1 satellite data (stations) via API
- Unit tests for API endpoints

## Week 4: Cesium.js Globe — Basic (Apr 17 – Apr 23)
- Set up Cesium.js frontend with globe + Ion token
- Render Phase 1 satellites (stations) as points on globe
- Click satellite → show info popup
- Connect frontend to FastAPI backend

## Week 5: Cesium.js Globe — Polish (Apr 24 – Apr 30)
- Orbit trail rendering for selected satellite
- Ground track line
- Time controls (play/pause/speed/scrub)
- Toggle constellation groups on/off

## Week 6: Conjunction Detection in C++ (May 1 – May 7)
- Implement coarse + medium filter pair scanning in C++, expose via pybind11
- Fine filter with scipy.optimize in Python
- Test with stations dataset, verify against known ISS close approaches
- Cross-validate results against Orekit
- Add `/api/conjunctions` endpoint
- Scale to Phase 2 (150 satellites)

## Week 7: ML Risk Classifier (May 8 – May 11)
- Fetch CDM data from Space-Track for training
- Feature engineering (miss distance, relative velocity, object types, TLE age, altitude)
- Train XGBoost/CatBoost risk classifier
- Integrate into conjunction pipeline (LOW / MEDIUM / HIGH / CRITICAL)

## Week 8: Docker + Polish + Demo (May 12 – May 15)
- Alert table in sidebar with conjunction results
- Search & filter interface
- Conjunction visualization on globe (connecting line, camera fly-to)
- Dockerfile + docker-compose.yml (one-command startup)
- Scale to Phase 3 (Starlink), optimize rendering with PointPrimitiveCollection
- Write README.md (project description, screenshots, how to run)
- Record demo GIF/video for portfolio

> **⚠ PERF FLAG (before Phase 3 scale-up):** Multiple `iterrows()` calls across the codebase
> are fine at 30 sats but will be slow at 6,000. All items tracked in `progress/scaling_tracker.md`.

---

## Milestones

| Date | Milestone | Demoable? |
|------|-----------|-----------|
| Apr 2 | Repo set up, pybind11 compiling, TLE fetch works, SPICE verified | No |
| Apr 9 | C++ SGP4 propagating real satellites, positions accurate | Backend only |
| Apr 16 | FastAPI serving satellite data via API | Backend only |
| Apr 23 | 3D globe with satellites rendered from live API | Yes |
| Apr 30 | Orbit trails, time controls, interactive globe | Yes |
| May 7 | Conjunction detection working (C++ + Orekit validated) | Yes |
| May 11 | ML risk classifier trained and integrated | Yes |
| May 15 | Full app polished, Dockerized, README done, demo recorded | Portfolio-ready |
