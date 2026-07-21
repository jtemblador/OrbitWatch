# Task 10.8 — Full-codebase review, 12 fixes, + a UX round

**Date:** Jul 21, 2026
**Status:** DONE — 8-agent full-codebase review, 10 findings fixed, 1 self-introduced
regression caught + fixed by a review-of-the-fixes, + 2 user-requested UX
improvements. 583 tests passing; every frontend change headless-verified.
**Live:** https://jtemblador.github.io/OrbitWatch/

---

## Goal

A full-project code review (not a diff review): fan out specialized agents over
every subsystem, verify the science is actually correct, and fix what's real.
Then a short user-driven UX round on top.

## The review (8 parallel agents)

Subsystems: C++ engine, Python screening pipeline, transforms + fetchers, FastAPI
backend, snapshot + build/CI, frontend JS, the test suite (run + audited), and an
end-to-end science/numerical audit. Several agents built/ran/probed, not just read.

**Headline: the core is excellent.** The C++ engine had no High/Med defects
(OpenMP per-thread copies, deep-space state reuse, GIL, no-skip all verified
sound). The science audit cross-checked propagation **bit-identical to the Vallado
`sgp4` reference (0 m across 2,400 comparisons incl. deep-space)**, frames/units/
GMST/RTN all correct, conjunction TCA/miss/rel-speed reproduce a brute-force grid
to sub-mm, and the SOCRATES validation is genuinely same-method. The test suite is
deterministic (583) with the <1 m SGP4 claim locked at the nanometer level. The
real issues clustered in the peripheral surfaces.

## The 10 findings fixed

| # | Sev | Fix |
|---|-----|-----|
| 1 | High | **Search left the searched satellite hidden** — a stale `isolationSet` (focus while a group filter was on, `conjOnlyActive===null`) kept it masked. `search.js selectResult` now clears the conjunction focus first. |
| 2 | High | **A/B gate floored only the raw fetch, not the screened subset** — an `is_screenable`/regime regression could make both A and B screen ~nothing, agree `0==0`, and pass. Added a 25%-of-raw screened-subset floor in `ab_screen.py` + `build_snapshot.py`. |
| 3 | High* | **`/api/refresh`-class blocking on first load** — `_ensure_data` did a blocking fetch/parse on the event loop in every handler's first call. `main.py` lifespan now warms the catalog via `run_in_threadpool` at startup. (*local-dev only; prod is static.) |
| 4 | Med | **Co-located suppression dropped an UNRELATED near-miss** — `<0.5 km/s + <50 m` suppressed any pair. Now: same-launch suppresses below the formation floor; cross-launch suppresses only truly co-moving (`<5 m/s AND <50 m`) docked hardware, so a genuine unrelated 30 m pass is kept. |
| 5 | Med | **API screen applied the LEO-1 fallback volume to MEO/HEO** — `ConjunctionScreener.screen` now gates on `is_screenable`, matching the deployed snapshot pipeline. |
| 6 | Med* | **`/positions` + `/track` ran CPU-bound propagation on the event loop** — now via `run_in_threadpool` under the lock, like `/conjunctions`. |
| 7 | Med | **`focusConjunction` committed focus/isolation before confirming the geometry drew** — a decayed pair left an isolated-to-nothing globe. Reordered to draw first, bail before committing. |
| 8 | Med | **Cron could deploy engine code before ci.yml validated it** — `deploy.yml` now runs the A/B gate on a *scheduled* rebuild too if engine code changed in the last ~10 h (`git log --since`, `fetch-depth: 50`). |
| 9 | Med | **Unpinned numeric stack** — pinned numpy/scipy/pandas/pyarrow to the tested versions so a green byte-identical gate is reproducible. |
| 10 | Low | **curl fallback lost the HTTP status** — rewrote `_download_via_curl` to read `%{http_code}` and raise `HTTPError` for ≥400, honoring the documented 403/404 no-retry contract on both paths. |

## Review-of-the-fixes → one self-introduced regression caught

