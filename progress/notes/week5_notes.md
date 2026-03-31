# Week 5 Notes — Globe Polish

---

## Task 5.1 — Nadir Line (Altitude Stalk)

### Key Decisions

1. **CallbackProperty for per-frame tracking** — initial implementation updated the nadir line on the 5-second API refresh cycle, causing visible lag as the satellite moved ahead of the line. Switched to `CallbackProperty` which evaluates every render frame, reading the satellite's current interpolated position directly from the `PointPrimitive`. Zero additional API calls.

2. **Always on, no toggle** — user feedback: a toggle adds clutter for something that should always be visible when a satellite is selected. Removed the nadir checkbox, `nadirVisible` state, and related event listener.

3. **Ground point via equatorial radius** — the nadir ground point is computed by normalizing the satellite's Cartesian3 to `Cesium.Ellipsoid.WGS84.maximumRadius` (6378.137 km). This is the equatorial radius, not the true WGS-84 surface — means the ground end is ~7 km above the real surface at the poles. Imperceptible at globe scale; true geodetic projection would require per-frame computation for negligible visual benefit.

4. **Entity polyline, not Primitive** — for a single line, Entity API is fine (no scale concern). Uses `arcType: NONE` for a straight 3D line from surface to altitude.

---

## Task 5.3 — Display Controls Panel

### Key Decisions

1. **Data-driven type filter gating** — rather than hardcoding "no type filters in Phase 1," the code checks if meaningful (non-"UNKNOWN") types exist in `satelliteMetadata`. Phase 1 stations are all "UNKNOWN" → no type checkboxes appear. Phase 2 data with PAYLOAD/ROCKET BODY/DEBRIS will automatically enable the filters with no code changes.

2. **`applyVisibilityState()` as a global hook** — called from `satellites.js` after every position refresh to maintain toggle state across updates. Uses `typeof` guard (`if (typeof applyVisibilityState === "function")`) since `controls.js` loads after `satellites.js`.

3. **Auto-deselect on hide** — if the user toggles off a type filter that hides the currently selected satellite, the info panel and orbit trail auto-close. Prevents orphaned UI state.

4. **`initControls()` waits for metadata** — uses `setTimeout` retry (500ms) until `satelliteMetadata.size > 0`. This handles the startup race where `controls.js` executes before the metadata fetch completes.

5. **Script load order: app → satellites → info-panel → controls** — `controls.js` depends on `satellites`, `satelliteMetadata`, `selectedNoradId`, and `deselectSatellite()` from prior scripts.

### Deferred to Phase 2

- Type filter checkboxes (code ready in `controls.js`, gated by data)
- Phase 2 TODO noted in `progress/roadmap.md`
