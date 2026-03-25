# Task 4.2 — Satellite Points on Globe

**Date:** Mar 25, 2026
**Status:** DONE
**Tests:** 279/279 passing (82 API + 197 backend/engine), no regressions

---

## Goal

Fetch all ~30 satellite positions from the backend API and render them as colored points on the Cesium globe with name labels. Points auto-refresh every 5 seconds with smooth interpolation between positions so satellites visibly glide rather than snap.

---

## Approach

### Rendering Strategy

Used `PointPrimitiveCollection` + `LabelCollection` — both GPU-batched single draw call per collection. This is the scalable pattern (trackthesky.com runs 9K+ sats this way). Avoided Entity API which chokes at scale (AstriaGraph lesson from pre-week research).

### Smooth Interpolation

API refreshes every 5 seconds. Between refreshes, `preRender` callback lerps between the last-known and newly-fetched positions. Throttled to ~20fps (`LERP_FRAME_MS = 50`) to save CPU at Phase 3 scale (6K sats × 20fps = 120K lerps/sec — trivial math, but avoids unnecessary GPU position uploads at 60fps).

### Dark Tile Base Map

Switched from OSM default (white landmasses, too bright) to CartoDB dark tiles via `UrlTemplateImageryProvider`. Dark background makes red satellite points pop visually. Country borders and labels still visible.

### Label Rendering

Cesium's `FILL_AND_OUTLINE` label style produces rendering artifacts (distorted text at oblique angles). Switched to `FILL` only with translucent dark background for contrast. Labels fade out at distance via `translucencyByDistance` instead of scaling down (which made them small and still distorted).

---

## What Was Built

| File | Action | Purpose |
|------|--------|---------|
| `frontend/js/satellites.js` | CREATED | Fetch positions, render points + labels, interpolation loop |
| `frontend/js/app.js` | MODIFIED | Switched to CartoDB dark tiles |
| `frontend/index.html` | MODIFIED | Added satellites.js script tag |

No backend changes — API already returns everything needed.

---

## Validation

- ~30 red dots visible at correct orbital positions (user confirmed)
- Points interpolate smoothly between 5-second refreshes
- ISS identifiable by name label at ~420 km altitude
- Labels properly occluded when satellite is behind Earth (depth testing)
- Labels fade at distance to avoid distortion
- 279/279 tests passing (no regressions)
- No console errors

---

## Lessons Learned

1. **Cesium `Cartesian3.fromDegrees` height is in meters.** API returns `alt_km` — must multiply by 1000. Verified against official Cesium docs.

2. **Cesium label `FILL_AND_OUTLINE` produces rendering artifacts.** Text outline rasterization on label textures causes distortion at oblique angles and varying distances. Use `FILL` style with `showBackground: true` instead.

3. **`disableDepthTestDistance: Number.POSITIVE_INFINITY` defeats globe occlusion.** Labels behind Earth remain visible. Removing it restores proper depth testing.

4. **CartoDB dark tiles work well as a satellite tracker base map.** `https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png` via `UrlTemplateImageryProvider`. Shows country borders + labels on dark background. No API key needed. CC BY 3.0 credit required.

5. **Cesium's `position` setter copies the value.** A single scratch `Cartesian3` can be reused across all satellites in the lerp loop without corruption — Cesium copies on assignment, doesn't store the reference.

---

## Function Reference

### Frontend (`frontend/js/satellites.js`)

| Function/Config | Purpose |
|----------------|---------|
| `REFRESH_INTERVAL_MS` | Position fetch interval (5000ms) |
| `LERP_FRAME_MS` | Interpolation throttle (50ms = ~20fps) |
| `fetchPositions()` | Fetches `/api/positions`, returns positions array or null |
| `toCartesian(pos)` | Converts API position (`lon, lat, alt_km`) to Cesium `Cartesian3` (meters) |
| `updatePositions(positions)` | Creates or updates point + label primitives, sets interpolation targets |
| `onPreRender()` | Throttled preRender callback — lerps all satellite positions each frame |
| `refreshSatellites()` | Fetch + update cycle with in-flight guard |
| `satellites` | `Map<norad_id, {point, label, start, target}>` — per-satellite state |

### Frontend (`frontend/js/app.js`)

| Change | Purpose |
|--------|---------|
| `UrlTemplateImageryProvider` (CartoDB dark) | Dark base map with country borders |
