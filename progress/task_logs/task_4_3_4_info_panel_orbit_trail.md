# Task 4.3+4.4 — Satellite Info Panel + Orbit Trail

**Date:** Mar 25, 2026
**Status:** DONE
**Tests:** Skipped (frontend-only changes, no backend modifications)

---

## Goal

Click a satellite point on the globe → bottom-left info panel with metadata (position + orbital params) and live auto-refresh. Simultaneously render a 90-minute orbit trail as a solid color polyline with a toggle checkbox to show/hide.

---

## Approach

### Combined Tasks

Tasks 4.3 (click interaction / info panel) and 4.4 (orbit trail) were built together since the orbit trail is triggered by satellite selection and rendered in the same `info-panel.js` module.

### Info Panel Design

- **Position:** Fixed bottom-left overlay (user preference, not the typical top-right)
- **Layout:** Vertical key-value table with all available fields — position data from `/api/positions/{norad_id}` (live) + orbital params from `/api/satellites` (cached at startup)
- **Theme:** Dark background (`rgba(10, 10, 20, 0.9)`) with cyan accent (`#4fc3f7`) matching the orbit trail
- **Auto-refresh:** Position data refreshes every 5 seconds (reuses `REFRESH_INTERVAL_MS` from satellites.js)

### Metadata Caching

Added a `satelliteMetadata` Map in `satellites.js` — fetches `/api/satellites` once at startup and caches orbital params (period, inclination, apoapsis, periapsis, object_type). The info panel combines this cached data with live position data, avoiding a second API call per click.

### Orbit Trail

- **Approach (final):** Fetch 360 track points, de-rotate ECEF positions to remove Earth rotation (~23°/orbit), densify 10x to ~3600 points, render as TWO PolylineGeometry Primitives at orbital altitude (near-side bright + far-side faint ghost).
- **Earth rotation fix:** Track API returns ECEF positions where Earth rotates under the satellite. This warps the orbital ellipse into a helix, causing visible "bending." Fix: rotate each ECEF position around the Z-axis by `dt × 7.2921159e-5 rad/s` to collapse back to the instantaneous orbital plane.
- **Dual-primitive rendering:** Near-side (depth test ON, 0.8 alpha, 2.5px) + far-side (depth test OFF, 0.2 alpha, 1.5px). Full ring visible with clear front/back distinction.
- **Client-side densification:** lerp + normalize-to-radius, 360 → ~3600 points, each chord ~12 km (<1 m sag).
- **Color:** Solid cyan (`rgba(0.31, 0.76, 0.97)`).
- **Past + future window:** Trail centered on "now" using satellite's actual orbital period from metadata. Originally tried forward-only (disconnected), then hardcoded 90 min (incorrect for non-LEO).
- **Trail refresh:** Re-fetched every 30 seconds to stay aligned.
- **Toggle:** Checkbox controls `show` on both primitives.
- **Race condition guard:** `if (selectedNoradId !== noradId) return` after async fetch prevents stale rendering.

### Selection Indicator

- Selected satellite point enlarges (6px → 10px) and gets a cyan outline ring (3px `outlineWidth`)
- Deselecting restores original point style
- Uses `PointPrimitive.outlineColor`/`outlineWidth` properties directly — no extra entity needed

---

## What Was Built

| File | Action | Purpose |
|------|--------|---------|
| `frontend/js/info-panel.js` | CREATED | Click handler, info panel DOM, orbit trail rendering, auto-refresh |
| `frontend/js/satellites.js` | MODIFIED | Added `satelliteMetadata` Map + `fetchSatelliteMetadata()` for cached orbital params |
| `frontend/css/style.css` | MODIFIED | Added info panel styles (bottom-left overlay, dark theme, cyan accent) |
| `frontend/index.html` | MODIFIED | Added `info-panel.js` script tag (load order: app → satellites → info-panel) |

No backend changes — all API endpoints already existed from Week 3.

---

## Validation

- Clicking a satellite point shows info panel with correct name, position, and orbital params
- Clicking elsewhere or the × button dismisses the panel
- Panel data auto-refreshes every 5 seconds while a satellite is selected
- Orbit trail renders as a solid cyan ground track along the 90-minute orbit path
- Trail is continuous all the way around the globe (no gaps on the far side)
- Trail checkbox toggles visibility
- Selecting a different satellite replaces both panel data and trail
- ISS trail wraps around globe at ~51.6° inclination as expected
- Race condition guard works — rapid click-switching doesn't leave stale trails
- Selected satellite has visible cyan outline ring for identification while rotating globe
- Cross-checked ISS position against python-sgp4 reference (sub-millimeter match) and wheretheiss.at API (speed matches to 3 decimal places)
- No console errors

---

## Lessons Learned

1. **Cesium `PolylineFade` material does NOT exist in 1.139.1.** The generic `Fade` material works on `PolylineCollection` via `materialInput.st` (where `s` = 0→1 along polyline length), but the fade makes the trail portion behind the satellite invisible. For orbit trails, a solid `Color` material is simpler and more usable.

