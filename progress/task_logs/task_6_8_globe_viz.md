# Task 6.8 — Minimal Globe Visualization

**Date:** Jun 13, 2026
**Status:** DONE
**Tests:** frontend-only — no automated tests (project convention); verified by running the app

---

## Goal

Prove the conjunction data reaches the browser end-to-end: fetch `/api/conjunctions` once, list the
close approaches in a corner overlay, and draw a visible line for the flagged geometry. Full polish
(alert table, fly-to, severity colors, detail panel) is Phase 9.

---

## Approach

- **New `frontend/js/conjunctions.js`** (IIFE, loaded last) — one fetch on startup, a top-left
  overlay list (`pair · miss · TCA`, header shows total flagged), and connecting lines drawn via
  `CallbackProperty` between the pair's live points (reuses the nadir-line pattern).
- **Live connecting line, not a TCA-static segment.** A conjunction line at TCA is tiny (objects are
  close by definition) — invisible at globe zoom. Instead we draw a line between the pair's *current*
  positions: long while they're apart, shrinking as they converge. So a *visible* line needs a pair
  currently far apart — crossing geometry — which correlates with the *larger* flagged miss
  distances (opposing planes). **Lines = widest-separation flagged events; list = closest-first.**
- **Degenerate-pair floor (`CONJ_MIN_VISIBLE_KM = 0.05 km`).** Skips essentially co-located objects
  (docked station modules sit at <5 m) while still surfacing genuine sub-km conjunctions (real
  Starlink pairs reach ~0.3 km). Initially 1 km (tuned for docked ISS modules) — lowered after real
  Starlink data showed it was hiding the closest real events.
- Orange theme (distinct from the cyan trails/selection); flagged points recolored.

---

## Implementation

| File | Change |
|------|--------|
| `frontend/js/conjunctions.js` | NEW — fetch + list overlay + connecting lines + point highlight |
| `frontend/index.html` | load `conjunctions.js` last |
| `frontend/css/style.css` | `#conjunction-list` overlay styles (top-left, orange accent) |

---

## Validation

- **Demo verified in the browser** (user confirmed "looks good") on both the seeded stations
  catalog and the real ~300-sat Starlink shell.
- **Endpoint data path** confirmed via the running app: `ORBITWATCH_DEMO_SEED=1` →
  `/api/conjunctions` returns the crosser + (on Starlink) hundreds of natural events; the list shows
  pair/miss/TCA, lines render for the widest-separation pairs, no console errors.
- `conjunctions.js` syntax-checked with `node --check`.

---

## Lessons learned

- **A conjunction line is inherently short** (the whole point is two objects being *close*) — at
  globe zoom it's a dot. The readable proof is the *list*; the visible *line* requires a
  currently-separated (crossing) pair. Don't draw "the closest" expecting a visible line.
- **A min-distance floor is dataset-dependent:** ~0 km on stations = docked artifacts (hide them);
  sub-km on Starlink = real conjunctions (show them). 0.05 km threads both. The proper fix is
  Phase 7's asymmetric RTN screening volumes / Pc, not a flat floor.

---

## Deferred to Phase 9

Sorted alert table with TCA countdown, severity color-coding, camera fly-to on click, RTN detail
panel, "matched SOCRATES?" status. Conjunction auto-refresh (one fetch is enough to prove the chain).
