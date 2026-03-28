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

---

## Task 4.2 — Satellite Points on Globe

### Key Decisions

1. **`PointPrimitiveCollection` + `LabelCollection`** — both GPU-batched, single draw call each. Correct scalable pattern (validated by trackthesky.com at 9K sats).

2. **Smooth interpolation at ~20fps** — `preRender` callback lerps between last-known and newly-fetched positions. Throttled to 50ms frames (`LERP_FRAME_MS`) to save CPU at Phase 3 scale. Configurable — bump to 60fps later if needed.

3. **CartoDB dark tiles** — switched from OSM default (too bright) to `dark_all` tiles via `UrlTemplateImageryProvider`. Dark background makes red satellite points pop. Country borders + labels still visible.

4. **Label style: FILL only, not FILL_AND_OUTLINE** — Cesium's text outline rasterization produces rendering artifacts (distorted text at oblique angles and varying distances). Using FILL with `showBackground: true` (translucent dark) provides contrast without distortion. Some labels still illegible at extreme angles — inherent Cesium limitation, acceptable for Phase 1.

5. **`translucencyByDistance` instead of `scaleByDistance`** — scaling labels down at distance made them small AND distorted. Fading them out entirely is cleaner.

6. **`alt_km * 1000` conversion** — Cesium's `Cartesian3.fromDegrees` expects height in meters. Verified against official docs.

7. **Scratch Cartesian3 reuse** — single `scratchCartesian` object reused across all satellites in the lerp loop. Safe because Cesium's position setter copies the value, doesn't store the reference.

---

## Tasks 4.3+4.4 — Info Panel + Orbit Trail

### Key Decisions

1. **Bottom-left fixed panel** — user preference (not top-right). Dark background with cyan (#4fc3f7) accent to match orbit trail color. Monospace font, vertical key-value table.

2. **Satellite metadata cached at startup** — added `satelliteMetadata` Map in `satellites.js`. Fetches `/api/satellites` once and caches orbital params (period, inclination, apoapsis, etc.). Info panel combines cached orbital data with live position data from `/api/positions/{norad_id}`. Avoids a second API call per click.

3. **Auto-refresh every 5 seconds** — reuses `REFRESH_INTERVAL_MS` from satellites.js. Only refreshes position data (cached orbital params don't change).

4. **Solid color orbit trail, not gradient** — initially implemented Cesium's `Fade` material for a gradient effect. The `Fade` material uses `materialInput.st` where `s` = 0→1 along polyline length. Worked for the visible portion but made the trail behind the satellite fully transparent. Switched to solid `Color` material — simpler and fully visible.

4b. **Past + future trail window** — initially fetched trail forward-only from "now", causing the satellite to drift ahead of the trail's starting point. Fixed by fetching from `now - 45min` to `now + 45min` so the satellite sits mid-trail. Trail re-fetched every 30 seconds to stay aligned.

5. **`PolylineFade` does NOT exist in Cesium 1.139.1** — research confirmed this is not a built-in material type. The generic `Fade` material works on `PolylineCollection` but has the transparency limitation described above.

6. **Race condition guard on trail fetch** — `if (selectedNoradId !== noradId) return` after the async track fetch prevents stale trail rendering if the user clicks a different satellite while the fetch is in-flight.

7. **Script load order: app → satellites → info-panel** — `info-panel.js` depends on globals from both prior scripts (`viewer`, `satellites`, `satelliteMetadata`, `REFRESH_INTERVAL_MS`).

8. **`PolylineCollection` cannot render orbital paths around the globe** — draws straight Cartesian chords between points with no `arcType` support. Chords sag below the actual arc on the far side of the globe, causing visible gaps. Switched to Entity polyline with `clampToGround: true` and `arcType: GEODESIC`. Entity overhead is negligible for a single trail.

9. **Ground track projection (surface), not orbital altitude** — rendering the trail at 385+ km altitude causes a perspective "lifting" effect near the globe's limb. The altitude gap is viewed edge-on, making the trail appear to peel away from the globe asymmetrically. This is physically correct (not a data bug) but visually confusing. Industry standard (satvis, trackthesky) is to project onto the surface. Satellite dot remains at real altitude.

10. **Geodetic altitude varies ~18-19 km per orbit for nearly circular LEO** — caused by orbital eccentricity + WGS-84 ellipsoid shape (Earth is ~21 km flatter at the poles). Verified by checking orbital radius at each track point — varies smoothly as expected.

11. **Data pipeline verified correct** — cross-checked ISS position against python-sgp4 reference (sub-millimeter match) and wheretheiss.at public API (speed: 7.657 vs 7.658 km/s). GMST matches Meeus formula exactly.

12. **Selection indicator via PointPrimitive outline** — selected satellite gets enlarged point (6→10px) with 3px cyan `outlineWidth`. Simpler than a separate Entity that would need to track position at orbital altitude.

---

## Week 4.5 — Orbit Trail Fix (Surface → Altitude Ring)

### Problem

The original orbit trail was a ground track (Entity polyline with `clampToGround: true`). Switched to rendering at orbital altitude using `PolylineGeometry` Primitives, but the trails appeared to "bend" — diagonal lines sagging across the globe face instead of forming clean tilted rings.

### Root Cause: Earth Rotation in ECEF

The track API returns positions in ECEF (geodetic lat/lon/alt). During one 93-minute LEO orbit, Earth rotates ~23° in longitude. This warps the orbital ellipse into a helix in ECEF coordinates, causing visible bending. The bending is physically correct (it IS the satellite's ground track at altitude), but it doesn't look like the clean orbital ring users expect.

### Fix: De-Rotate ECEF Positions

Rotate each ECEF position around the Z-axis by `dt × ω_earth` (where dt = time offset from "now") to collapse the helix into the satellite's instantaneous orbital plane. This is a simple Z-rotation — ECEF and ECI share the same Z-axis, so undoing Earth's spin recovers the inertial orbital geometry.

### Additional Fixes Applied

1. **Dual-primitive rendering** — two Primitives with the same orbit path: near-side (depth test ON, 0.8 alpha, 2.5px) and far-side (depth test OFF, 0.2 alpha, 1.5px). Shows the full ring while distinguishing front from back.

2. **Client-side densification** — 360 API points densified 10x to ~3600 points via lerp + normalize-to-radius (approximate SLERP). Each chord is ~12 km with <1 m sag. Eliminates straight-line artifacts from `arcType: NONE`.

3. **Dynamic orbital period** — trail duration matches the satellite's actual period from metadata (not hardcoded 90 min). Works for LEO through GEO.

### Iteration History

| Attempt | Approach | Result |
|---------|----------|--------|
| v1 | Entity polyline, `clampToGround: true` | ✅ Worked but surface-only |
| v2 | PolylineGeometry Primitive at altitude, depth test OFF, 360 pts | Chord artifacts (straight lines cutting through globe) |
| v3 | Same but 2000 pts | 422 error (API limit is 500 steps) |
| v4 | PathGraphics + SampledPositionProperty | Trail auto-occluded behind globe |
| v5 | Primitive + client-side densification (360 × 6 = 2160 pts) | Bending — Earth rotation not accounted for |
| v6 | Dual primitives (near bright + far faint), densify × 10 | Bending persisted |
| v7 | **De-rotate ECEF + dual primitives + densify × 10** | ✅ Clean orbital ring |
