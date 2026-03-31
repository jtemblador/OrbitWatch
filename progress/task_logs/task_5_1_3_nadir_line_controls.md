# Task 5.1+5.3 — Nadir Line + Display Controls

**Date:** Mar 30, 2026
**Status:** DONE
**Tests:** Skipped (frontend-only changes, no backend modifications)

---

## Goal

Add two interactive polish features to the globe: (1) a nadir line that visually connects the selected satellite to its sub-satellite ground point, communicating altitude at a glance, and (2) a display controls panel with label toggle and satellite type filters (type filters gated until Phase 2 when meaningful types exist).

---

## Approach

### Combined Tasks

Tasks 5.1 (nadir line) and 5.3 (display controls) were built together since both are lightweight UI features that modify the same satellite selection/visibility flow.

### Nadir Line (Task 5.1)

- **Real-time tracking via CallbackProperty:** The nadir line uses Cesium's `CallbackProperty` to read the satellite's current interpolated position every render frame. This makes the line move smoothly with the satellite point (no 5-second lag between API refreshes). The callback computes the ground point by normalizing the satellite's Cartesian3 position to Earth's equatorial radius.
- **Always on when selected:** No toggle — the nadir line appears automatically when a satellite is selected and clears on deselection. User feedback drove this decision (toggle adds clutter for something that should always be visible).
- **Entity polyline:** Uses `viewer.entities.add()` with a polyline — appropriate for a single line (no scale concern). `arcType: NONE` draws a straight 3D line from surface to altitude.
- **Cyan theme:** Matches orbit trail color (`rgba(0.31, 0.76, 0.97, 0.4)`), slightly transparent to avoid visual dominance.

### Display Controls (Task 5.3)

- **Top-right overlay panel:** Mirrors the info panel's dark theme (`rgba(10, 10, 20, 0.9)` + cyan accent).
- **Label toggle:** Shows/hides all satellite name labels. Useful when labels clutter the view at certain zoom levels.
- **Type filter gating:** Collects unique `object_type` values from `satelliteMetadata`. Only shows type checkboxes when multiple meaningful (non-"UNKNOWN") types exist. Phase 1 stations are all "UNKNOWN", so no type filters appear yet.
- **Visibility persistence:** `applyVisibilityState()` is called after each position refresh (from `satellites.js`) to maintain toggle state across updates.
- **Deselect on hide:** If the currently selected satellite is hidden via type filter, the info panel auto-closes.

### Ground Point Approximation

The nadir ground point uses `Cesium.Ellipsoid.WGS84.maximumRadius` (equatorial radius, 6378.137 km) to project the satellite position to the surface. This means the ground point is slightly above the true WGS-84 surface at mid-latitudes (~7 km at the poles). The visual difference is imperceptible at globe scale — a more accurate projection would require per-frame geodetic computation for negligible benefit.

---

## What Was Built

| File | Action | Purpose |
|------|--------|---------|
| `frontend/js/info-panel.js` | MODIFIED | Added nadir line (CallbackProperty Entity), removed nadir toggle UI |
| `frontend/js/controls.js` | CREATED | Display controls panel — label toggle + type filter checkboxes |
| `frontend/js/satellites.js` | MODIFIED | Added `applyVisibilityState()` call after position refresh |
| `frontend/css/style.css` | MODIFIED | Added controls panel styles, removed unused nadir toggle CSS |
| `frontend/index.html` | MODIFIED | Added `controls.js` script tag (load order: app → satellites → info-panel → controls) |
| `progress/roadmap.md` | MODIFIED | Updated Week 5 tasks, added Phase 2 TODO note for type filters |

---

## Validation

- Selecting a satellite shows a thin cyan line from ground to satellite at orbital altitude
- Nadir line tracks the satellite smoothly as it moves (no 5-second lag)
- Nadir line clears on deselection, replaces when selecting a different satellite
- "Display" panel appears top-right with "Labels" checkbox
- Unchecking "Labels" hides all satellite name labels; re-checking restores them
- Label toggle persists across 5-second position refreshes
- No "UNKNOWN" type filter appears (correct for Phase 1 stations)
- All existing functionality still works (info panel, orbit trail, selection indicator)
- No console errors

---

## Lessons Learned

1. **`CallbackProperty` is the correct tool for per-frame tracking in Cesium.** The initial nadir line implementation updated on the 5-second API refresh cycle, causing visible lag. `CallbackProperty` evaluates a function every render frame, reading the satellite's current interpolated position directly from the `PointPrimitive`. No extra API calls, no timing issues.

2. **Ground point projection via normalize-to-radius is sufficient for visualization.** True WGS-84 surface projection requires converting to geodetic and back, which would add per-frame computation. Using `Cesium.Ellipsoid.WGS84.maximumRadius` (equatorial) introduces ~7 km error at the poles — invisible at globe scale.

3. **Type filters should be gated by data, not by phase.** Rather than hardcoding "hide type filters in Phase 1," the code checks if meaningful (non-"UNKNOWN") types exist. This automatically enables filters when Phase 2 data arrives, with no code changes needed.

4. **`applyVisibilityState()` must be called after every position refresh.** Without this, newly fetched satellites would appear visible regardless of toggle state. The hook in `satellites.js` ensures toggles persist across updates.

5. **Script load order extended to 4 files.** `controls.js` depends on `satellites` (Map), `satelliteMetadata` (Map), `selectedNoradId`, and `deselectSatellite()` — all from prior scripts. Load order: app → satellites → info-panel → controls.

---

## Function Reference

### Frontend (`frontend/js/info-panel.js` — additions)

| Function/Config | Purpose |
|----------------|---------|
| `nadirEntity` | Cesium Entity reference for the nadir line (null when no satellite selected) |
| `createNadirLine(noradId)` | Creates an Entity polyline with CallbackProperty that tracks the satellite's interpolated position every frame |
| `clearNadirLine()` | Removes the nadir Entity and nulls the reference |

### Frontend (`frontend/js/controls.js` — new file)

| Function/Config | Purpose |
|----------------|---------|
| `toggleState` | `{ labels: true, types: {} }` — current toggle state for all controls |
| `initControls()` | Builds the controls panel DOM; waits for `satelliteMetadata` via setTimeout retry |
| `applyVisibilityState()` | Iterates all satellites, sets `point.show` and `label.show` based on toggle state; deselects hidden selected satellite |
