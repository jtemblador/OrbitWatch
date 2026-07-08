# Task 9.6 — Live-site QA → UI feature round (search, conjunction UX, heat fix #2)

**Date:** Jul 8, 2026
**Status:** DONE (frontend-only; ships via the ~1 min REUSE deploy)
**Tests:** no pytest (frontend convention). Verified with **Playwright MCP** against a
local preview serving the real live 5,000-sat snapshot — feature checks, perf
counters, a 7-state mode-transition matrix, and zero console errors on every pass.

---

## Goal

9.6 was scoped as "verify the deployed site." The verification happened — by Jose,
on the real site — and what it surfaced was a **user-driven UX/feature round**:
the site ran hot on Chrome even with an empty globe, the conjunction UI needed to
be the centerpiece (not the satellite clutter), and a set of concrete gaps
(search, editable clock, threat colors, ground context for encounters). So 9.6
became: fix what QA found + build what QA showed was missing, with the same
review rigor as a backend phase.

---

## What was built (all frontend, ~730 lines across 9 files)

| Feature | Where |
|---------|-------|
| **Search bar** (top-center): name / NORAD-id / alias autocomplete → select + fly + reveal | `js/search.js` (new) |
| **Conjunction list**: top 20 + **Show more**, group-colored names, **red TCA**, **miss-distance gradient** (0.1 km red → 0.9 km yellow, `missColor()` hsl ramp), sticky header | `js/conjunctions.js`, CSS |
| **Ephemeris at TCA**: nadir drop-line + ground marker with lat/lon — what land the encounter is over | `js/conjunctions.js` |
| **"Conjunctions only" views** — either/or buttons: **Top 20** = short fading arcs (~10 % of orbit centered on TCA, per-vertex alpha ramp, one batched `Primitive`, **clickable** via `GeometryInstance.id`) that drill into the full 2-trail focus and restore on deselect; **All** = only the ~557 participant dots (+ worker mask, below) | `js/conjunctions.js`, `js/controls.js` |
| **Per-satellite exclusive reveal** (`revealedSats`): focusing a conjunction un-hides only its two participants; switching focus re-hides the old pair (replaced whole-group reveal) | `js/controls.js` |
| **Detail panel**: two-line header (name / count), both sat names per row, **⤓ Jump to TCA** button; "All"-click soft-focuses (trails + orb, no clock jump) | `js/conjunctions.js` |
| **Editable HH:MM:SS clock** — click a field, type, Enter/blur commits (jump on the current sim date), Escape cancels | `js/clock.js` |
| **Trails**: colored by display group (match the dots); **single depth-tested primitive** → the Earth occludes the far side (removed the faint behind-globe ghost that obscured conjunction trails) | `js/info-panel.js`, `js/conjunctions.js` |
| **Startup default**: opens on the "All" conjunction view, all group filters off → ~557 objects on load, not 5,000 | `js/controls.js` |
| **Perf — heat fix #2** (Chrome fan on an *empty* globe): `animationTick` only calls `requestRender()` when a **visible point actually moved** — empty globe while playing = ~0 renders (measured 1 render/1.5 s vs 27/1.2 s with sats) | `js/satellites.js` |
| **Perf — worker participant mask**: in "All" mode the worker propagates only participants (measured **4,999 → 557** per batch) | `js/propagation-worker.js`, `js/satellites.js` |
| Themed dark scrollbars on the overlay panels | CSS |

---

## Review — two adversarial rounds, 4 findings, all fixed + repro-verified

**Round 1 (3):**
1. **HIGH — pause → toggle a group → sats never appear.** "All"-mode masked
   batches set non-participants `ok=false`; exiting the mode while paused never
   requested an unmasked batch (paused guard). Fix: `refreshSatellites(force)` —
   a mode change forces one batch at the frozen sim time. *(Verified: paused
   toggle → 2,626 Starlink shown.)*
2. **HIGH/MED — Escape in the clock committed instead of cancelling** — `tick()`
   ran while the field still had focus (restore gated off by `isEditingTime`),
   then blur committed the typed text. Fix: `cancelTimeEdit` flag consumed by the
   blur handler. *(Verified: drift 0 ms.)*
3. **MED — entering Top-20 with an active focus left orb/trails over the arcs.**
   Fix: `setConjOnly` tears down focus visuals on **every** mode transition.
   *(Verified: 7-state transition matrix, 0 leaked primitives.)*

**Round 2** re-traced all three fixes (clean: ordering, re-entrancy, TDZ, clock
edges) and found **1 more:** `force` didn't bypass `workerBusy` — two mode
toggles inside the worker's ~25 ms roundtrip while paused dropped the second
forced batch. Fix: `pendingForcedRefresh` flag re-fired when the in-flight batch
lands. *(Verified: synchronous triple-toggle while paused → 0/4443 → 4442/4443
recovered; the 1 straggler is a genuinely-failed satrec.)*

---

## Lessons learned (durable → key_information)

- **`requestRenderMode` ladder, rung 2:** capping the loop at 30 fps isn't
  enough — `requestRender()` on frames where nothing visible moved still redraws
  the whole globe (Chrome runs hot on an *empty* scene). Gate the render request
  on actual visible change.
- **Masked worker batches interact with the ok-sentinel:** a masked-out sat looks
  identical to a decayed one (`ok=false`). Every mode *exit* must force one
  unmasked batch — **even while paused** (propagating at a frozen time is valid),
  and a forced request must survive `workerBusy` (pending flag) or a paused
  double-toggle strands stale state until unpause.
- **`contenteditable` Escape-cancel needs a flag consumed in blur** — blur always
  fires the commit handler, and a restore attempted *before* blur is gated off by
  the very focus check that makes editing work.
- **Every view-mode transition must tear down cross-mode visuals** — the round-1
  Top-20 finding is the same class as 9.4's narrow-teardown lesson: symmetric
  enter/exit cleanup, not just one direction.
- **Batched `Primitive` + per-instance `GeometryInstance.id` = pickable at one
  draw call** — the arc-click feature cost no extra primitives.
- **A single depth-tested polyline gets Earth occlusion for free** — the old
  two-primitive near/far ghost was both clutter and cost.
- **RTN readout context** (user question): the detail rows show miss (colored),
  relative speed, TCA; R/T/N is the miss split into radial / along-track /
  cross-track — √(R²+T²+N²) = miss. Formation pairs (TerraSAR-X × TanDEM-X) show
  ~0 rel-speed; along-track usually dominates.

---

## Deferred / open

- **Real-GPU confirmation** of heat fix #2 (headless proves the mechanism; the
  fan test is the user's machine).
- Real iOS/WebKit device check of `_isoToEcma` (long-standing).
- Old 9.1 polish leftovers (severity colors now largely covered by the miss
  gradient; TCA countdown / alert sort still open — fold into 9.9 if wanted).
- Arc hitbox is 2 px — widen if clicking feels fiddly on the live site.
