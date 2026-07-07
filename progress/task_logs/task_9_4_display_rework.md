# Task 9.4 (b) — Display rework: render-loop heat fix + group filters

**Date:** Jul 7, 2026
**Status:** DONE (frontend-only; ships with the 9.4 deploy)
**Tests:** no pytest (frontend convention). Verified by three headless-Chrome
smoke suites (full + round-1 fixes + round-2 fixes), all green, mutation-checked.

---

## Goal

Prompted by real-world testing: the deployed site spun up laptop fans and ran
hot on two different machines. The user proposed group filters to render fewer
satellites. Diagnosis reframed it — the dominant cost was **Cesium's continuous
render loop**, not the satellite count — so this task does both: tame the render
loop (the actual heat cure) **and** build the group-filter system (UX + a real
secondary perf dial).

---

## Diagnosis (why it ran hot)

1. **#1 cause — Cesium never stops re-drawing.** By default the viewer renders at
   ~60 fps forever, redrawing the whole globe even when nothing changes and even
   while paused. `requestRenderMode` was never set. **This is why it was hot
   regardless of satellite count** — an empty globe would run just as hot.
2. Animating all ~5k points every frame (secondary).
3. Default scene "bling" (fog etc.) — extra per-frame GPU.

Filters address (2); the big lever is (1).

---

## Approach + key decisions

- **`requestRenderMode: true` + `maximumRenderTimeChange: Infinity`** — the scene
  redraws only when `scene.requestRender()` is called (or the camera moves).
  Infinity because we animate off our own `simClock`, not Cesium's clock.
- **Self-driven 30 fps `requestAnimationFrame` loop** (`animationTick`) replaces
  the old `preRender` lerp. It advances the lerp for visible sats and calls
  `requestRender()` — but **only while playing and the tab is visible**. So the
  GPU goes fully idle the instant you pause or background the tab. (Headless:
  **0 renders while paused**.) `fog.enabled = false`.
- **The `requestRenderMode` tax:** any globe mutation that can happen while
  paused must call `requestRender()` itself. Audited every interaction; the
  covered set: select/deselect, group + label toggles, trail checkbox,
  conjunction-focus clear. Camera drags and `flyTo` self-render.
- **5 mutually-exclusive display groups**, classified at load in
  `snapshot-data.js` from name + orbit regime (constellation first, then
  regime): **Starlink · Space Stations · Navigation/MEO · Other LEO · GEO/high**.
  No snapshot/backend change — regime mirrors the backend `screening_volumes`
  logic (ecc gate, GEO period band, LEO ≤ 2000 km).
- **Hiding a group stops its per-frame work** — `animationTick` skips
  `entry.point.show === false`, so a filter is a real perf dial, not just visual.
  The worker still propagates all sats (~25 ms/5 s, negligible) so re-show is
  instant; "stop the work" targets the per-frame render/lerp, which is the heat.
- **Dots colored by group** + per-group **counts** + swatches in the Display tab
  (which dropped the near-useless object-type checkboxes). **Info-panel name
  wraps** instead of truncating with `…`.

---

## Implementation

| File | Change |
|------|--------|
| `frontend/js/app.js` | `requestRenderMode` + `maximumRenderTimeChange: Infinity`; `scene.fog.enabled = false` |
| `frontend/js/satellites.js` | `animationTick` 30 fps rAF driver (idle when paused/hidden); `visibilitychange` handler; skip hidden points; `GROUP_COLOR`/`groupColor`; `refreshSatellites` gated on `tabVisible` |
| `frontend/js/snapshot-data.js` | `SAT_GROUPS` (+ colors), `_regime`, `_classifyGroup`; `_deriveMetadata` returns `regime` + `group` |
| `frontend/js/controls.js` | Display panel rebuilt as group filters (swatch + count); `applyVisibilityState` uses groups, snaps un-hidden points, tears down orphaned interactions, `requestRender`; `revealGroups` |
| `frontend/js/info-panel.js` | `requestRender` on select/deselect + trail toggle; panel refresh gated on `tabVisible` |
| `frontend/js/conjunctions.js` | `focusedPair` + `focusedConjunctionInvolves`; `clearConjunctionVisuals` vs `clearConjunctionFocus`; `handleParticipantHidden`; auto-reveal groups on focus |
| `frontend/css/style.css` | `#info-panel-title` wraps; group-toggle/swatch/count styles |

---

## Review — two adversarial rounds, six findings, all fixed + verified

**Round 1 (4):** (1) un-hiding a group while paused showed a stale position →
snap to current on the transition; (2) trail checkbox didn't `requestRender`;
(3) hiding a conjunction participant orphaned the orb/trails → tear down focus;
(4) panel refresh not `tabVisible`-gated.

**Round 2 (2, both fallout from fix #3):** (1) the teardown reused the *blanket*
`clearConjunctionFocus`, wiping the selected sat's whole detail panel → split
into `clearConjunctionVisuals` (visuals only) + keep selection; (2) focusing a
hidden-group conjunction drew an orphaned orb/trail → **auto-reveal** the
participants' groups on focus (the list stays a complete safety view).

Each fix has a passing headless check: paused renders = 0; filter hides
2,625 Starlink + keeps 2,375; un-hide snaps to Δ 0 m; focus teardown clears
orb/trails/pair; panel preserved on partner-hide; auto-reveal re-checks the box.

---

## Validation (headless Chrome, real 5,000-sat data)

- **Heat fix:** **0 renders while paused** (GPU idle); renders + animation while
  playing; 30 fps cap mechanism in place (exact fps only visible on real GPU —
  headless software raster caps itself lower).
- **Filters:** correct per-group counts (Starlink 2,625 · Other LEO 1,682 · GEO
  515 · Navigation/MEO 170 · Stations 8); hide stops the work; re-show resumes.
- **Colors** distinct per group; **name wraps**; **zero console errors** across
  all suites.

---

## Lessons learned

- **A Cesium page runs hot because of the render loop, not the object count.**
  `requestRenderMode` + a capped, idle-able rAF driver is the fix; filtering
  helps the secondary per-frame cost. Diagnose before accepting the proposed fix.
- **`requestRenderMode` has a tax:** every paused-time scene mutation needs an
  explicit `requestRender()`. This is the class of bug both review rounds
  circled — easy to miss one (the trail toggle, round 1).
- **Narrow teardown functions.** Reusing a blanket `clearConjunctionFocus` for a
  narrow "a participant got hidden" case wiped unrelated UI state (round 2). Split
  visuals-only vs full teardown.
- **A filter should filter consistently.** Hiding a group but leaving its
  conjunctions clickable created orphaned visuals; auto-revealing on focus keeps
  the conjunction list a complete safety view while honoring the filter for
  clutter.
- **Cross-script classic-script globals are safe when accessed only in async
  callbacks** (worker round-trip, setTimeout, user event) — by then every later
  `<script>` has executed. Function-declaration globals also make `typeof` guards
  TDZ-safe.

---

## Deferred / open

- **Real-GPU/fan confirmation** — the one thing headless can't show; the user's
  live check.
- Leftover 9.1 polish (severity colors, TCA countdown, alert sort) still open —
  independent of this rework.
- Worker still propagates hidden groups (negligible; could mask at much larger N).
