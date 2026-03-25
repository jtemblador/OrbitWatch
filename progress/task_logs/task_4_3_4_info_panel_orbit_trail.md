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

- **Approach:** Fetch 120-point ground track from `/api/positions/{norad_id}/track?duration_min=90&steps=120`, render as `PolylineCollection` polyline (GPU-batched, consistent with Task 4.2 primitive-based approach)
- **Color:** Solid cyan (`#4fc3f7`, 80% opacity) — initially tried Cesium's `Fade` material for a gradient trail but it made the portion behind the satellite invisible. Solid color is simpler and fully visible.
- **Past + future window:** Trail fetched from `now - 45min` to `now + 45min` so the satellite sits mid-trail. Originally fetched forward-only from "now", which caused the trail to disconnect as the satellite moved ahead of the trail's origin.
- **Trail refresh:** Re-fetched every 30 seconds to keep the trail aligned with the satellite's moving position.
- **Toggle:** Checkbox in the info panel controls `show` property on the active polyline
- **Race condition guard:** `if (selectedNoradId !== noradId) return` after async trail fetch prevents stale trail rendering if selection changes mid-fetch

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
- Orbit trail renders as a solid cyan polyline along the 90-minute ground track
- Trail checkbox toggles visibility
- Selecting a different satellite replaces both panel data and trail
- ISS trail wraps around globe at ~51.6° inclination as expected
- Race condition guard works — rapid click-switching doesn't leave stale trails
- No console errors

---

## Lessons Learned

1. **Cesium `PolylineFade` material does NOT exist in 1.139.1.** The generic `Fade` material works on `PolylineCollection` via `materialInput.st` (where `s` = 0→1 along polyline length), but the fade makes the trail portion behind the satellite invisible. For orbit trails, a solid `Color` material is simpler and more usable.

2. **Forward-only trails disconnect from the satellite.** Fetching the trail from "now" forward means the satellite moves ahead of the trail's starting point within seconds. Fix: fetch from `now - 45min` so the satellite sits mid-trail with past and future path visible. Combined with a 30-second re-fetch, the trail stays aligned.

3. **`satelliteMetadata` cache avoids per-click API overhead.** Orbital params (period, inclination, etc.) change slowly — fetching once at startup is sufficient. Position data still refreshes live.

4. **Script load order matters.** `info-panel.js` depends on `viewer`, `satellites`, `satelliteMetadata`, and `REFRESH_INTERVAL_MS` — all defined in `app.js` and `satellites.js`. Must load in order: app → satellites → info-panel.

---

## Function Reference

### Frontend (`frontend/js/info-panel.js`)

| Function/Config | Purpose |
|----------------|---------|
| `selectedNoradId` | Currently selected satellite NORAD ID (null = none) |
| `trailVisible` | Orbit trail visibility state |
| `trailCollection` | `PolylineCollection` for GPU-batched trail rendering |
| `selectSatellite(noradId)` | Opens panel, fetches position data, renders orbit trail |
| `deselectSatellite()` | Hides panel, clears trail |
| `refreshPanelData(noradId)` | Fetches `/api/positions/{noradId}`, updates table with position + metadata |
| `fetchAndRenderTrail(noradId)` | Fetches 120-point track, renders solid cyan polyline |
| `clearTrail()` | Removes active polyline from collection |

### Frontend (`frontend/js/satellites.js` — additions)

| Function/Config | Purpose |
|----------------|---------|
| `satelliteMetadata` | `Map<norad_id, {object_type, epoch, period_min, inclination_deg, apoapsis_km, periapsis_km}>` |
| `fetchSatelliteMetadata()` | One-time fetch of `/api/satellites` at startup, populates metadata cache |
