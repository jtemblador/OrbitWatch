# Task 5.2 — Time Controls (Play/Pause/Speed)

**Date:** Mar 31, 2026
**Status:** DONE
**Tests:** Skipped (frontend-only changes, no backend modifications)

---

## Goal

Let users control the passage of time on the globe — pause to inspect a moment, accelerate to watch satellites complete full orbits, or return to real-time. All API calls already support `?time=` parameters; this task wires a simulated clock through the frontend so every fetch uses simulated time instead of wall-clock UTC.

---

## Approach

### Simulated Clock Module

`clock.js` exposes a `simClock` IIFE with five methods (`getTime`, `getTimeMs`, `isPaused`, `togglePause`, `setSpeed`). Time is computed drift-free via anchor arithmetic: `baseSimTime + (Date.now() - baseWallTime) * speed`. Re-anchoring on every pause/resume/speed-change prevents accumulation errors.

### Speed-Adaptive Refresh Interval

At 1x, positions refresh every 5 seconds (5s simulated gap — small enough for accurate lerp). At 60x with a fixed 5s interval, each refresh would span 300 simulated seconds (~2,250 km of orbital arc), causing the satellite to lerp in a straight line across a curved orbit. The fix: `getRefreshInterval()` returns `max(5000 / speed, 500)`, scaling the interval inversely with speed so the simulated gap stays small. At 60x the interval is 500ms (30s sim gap, ~225 km — close enough for lerp to follow the arc).

The lerp denominator in `onPreRender()` uses `getRefreshInterval()` instead of the old constant, so interpolation reaches 1.0 just as the next fetch arrives.

Both `satellites.js` and `info-panel.js` use self-scheduling `setTimeout` loops (not `setInterval`) so the interval adapts in real time when the user changes speed.

### Orbit Trail Alignment at High Speed

The orbit trail is rendered from cached TEME positions with a single GMST rotation for a clean closed ring. At high speeds, Earth rotation causes the trail's ECEF projection to drift from the satellite's fresh API positions. A `preRender` listener re-rotates the cached TEME data every 500ms at speed > 1 — purely client-side (no API call), just ~3600 rotations + 2 primitive rebuilds.

Trade-off explored during development:
- **Per-point GMST** (each trail point rotated by its own timestamp): static trail, satellite follows it perfectly, but trail doesn't close (~23° gap for LEO). User preferred the clean ring.
- **Single GMST + client-side re-rotation** (chosen): clean closed ring, slight visible rotation at 60x but trail stays aligned with satellite. ICRF rendering (spinning Earth + inertial ring) noted as future enhancement to eliminate this.

### Time Bar UI

Bottom-center overlay matching the existing panel theme. Pause/play button, UTC time display (updated every 250ms), and three speed buttons (1x/10x/60x) with active-state highlighting. DOM is created programmatically in `clock.js` (no HTML template needed).

---

## What Was Built

| File | Action | Purpose |
|------|--------|---------|
| `frontend/js/clock.js` | CREATED | Simulated clock module (IIFE) + time bar UI |
| `frontend/js/satellites.js` | MODIFIED | `?time=` on position fetch, pause guards on fetch/lerp, adaptive refresh interval via `getRefreshInterval()` |
| `frontend/js/info-panel.js` | MODIFIED | `?time=` on panel/trail fetches, simulated time for GMST + trail start, adaptive panel refresh, cached TEME re-rotation at high speed |
| `frontend/css/style.css` | MODIFIED | Time bar styles (bottom-center, flex layout, speed button states) |
| `frontend/index.html` | MODIFIED | Added `clock.js` script tag (load order: app → clock → satellites → info-panel → controls) |

---

## Validation

