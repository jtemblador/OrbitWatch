# Week 5 — Globe Polish (Apr 24–30, 2026)

**Goal:** Add interactive polish to the 3D globe — nadir line for selected satellite, time controls for scrubbing/speeding through orbits, and toggle controls for satellite visibility. By the end of this week, the globe is a genuinely interactive tool, not just a viewer.

---

## What We Have (from Weeks 2–4)

| Component | What it does |
|-----------|-------------|
| Cesium.js globe | 3D Earth with CartoDB dark tiles, ~30 station satellites as red points + labels |
| Auto-refresh | Positions update every 5s with smooth interpolation (~20fps lerp) |
| Info panel | Click satellite → bottom-left panel with position + orbital params, auto-refresh |
| Orbit trail | Full-period ring at orbital altitude via TEME API + GMST rotation, dual-primitive (bright near / faint far), toggle checkbox |
| Selection indicator | Enlarged point + cyan outline ring on selected satellite |
| Track API | Returns TEME (inertial) + geodetic positions for orbit trail rendering |
| All endpoints | Support optional `?time=` parameter for arbitrary UTC timestamps |

---

## Main Tasks

### 1. Nadir Line (Altitude Stalk) for Selected Satellite ✅

Show a vertical line from the sub-satellite point on Earth's surface straight up to the satellite at orbital altitude. Visually communicates the satellite's altitude and ground position.

**What was built:**
- Cesium Entity polyline with `CallbackProperty` — tracks satellite's interpolated position every render frame (no 5s lag)
- Always on when selected (no toggle needed — user decision)
- Cyan color matching orbit trail theme, 1.5px width, 0.4 alpha
- Ground point projected via normalize-to-equatorial-radius (sufficient accuracy for visualization)

**Success criteria:**
- [x] Selecting a satellite shows a vertical cyan line from ground to satellite
- [x] Line updates as the satellite moves (every frame via CallbackProperty)
- [x] Line clears on deselection

---

### 2. Time Controls (Play/Pause/Speed) ✅

**What was built:**
- `clock.js` IIFE module with simulated clock: drift-free anchor arithmetic (`baseSimTime + wallElapsed × speed`), re-anchors on pause/resume/speed-change
- Bottom-center time bar UI: pause/play button, UTC time display (250ms tick), speed buttons (1x/10x/60x) with active highlight
- All API calls (`/api/positions`, `/api/positions/{id}`, `/api/positions/{id}/track`) pass `?time=` with simulated time
- Speed-adaptive refresh interval: `max(5000/speed, 500)` — at 60x, fetches every 500ms (30s sim gap) instead of 5s (300s sim gap), keeping lerp accurate on curved orbits
- Self-scheduling `setTimeout` loops replace `setInterval` for real-time interval adaptation
- Orbit trail: cached TEME + client-side re-rotation every 500ms at high speed (no API call)
- Pause freezes all motion, API calls, lerp interpolation, and panel refresh

**Success criteria:**
- [x] Play/pause button stops and resumes satellite motion
- [x] Speed buttons (1x/10x/60x) accelerate time progression
- [x] Current simulated UTC time displayed and updating
- [x] Orbit trail stays aligned at all speeds

---

### 3. Toggle Satellite Visibility ✅

Let users show/hide satellites by category. Phase 1 has only "stations" (~30 objects), but the UI should support future groups (Phase 2: visual sats, Phase 3: Starlink).

**What was built:**
- Top-right "Display" panel with dark theme matching info panel
- "Labels" toggle hides/shows all satellite name labels
- Type filter checkboxes gated by data — only shown when meaningful (non-"UNKNOWN") types exist
- Phase 1 stations are all "UNKNOWN" → no type filters shown yet (Phase 2 TODO in roadmap)
- `applyVisibilityState()` called after each position refresh to maintain toggle state
- Hiding a selected satellite auto-deselects it (closes info panel)

**Success criteria:**
- [x] Label toggle hides/shows all satellite name labels
- [x] Type filter toggles ready (gated until Phase 2 when meaningful types exist)
- [x] Toggles persist across position refreshes

---

## File Structure (Actual Changes)

```
frontend/js/
├── app.js          — no changes
├── clock.js        — NEW: simulated clock module (IIFE) + time bar UI
├── satellites.js   — ?time= on fetches, pause guards, adaptive refresh interval
├── info-panel.js   — nadir line, ?time= on fetches, simulated GMST, cached TEME re-rotation
└── controls.js     — NEW: toggle panel UI + satellite filtering
```

---

## Implementation Order

1. **Nadir line** — simplest, builds on existing selection logic
2. **Time controls** — core interactive feature, no backend changes
3. **Toggle groups** — UI polish, works with existing metadata cache

---

## Things to Watch

| Concern | Detail |
|---------|--------|
| Time control + orbit trail | Trail must re-render when simulated time changes significantly — the trail is centered on "now" which changes at accelerated speeds |
| Lerp at high speed | At 60x, satellites jump ~300 km between 5s refreshes. Lerp smooths this but may look jerky. May need shorter refresh interval at high speeds |
| Nadir line depth | Line should be visible through the globe (depth test OFF) or only on the near side — match the orbit trail's dual-primitive approach if needed |
| Toggle state vs selection | If the selected satellite is hidden via toggle, should the info panel close? Yes — deselect on hide |
| Phase 2 readiness | Toggle UI should accommodate ~10 groups without redesign |

---

## Success Criteria (Definition of Done)

- [x] Nadir line visible from ground to selected satellite
- [x] Time controls: play/pause/speed work correctly
- [x] Simulated time displayed in UI
- [x] Satellite visibility toggles work
- [x] All existing functionality still works (info panel, orbit trail, selection)
- [x] No console errors
- [x] 279+ tests passing (no backend changes — frontend only)
