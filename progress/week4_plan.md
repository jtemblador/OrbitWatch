# Week 4 — Cesium.js Globe (Apr 17–23, 2026)

**Goal:** Render Phase 1 satellites on an interactive 3D globe powered by Cesium.js, connected to the FastAPI backend. By the end of this week, a user can open a browser, see ~30 satellites orbiting Earth in real-time, and click any satellite to see its info.

---

## What We Have (from Weeks 2–3)

| Component | Endpoint | What it returns |
|-----------|----------|-----------------|
| Satellite metadata | `GET /api/satellites` | 30 stations with name, norad_id, epoch, orbital params |
| Batch positions | `GET /api/positions?time=T` | lat, lon, alt_km, speed_km_s for all 30 sats at time T |
| Single position | `GET /api/positions/{norad_id}` | Position for one satellite |
| Ground track | `GET /api/positions/{norad_id}/track?duration_min=90&steps=60` | 60 lat/lon/alt points spanning ~1 orbit |
| Data refresh | `POST /api/refresh` | Triggers fresh TLE fetch from CelesTrak |
| OpenAPI docs | `GET /docs` | Swagger UI with typed schemas for all endpoints |

**All responses are Pydantic-validated JSON.** Frontend just needs to fetch and render.

---

## Prerequisites

- [x] Create a free Cesium Ion account at [ion.cesium.com](https://ion.cesium.com)
- [x] Get an access token for terrain/imagery tiles
- [x] Store token in `frontend/js/config.js` (gitignored, template in `config.example.js`)

---

## Main Tasks

### ✅ 1. Cesium.js Setup (`frontend/index.html`)

Get a basic Cesium globe rendering in the browser.

**What to build:**
- HTML page loading Cesium.js via CDN (no npm/bundler for Phase 1)
- Cesium Ion access token configuration
- Basic `Cesium.Viewer` with Earth globe, default imagery, terrain
- FastAPI serves `frontend/` as static files

**Success criteria:**
- [x] Opening `http://localhost:8000` shows a spinning 3D globe
- [x] Globe has terrain and satellite imagery from Cesium Ion (terrain disabled for UHD 620, imagery via Ion)
- [x] No console errors

**Actual:** Cesium 1.139.1 via jsDelivr CDN. Terrain disabled for integrated GPU performance. Token in gitignored `config.js` with missing-token guard. StaticFiles mount after API routes. 82/82 tests passing.

---

### ✅ 2. Satellite Points on Globe

Fetch satellite positions from the API and render them as points.

**What to build:**
- On page load, `fetch('/api/positions')` to get all 30 satellite positions
- Render each satellite as a colored point using `Cesium.PointPrimitiveCollection` (GPU-accelerated, scales to Phase 3)
- Point color: all red for Phase 1 (stations). Will differentiate by type in Phase 2+ when `object_type` is available.
- Auto-refresh positions every 5 seconds (satellites move ~40 km/s → noticeable drift in seconds)

**Success criteria:**
- [x] ~30 colored dots visible on globe at correct positions
- [x] Points move in real-time as positions auto-refresh
- [x] ISS visible at ~420 km altitude, moving noticeably

**Actual:** `PointPrimitiveCollection` + `LabelCollection` with smooth interpolation (~20fps throttled). CartoDB dark tiles for base map. Labels use FILL style with translucent background (FILL_AND_OUTLINE caused rendering artifacts). 279/279 tests passing.

**Performance note:** Use `PointPrimitiveCollection` not individual `Entity` objects. Entities are fine at 30 but will choke at 6,000 Starlink sats (Phase 3). Starting with the scalable approach now avoids a rewrite.

---

### ✅ 3. Satellite Info Popup (Click Interaction)

Click a satellite point → show info panel.

**What to build:**
- Click handler on satellite points
- On click, fetch `/api/positions/{norad_id}` for fresh data
- Display popup/panel with: name, NORAD ID, altitude, speed, lat/lon, epoch age
- Close popup on click elsewhere or click X

**Success criteria:**
- [x] Clicking a satellite shows info popup with correct data
- [x] Popup updates with fresh position data
- [x] Clicking elsewhere dismisses popup

**Actual:** Bottom-left fixed panel with vertical key-value table. Shows position data (live from API) + orbital params (cached at startup from `/api/satellites`). Auto-refreshes every 5 seconds. Dark theme with cyan (#4fc3f7) accent. `ScreenSpaceEventHandler` for click detection via `scene.pick()`.

---

### ✅ 4. Orbit Trail for Selected Satellite

Show the orbital path when a satellite is selected.

**What to build:**
- On satellite click, fetch `/api/positions/{norad_id}/track?duration_min=90&steps=120`
- Render as a polyline using `Cesium.PolylineCollection` (or entity polyline)
- Trail follows the ground track (lat/lon projected onto globe surface) or 3D orbit path at altitude
- Clear previous trail when selecting a different satellite

**Success criteria:**
- [x] Selecting a satellite shows its ~90-minute orbit trail
- [x] Trail is visually smooth (120 points over 90 min)
- [x] Selecting a different satellite replaces the trail
- [x] ISS trail wraps around globe at ~51.6° inclination

**Actual:** Entity polyline with `clampToGround: true` (ground track projection), 360 steps over 90 minutes. Toggle checkbox in info panel. Selection indicator: enlarged point + cyan outline ring. Initially tried `PolylineCollection` at orbital altitude — straight Cartesian chords caused gaps on far side of globe + perspective "lift" at the limb. Switched to Entity polyline with surface projection (industry standard). Data pipeline cross-verified against python-sgp4 (sub-mm) and wheretheiss.at API.

---

### ✅ 5. Static Files + Layout Polish

Wire up the frontend to be served by FastAPI and add basic styling.

**What to build:**
- FastAPI `StaticFiles` mount for `frontend/` directory
- Minimal CSS: globe fills viewport, info panel overlays top-right corner
- Satellite name labels (optional — may be too cluttered at 30 sats, but try it)
- Page title: "OrbitWatch — Satellite Tracker"

**Success criteria:**
- [x] `uvicorn backend.main:app` serves both API and frontend
- [x] Clean, full-viewport layout with no scrollbars
- [x] Works in Chrome/Firefox/Edge

---

## File Structure

```
frontend/
├── index.html          ✅ CREATED — main page with Cesium 1.139.1 via CDN
├── css/
│   └── style.css       ✅ CREATED — full-viewport layout
└── js/
    ├── config.js       ✅ CREATED — Cesium Ion token (gitignored)
    ├── config.example.js ✅ CREATED — token template for repo cloners
    ├── app.js          ✅ CREATED — init Cesium viewer, token guard, UHD 620 opts
    ├── satellites.js   ✅ CREATED — fetch positions, render points + labels, interpolation
    └── info-panel.js   ✅ CREATED — click handler, info panel, orbit trail

backend/
└── main.py             ✅ MODIFIED — added StaticFiles mount for frontend/
```

---

## Implementation Order

1. ✅ **Cesium setup** — globe rendering, Ion token, static file serving
2. ✅ **Satellite points** — fetch + render 30 dots, labels, interpolation, dark tiles
3. ✅ **Click interaction** — info panel with position + orbital data, auto-refresh
4. ✅ **Orbit trail** — solid cyan polyline, 90-min ground track, toggle checkbox
5. ✅ **Polish** — layout, styling, cross-browser verified (Chrome + Firefox)

---

## Competitive Research (Pre-Week 4)

Surveyed 9 satellite visualization sites to inform architecture decisions.

### Rendering Engine Landscape

| Site | Renderer | Max Objects | SGP4 Location |
|------|----------|-------------|---------------|
| satellitemap.space | Custom WebGL (TWGL.js) | 8,000+ | Client (satellite.js) |
| satellitetracker3d.com | Three.js + Web Workers | 24,000+ | Client (satellite.js) |
| AstriaGraph (UT Austin) | **Cesium.js** (Entity API) | 17,000+ | Client (Kepler per-frame) |
| keeptrack.space | Custom WebGL 2.0 | 37,000+ | Client (Web Workers) |
| trackthesky.com | **Cesium.js** | 9,000+ | Client (satellite.js) |
| stuffin.space | Custom WebGL + GLSL | Full catalog | Client (satellite.js) |
| agsattrack.com | **Cesium.js** | Dynamic | Client |
| 3dsatellitetracker.com | Three.js | ~1,000 | Client |
| scad3d.com | Three.js (likely) | Thousands | Client |

### Key Findings

1. **Cesium.js is validated** — trackthesky.com runs 9K+ satellites on Cesium successfully. AstriaGraph is laggy because it uses Entity + CallbackProperty (per-frame JS Kepler solving for 17K objects). We will use `PointPrimitiveCollection` + precomputed server-side positions — completely different perf profile.

2. **Our server-side SGP4 is an advantage** — Every other site runs satellite.js (client-side SGP4). Our C++ propagation backend means the frontend just renders precomputed positions — no main-thread contention with orbit math.

3. **UHD 620 (integrated GPU) constraint** — Dev machine has no discrete GPU. Mitigations: disable terrain initially (ellipsoid only), low-res imagery, cap pixel ratio at 1x, use `PointPrimitiveCollection` (single draw call).

4. **Performance patterns from the best sites:**
   - satellitetracker3d.com: All 24K sats as ONE `THREE.Points` draw call + Web Workers for propagation
   - satellitemap.space: Viewport-based label culling (names only for central screen area), connection-speed-aware asset loading
   - keeptrack.space: Custom WebGL 2.0, handles 37K objects at 60fps

5. **UI patterns worth adopting (future weeks):**
   - Info panel with TLE data, orbital params, speed/height/lat/lon (satellitetracker3d.com)
   - Bottom-left quick-info overlay on hover (satellitetracker3d.com)
   - Color-coding by object type: payload=blue, debris=gray, rocket body=orange (stuffin.space)
   - Time controls: pause/10x/60x speed (trackthesky.com) — planned for Week 5
   - Orbit filters by type (GEO/MEO/LEO/HEO) and tag groups (trackthesky.com)
   - Proximity alert color highlighting (scad3d.com) — relevant for conjunction visualization

6. **Why NOT Three.js or custom WebGL:** Both require building Earth rendering, WGS84 coordinate handling, camera controls, and picking from scratch. Cesium provides all of this out of the box. With 5 weeks remaining and the focus on the full pipeline (SGP4 → API → visualization → ML), Cesium's development speed advantage is decisive. Three.js is valid but costs 2-3 extra weeks of frontend work.

---

## Things to Watch

| Concern | Detail |
|---------|--------|
| Cesium Ion token | Must NOT be committed to repo. Use env var or separate config file in `.gitignore` |
| `PointPrimitiveCollection` vs `Entity` | Start with PointPrimitiveCollection — it's GPU-accelerated and scales to 6k+ points. Entity API is simpler but won't scale. |
| CORS | Already configured `allow_origins=["*"]` in FastAPI. If serving frontend from same origin (static files), CORS isn't even needed for API calls. |
| Auto-refresh interval | 5 seconds is a good starting point. Each refresh = 1 API call returning 30 positions. At Phase 3 (6k sats), may need to reduce frequency or use WebSocket. |
| Cesium CDN version | Pin to a specific version (e.g., `1.115`) to avoid breaking changes |
| Browser compatibility | Cesium requires WebGL. Works in all modern browsers. |
| Intel UHD 620 | Integrated GPU — disable terrain during dev, use ellipsoid + low-res imagery, cap pixelRatio at 1. `PointPrimitiveCollection` keeps draw calls minimal. |

---

## Success Criteria (Definition of Done)

- [ ] 3D globe renders with Cesium Ion terrain/imagery
- [ ] ~30 satellite points visible at correct positions
- [ ] Points move in real-time (auto-refresh)
- [ ] Click satellite → info popup with name, altitude, speed, position
- [ ] Selected satellite shows ~90-minute orbit trail
- [ ] Served by FastAPI (`uvicorn backend.main:app`)
- [ ] No console errors, clean layout
- [ ] Ready for Week 5 polish (time controls, toggle groups)