- **1x baseline:** Behavior identical to pre-task — satellites move smoothly, trail aligned
- **Pause:** All motion stops, time display freezes, no API calls in Network tab
- **Resume:** Motion resumes from paused position, no time jump
- **10x speed:** Satellites visibly faster, trail stays aligned, adaptive refresh (500ms)
- **60x speed:** Satellites move rapidly, trail re-rotates to stay aligned, panel data updates
- **Speed switch mid-flight:** 1x→60x→10x→1x — no time jumps due to re-anchoring
- **Pause at high speed:** Everything freezes at current simulated position
- **Select satellite at high speed:** Info panel, orbit trail, nadir line all work correctly
- **Toggle labels while accelerated:** Display controls unaffected by time state

---

## Lessons Learned

1. **Linear lerp breaks on curved orbits when positions are far apart.** At 60x with a 5s refresh interval, the lerp spanned 300 simulated seconds of orbital arc (~2,250 km). The satellite visibly cut across the curve. Scaling the refresh interval inversely with speed (`max(5000/speed, 500)`) keeps the simulated gap small enough for accurate lerp.

2. **Static ECEF primitives drift from fresh API positions at high speed.** The orbit trail bakes in a single GMST angle at render time. As simulated time advances, Earth rotates but the trail doesn't — the satellite (getting fresh ECEF from the API) drifts off. Client-side re-rotation of cached TEME data every 500ms fixes this without extra API calls.

3. **Per-point GMST produces a physically correct but visually disconnected trail.** Earth rotates ~23° during one LEO orbit, so the trail endpoints don't meet. Users prefer the clean closed ring (single GMST) over the accurate-but-open arc. ICRF rendering (camera locked in inertial frame, Earth spins) would give both — deferred as future enhancement.

4. **`setInterval` can't adapt to speed changes; `setTimeout` loops can.** Replacing fixed `setInterval` with self-scheduling `setTimeout` lets the refresh interval change dynamically when the user switches speed.

5. **Re-anchoring on speed change prevents time jumps.** Before changing `speed`, save `getTimeMs()` as the new `baseSimTime` and `Date.now()` as `baseWallTime`. Without this, switching from 60x to 1x would cause a large forward time jump.

---

## Function Reference

### Frontend (`frontend/js/clock.js` — new file)

| Function/Config | Purpose |
|----------------|---------|
| `simClock.getTime()` | Current simulated time as ISO 8601 string (for API `?time=` param) |
| `simClock.getTimeMs()` | Current simulated time in Unix milliseconds (for GMST computation) |
| `simClock.isPaused()` | Returns `true` if clock is paused |
| `simClock.togglePause()` | Toggle play/pause — re-anchors `baseSimTime`/`baseWallTime` |
| `simClock.setSpeed(n)` | Set speed multiplier (1, 10, 60) — re-anchors to prevent time jump |
| `simClock.getSpeed()` | Returns current speed multiplier |

### Frontend (`frontend/js/satellites.js` — modifications)

| Function/Config | Purpose |
|----------------|---------|
| `BASE_REFRESH_MS` | Base refresh interval (5000ms), replaces old `REFRESH_INTERVAL_MS` |
| `MIN_REFRESH_MS` | Floor for adaptive interval (500ms) |
| `getRefreshInterval()` | Returns `max(BASE_REFRESH_MS / speed, MIN_REFRESH_MS)` — used by lerp + fetch scheduling |

### Frontend (`frontend/js/info-panel.js` — modifications)

| Function/Config | Purpose |
|----------------|---------|
| `computeGmst(simMs)` | IAU 1982 GMST from Unix ms — extracted as reusable helper |
| `cachedDenseTEME` | ~3600 densified TEME Cartesian3 cached for client-side re-rotation |
| `renderTrailFromCache()` | Re-applies current GMST to cached TEME and rebuilds trail primitives |
| `buildTrailPrimitives(positions)` | Creates near+far dual primitives from ECEF positions array |
| `getTrailRefreshInterval()` | Speed-scaled trail API refresh: `max(30000 / speed, 5000)` |

### Deferred

- **ICRF rendering (spinning Earth):** Would produce a perfectly static closed orbital ring with no re-rotation needed. Requires rendering all positions in TEME, syncing Cesium's clock, and handling camera transforms. Noted as future enhancement.
