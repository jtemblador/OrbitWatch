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

### 2. Time Controls (Play/Pause/Speed)

Let users scrub through time to watch satellites orbit, speed up to see full orbits, or pause to inspect a moment.

**What to build:**
- UI bar at the bottom of the viewport with: play/pause button, speed selector (1x, 10x, 60x), current UTC time display
- A simulated clock that advances at the selected speed multiplier
- All API calls use the simulated time (`?time=` parameter) instead of real UTC
- When paused, positions freeze; when playing at 1x, matches real-time (current behavior)

**Implementation notes:**
- Add a `clock.js` module managing simulated time state
- The existing `?time=` parameter on `/api/positions` and `/api/positions/{norad_id}/track` already supports this — no backend changes needed
- At 60x speed, 5s refresh interval means each fetch jumps 5 minutes of simulated time — satellite motion will be visible but discrete. Consider reducing refresh interval at high speeds or relying on lerp to smooth the jumps
- Orbit trail should re-fetch when time jumps significantly (>30s of simulated time per refresh)

**Success criteria:**
- [ ] Play/pause button stops and resumes satellite motion
- [ ] Speed buttons (1x/10x/60x) accelerate time progression
- [ ] Current simulated UTC time displayed and updating
- [ ] Orbit trail stays aligned at all speeds

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
├── satellites.js   — added applyVisibilityState() hook after position refresh
├── info-panel.js   — added nadir line (CallbackProperty Entity)
├── clock.js        — (Task 5.2, not yet started)
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
- [ ] Time controls: play/pause/speed work correctly
- [ ] Simulated time displayed in UI
- [x] Satellite visibility toggles work
- [x] All existing functionality still works (info panel, orbit trail, selection)
- [x] No console errors
- [x] 279+ tests passing (no backend changes expected for tasks 1 and 3)
