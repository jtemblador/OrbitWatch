# Task 4.1 — Cesium.js Globe Setup

**Date:** Mar 25, 2026
**Status:** DONE
**Tests:** 82/82 API tests passing + 6 integration checks for static file serving

---

## Goal

Get a Cesium.js 3D globe rendering in the browser, served by FastAPI as static files. This is the frontend foundation — all satellite rendering, click interaction, and orbit trails build on top of this viewer.

---

## Approach

### Pre-Task Research

Surveyed 9 satellite visualization sites to validate the Cesium.js choice:

- **Custom WebGL sites** (satellitemap.space, keeptrack.space, stuffin.space) — best raw performance but require building Earth rendering, coordinate systems, and picking from scratch. 4+ weeks of frontend work.
- **Three.js sites** (satellitetracker3d.com, 3dsatellitetracker.com) — good performance via `THREE.Points` single draw call + Web Workers for SGP4. Still requires custom Earth/coordinate handling. 2-3 extra weeks.
- **Cesium.js sites** (AstriaGraph, trackthesky.com, agsattrack.com) — AstriaGraph is laggy (Entity API + per-frame Kepler solving), but trackthesky.com runs 9K+ satellites smoothly using proper `PointPrimitiveCollection`. Cesium gives globe, WGS84 coordinates, camera controls, and picking for free.

**Decision:** Stick with Cesium.js. Development speed advantage is decisive with 5 weeks remaining. The key is using `PointPrimitiveCollection` (not Entity API) for satellite rendering.

### UHD 620 Performance Mitigations

Dev machine has Intel UHD 620 (integrated GPU). Applied:
- Terrain disabled (ellipsoid only) — biggest GPU saver
- `resolutionScale = 1.0` — prevents HiDPI rendering
- Stripped all default UI widgets (timeline, animation, geocoder, etc.)
- Will use `PointPrimitiveCollection` for satellites (single draw call)

### Token Security

Standard pattern: `config.js` (real token, gitignored) + `config.example.js` (template, committed). Guard in `app.js` shows helpful error if token is missing.

### Static File Serving

`StaticFiles` mounted at `/` AFTER API routes so `/api/*` resolves first. `html=True` makes `/` serve `index.html`.

---

## What Was Built

| File | Action | Purpose |
|------|--------|---------|
| `frontend/index.html` | CREATED | Main page — loads Cesium 1.139.1 via CDN, config, app.js |
| `frontend/css/style.css` | CREATED | Full-viewport reset (no margins, no scrollbars) |
| `frontend/js/config.js` | CREATED | Cesium Ion token (gitignored) |
| `frontend/js/config.example.js` | CREATED | Token template for repo cloners |
| `frontend/js/app.js` | CREATED | Cesium.Viewer init, token guard, UHD 620 optimizations |
| `backend/main.py` | MODIFIED | Added StaticFiles mount for frontend/ |
| `.gitignore` | MODIFIED | Added `frontend/js/config.js` |
| `tests/test_api.py` | MODIFIED | Updated `test_refresh_get_not_allowed` (405→404 with catch-all mount) |

---

## Validation

- `GET /` returns 200 with index.html content
- `GET /css/style.css`, `/js/app.js`, `/js/config.example.js` all accessible
- `GET /api/health` and `/api/satellites` still resolve before static mount
- Globe renders in browser (manual verification)
- 82/82 existing API tests passing

---

## Lessons Learned

1. **StaticFiles catch-all changes 405→404:** When mounted at `/` with `html=True`, requests for undefined routes (like `GET /api/refresh` on a POST-only endpoint) return 404 from the static mount instead of 405 from FastAPI. Both correctly reject the request — tests updated to accept either.

2. **Cesium credit attribution required:** Cesium Ion ToS requires visible attribution for free accounts. Don't hide it with `display: none` — minimize font size instead.

3. **AstriaGraph is a cautionary tale:** Entity + CallbackProperty pattern (per-frame JS Kepler solving for 17K objects) is the wrong Cesium API for bulk visualization. `PointPrimitiveCollection` is the correct choice — GPU-batched single draw call.

---

## Function Reference

### Frontend

| Function/Config | File | Purpose |
|----------------|------|---------|
| `CESIUM_ION_TOKEN` | `config.js` | Cesium Ion access token (gitignored) |
| `viewer` | `app.js` | `Cesium.Viewer` instance — terrain off, UI stripped, pixel ratio 1x |
| Token guard | `app.js` | Shows setup instructions if config.js missing/placeholder |

### Backend

| Change | File | Purpose |
|--------|------|---------|
| `StaticFiles` mount | `main.py:51` | Serves `frontend/` at `/`, after API routes |
