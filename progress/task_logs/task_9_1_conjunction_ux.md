# Task 9.1 (partial) — Conjunction UX pass (pulled forward)

**Date:** Jun 22, 2026
**Status:** DONE (the interactive core of 9.1; alert-table sorting/countdown + severity coloring remain)
**Tests:** none — frontend-only (project convention); verified by `node --check` + data-path checks

---

## Goal

The minimal 6.8 viz proved the data reached the browser but wasn't usable: orange connecting lines
knifed straight through Earth, were global/static (didn't change on selection), and the count was a
meaningless 5,678 (100 km threshold × per-encounter rows). Pulled the interactive core of Phase 9.1
forward so conjunctions are actually explorable.

---

## What changed

- **Click a conjunction → play it out.** Replaces the through-Earth line: rewinds the clock to
  **TCA − 5 min at 1×** and plays, flies the camera to the close-approach point, marks it with a
  **translucent yellow orb**, and draws **both satellites' orbit trails** (thin transparent blue,
  re-rotated every 500 ms so they track the moving dots). So you watch approach → closest → separation.
- **Selection-driven.** Selecting a satellite shows **its** conjunctions in a **bottom-right detail
  panel** (partner, miss, rel-speed, TCA, RTN), each row clickable to fly there.
- **Clickable top-left list** (closest approaches overall) with a focused-row highlight.
- **Label de-overlap.** The two conjuncting satellites' name tags are nudged apart (one above, one
  below) so both read as the paths cross; restored on deselect.
- **Time bar:** speeds now **1× / 5× / 10×** (dropped 60×) + a **LIVE** button that jumps the clock
  back to the present and clears the conjunction focus.
- **Context + sane count:** header shows `N flagged · M satellites`; threshold lowered 100 → **25 km**
  (full fix — asymmetric volumes + co-located suppression + de-dupe — is 7.2).

---

## Implementation

| File | Change |
|------|--------|
| `frontend/js/conjunctions.js` | rewritten: selection-driven, clickable list + detail panel, `focusConjunction` (lead-in playback + orb + dual trails + label offsets), count header |
| `frontend/js/clock.js` | new `setTime(ms)`; speeds 1/5/10; LIVE button |
| `frontend/js/info-panel.js` | select → `showConjunctionsFor`; deselect → `clearConjunctionFocus` |
| `frontend/css/style.css` | bottom-right `#conjunction-detail`, hover/focused states, LIVE button |

---

## Approach notes

- **Close-approach location** = either object's position at TCA, fetched from
  `/api/positions/{id}?time={TCA-as-Z}` (a literal `+` in the query would decode to a space).
- **Trails** reuse the track API (one period, 120 pts) + `computeGmst` (global from info-panel.js),
  rotated TEME→ECEF and re-rotated on a 500 ms timer so they stay aligned with the live dots.
- **Dismiss:** clicking empty globe (→ `deselectSatellite`) or **LIVE** clears the orb/trails/labels.

---

## Lessons learned

- **A conjunction line is inherently tiny / through-Earth when drawn between far-apart current
  positions.** The right UX is *fly there + jump time to TCA*, so the objects converge on screen —
  not a straight chord.
- **Min-miss floor is dataset-dependent** (docked ISS ~0 km artifacts vs real sub-km Starlink). The
  flat 25 km threshold is a stopgap; per-regime asymmetric RTN volumes are 7.2.

---

## Still open (rest of 9.1)

Alert-table sorting / TCA countdown, severity color-coding, "matched SOCRATES?" status. Plus
de-duping the list to unique pairs (counts are still encounter-level). Re-screen on large time jumps.
