# Week 4 Notes — Cesium.js Globe

---

## Pre-Week Research: Satellite Visualization Landscape

Surveyed 9 satellite tracker sites before starting. Key takeaways:

- **Best performers use custom WebGL** (satellitemap.space, keeptrack.space) but require months of frontend work
- **Three.js** (satellitetracker3d.com) uses `THREE.Points` single draw call + Web Workers for 24K satellites — smooth but no built-in geospatial support
- **Cesium.js works at scale** when used correctly (trackthesky.com: 9K sats) but fails badly with Entity API (AstriaGraph: laggy at 17K)
- **Our advantage:** Server-side C++ SGP4 means frontend just renders precomputed positions — no main-thread contention with orbit math (every other site does client-side SGP4)

Full research captured in `progress/week4_plan.md` under "Competitive Research."

---

## Task 4.1 — Cesium.js Setup

### Key Decisions

1. **Cesium 1.139.1 via jsDelivr CDN** — no npm/bundler for Phase 1. Pinned version to avoid breaking changes.

2. **Terrain disabled** — dev machine is ThinkPad T480s with Intel UHD 620 (integrated GPU). Terrain tiles are the heaviest Cesium GPU load. Using ellipsoid-only with default Ion imagery. Can re-enable on discrete GPU.

3. **All default Cesium UI stripped** — timeline, animation, geocoder, homeButton, sceneModePicker, etc. all disabled. We'll build our own info panel (Task 4.3). Keeps the viewport clean and reduces widget overhead.

4. **`resolutionScale = 1.0`** — prevents HiDPI rendering on the integrated GPU.

5. **Token in gitignored `config.js`** — standard pattern. Guard in `app.js` shows helpful setup instructions if missing.

6. **StaticFiles catch-all changes 405→404** — mounting `StaticFiles(html=True)` at `/` means undefined routes return 404 from the static mount instead of 405 from FastAPI. Test updated to accept either.

7. **Cesium credit attribution kept visible** — Ion ToS requires it for free accounts. Minimized font size instead of hiding.