A second review (3 agents) over the just-made fixes found that #7's reorder
**introduced a crash**: refocusing from one conjunction straight to another
(clicking a second list row without Escape) drew the orb first, then
`revealSatsExclusive` ran `applyVisibilityState` while `isolationSet` still held
the OLD pair → the new participants read as filtered-out → `handleParticipantHidden`
tore down the just-set focus, nulling the orb → `flyTo(null)` threw. Root cause was
pre-existing (reveal ran before isolate); the reorder turned a silent glitch into a
crash. **Fix: isolate BEFORE reveal**, so `isolationSet` already contains the new
pair when `applyVisibilityState` runs. Headless-verified: refocus is now clean, no
throw, correct final state.

## UX round (user-requested, live-tested)

- **POV badge was conjunction-centric.** It read "All Conjunctions" even in browse
  mode and never updated when you clicked a plain satellite (`showConjunctionsFor`
  early-returns for a no-conjunction sat before touching the badge). Rewrote
  `updatePovIndicator` to be mode- and selection-aware (browse+nothing → hidden;
  selected → "Satellite | NAME" or "Conjunction View | NAME"; conj-mode+nothing →
  "All Conjunctions"), and wired the refresh into `selectSatellite` (info-panel.js)
  and `setConjOnly` (controls.js) so it updates on selection AND on mode toggle.
- **Full-orbit framing on selection.** The orbit trail already drew the full
  period, but the camera never reframed, so a HEO/high orbit swept off-screen and
  you saw only the near-Earth arc. `info-panel.js flyToOrbit` flies to a bounding
  sphere sized to the orbit's apoapsis on every selection; search uses it too
  (dropped the redundant `flyToSat`). Verified across LEO/GEO/HEO (screenshots: a
  HEO orbit went from swept-off-screen to a full framed ellipse).

## Validation
- **583 tests passing** (co-location + is_screenable changes proven test-safe: the
  suite's co-location fixtures are same-launch or truly-co-moving; the screener
  fixtures are LEO/screenable — neither behavior change alters expected results).
- `_is_co_located` + curl-HTTPError behavior functionally probed with the exact
  bug-repro inputs.
- Every frontend change headless-verified (Playwright): refocus (no crash), search
  (searched sat visible), POV badge (all 5 states), orbit framing (LEO/GEO/HEO
  camera heights + screenshots), 0 console errors throughout.
- CI/scripts changes empirically verified (floors exit 1; `git log --since`
  pipeline emits the right token; `if:` expression sound).

## Lessons learned (durable → key_information)
- **Co-location suppression must distinguish related from unrelated.** The
  dangerous class (very close, low closing speed) looks like docked hardware —
  gate cross-launch suppression on truly co-moving (`<5 m/s`), never miss alone.
- **An `A==B` gate needs a floor on the SCREENED set, not just the raw input.** Two
  identically-degraded inputs agree trivially.
- **Reordering UI setup can expose a latent state-sync bug** — `revealSatsExclusive`
  running `applyVisibilityState` with a stale `isolationSet` + fresh `focusedPair`
  fires the orphan-teardown. Commit `isolationSet` before revealing.
- **The POV/zoom badge must refresh on mode change**, not just selection/focus —
  `setConjOnly` and `selectSatellite` both call `updatePovIndicator`.
- **Full-orbit view = camera framing, not just geometry.** The trail was always the
  full period; the fix was flying to a bounding sphere sized to apoapsis.

## Not fixed (out of scope, flagged in the review)
- C++ `uint32_t` sieve-index ceiling (~92k objects; matters only for the future
  debris-scaling path) and the naive year-2100 leap rule in `invjday`.
- Docstring completeness (UT1-UTC omitted from `coordinate_transforms` neglected-
  terms; the "0.000 km" summary rounds real ≤0.014 km SOCRATES residuals).
- `test_spice.py` asserts nothing (print-only); `test_api.py:215` epoch-age test is
  freshness-coupled; SOCRATES CSV parse has no per-row resilience.