2. **Forward-only trails disconnect from the satellite.** Fetching the trail from "now" forward means the satellite moves ahead of the trail's starting point within seconds. Fix: fetch from `now - 45min` so the satellite sits mid-trail with past and future path visible. Combined with a 30-second re-fetch, the trail stays aligned.

3. **`PolylineCollection` cannot render orbit trails correctly around the globe.** It draws straight Cartesian chords between points with no `arcType` support. These chords sag below the actual orbital arc on the far side of the globe, causing visible gaps and disconnections. The Cesium community confirms this limitation ([cesium.com/t/20570](https://community.cesium.com/t/how-to-set-arctype-of-polyline-in-polylinecollection/20570)). Fix: use Entity polyline with `arcType` and `clampToGround`.

4. **Render orbit trails as ground tracks (surface projection), not at orbital altitude.** Rendering at altitude causes a perspective "lifting" effect near the globe's limb — the 385+ km gap between trail and surface becomes visible edge-on, making the trail appear to peel away from the globe asymmetrically. This is physically correct (not a data bug) but visually confusing. Industry standard (satvis, trackthesky, etc.) is to project the trail onto the surface while keeping the satellite dot at real altitude.

5. **Geodetic altitude varies ~18-19 km over one orbit for nearly circular LEO satellites.** This is the combined effect of orbital eccentricity (~7-14 km) and the WGS-84 ellipsoid shape (~12 km, since Earth is flatter at the poles). Verified by computing orbital radius at each track point — radius varies smoothly as expected. The geodetic altitude variation amplifies the visual lifting effect, which is another reason surface projection is the right approach.

6. **Our SGP4 + coordinate transform pipeline is verified correct.** Cross-checked against python-sgp4 reference implementation at the same time — position delta is sub-millimeter (0.00001°). GMST matches independent Meeus formula to 0.00 arcseconds. ISS speed matches wheretheiss.at public API to 3 decimal places (7.657 vs 7.658 km/s).

7. **`satelliteMetadata` cache avoids per-click API overhead.** Orbital params (period, inclination, etc.) change slowly — fetching once at startup is sufficient. Position data still refreshes live.

8. **Script load order matters.** `info-panel.js` depends on `viewer`, `satellites`, `satelliteMetadata`, and `REFRESH_INTERVAL_MS` — all defined in `app.js` and `satellites.js`. Must load in order: app → satellites → info-panel.

9. **ECEF orbit trails bend because Earth rotates ~23° per orbit.** Track API returns geodetic positions in ECEF, where the satellite's path is a helix, not an ellipse. Must de-rotate each position around the Z-axis by `dt × ω_earth` to recover the clean inertial orbital plane. This is the same GMST Z-rotation used in the backend coordinate transforms — but applied in reverse on the frontend.

10. **Dual-primitive approach for orbit ring visibility.** A single primitive with depth test OFF shows the full ring but both arcs overlap at the same brightness, creating confusing X-patterns. Two primitives (near-side bright + far-side faint) make the ring structure legible from any camera angle.

11. **Client-side densification needed for `arcType: NONE`.** Cesium draws straight Cartesian 3D chords between sample points. At 360 points per orbit, each chord is ~120 km — visible as straight lines cutting through the globe. Densifying 10x to ~3600 points (each chord ~12 km, <1 m sag) makes them imperceptible.

---

## Function Reference

### Frontend (`frontend/js/info-panel.js`)

| Function/Config | Purpose |
|----------------|---------|
| `selectedNoradId` | Currently selected satellite NORAD ID (null = none) |
| `trailVisible` | Orbit trail visibility state |
| `trailPrimitives` | Array of two Primitives (far-side faint + near-side bright) for orbit ring |
| `densifyPositions(positions, factor)` | Client-side spherical interpolation — lerp + normalize-to-radius between each pair |
| `selectionIndicator` | NORAD ID of currently highlighted satellite (for style restore) |
| `SELECTED_STYLE` / `DEFAULT_STYLE` | Point style configs for selection highlight |
| `selectSatellite(noradId)` | Opens panel, highlights point, fetches position data, renders orbit trail |
| `deselectSatellite()` | Hides panel, clears trail, restores point style |
| `updateSelectionIndicator(noradId)` | Enlarges point + adds cyan outline ring |
| `clearSelectionIndicator()` | Restores original point style |
| `refreshPanelData(noradId)` | Fetches `/api/positions/{noradId}`, updates table with position + metadata |
| `fetchAndRenderTrail(noradId)` | Fetches 360-point track, renders ground track Entity polyline |
| `clearTrail()` | Removes active Entity polyline |

### Frontend (`frontend/js/satellites.js` — additions)

| Function/Config | Purpose |
|----------------|---------|
| `satelliteMetadata` | `Map<norad_id, {object_type, epoch, period_min, inclination_deg, apoapsis_km, periapsis_km}>` |
| `fetchSatelliteMetadata()` | One-time fetch of `/api/satellites` at startup, populates metadata cache |
