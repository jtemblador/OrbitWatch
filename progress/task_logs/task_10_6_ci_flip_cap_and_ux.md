# Task 10.6 — CI A/B gate, production flip, full-catalog cap lift, + the frontend perf/UX round (Phase 10 finale)

**Date:** Jul 15, 2026
**Status:** DONE — **Phase 10 complete.** The live site screens the full active
catalog, gated byte-identical in CI, with a smooth conjunction-first UI.
**Live:** https://jtemblador.github.io/OrbitWatch/ (16,074 sats / 7,872
conjunctions at write time; self-refreshing).

---

## Goal

Turn the validated Phase-10 engine on in production and remove the 5,000-sat
cap, with a standing CI gate that re-proves byte-identity on each day's real
fetch before anything ships. Then fix the frontend cost of showing the whole
catalog.

## Part 1 — CI gate + flip + cap lift (the engine finale)

**`build_snapshot.py`** now runs `fused+sieve+refine` (`_ACCEL`), so the
production screen is the accelerated engine.

**`deploy.yml`** (rebuild path): fetch the active catalog ONCE → **two-tier A/B
gate** → screen the FULL catalog → publish. The gate (`set -e`, exit non-zero
fails the `build` job → `deploy` never runs, so a wrong snapshot can't ship):
- **Tier 1** — `ab_screen.py --baseline classic --refine --max-sats 5000`:
  the complete contract (classic == fused+sieve+refine) where classic fits.
- **Tier 2** — `ab_screen.py --baseline fused --refine --hours 6` (full
  catalog): isolates the sieve's event-level no-skip on the real full catalog,
  the one thing offline tests can't construct. **Memory-safety fix:** the
  `fused` baseline now honors `--refine` too (`ab_screen.py`), so both sides
  keep the C++ fine pre-cut and neither materializes the ~13 GB Python fine
  dicts — Tier 2 ran at **3.24 GB**, not an OOM.

**Cap lifted:** `--max-sats 5000` removed → the whole ~16k catalog is screened
and displayed. Local pre-flight (the crux, measured before any CI change): full
16,030-sat / 24 h screen = 307 s / 5.38 GB / **1.29 MB gz** (under the 5 MB
budget) → decision: lift to full.

**Verification (all measured, not asserted):**
- Local: Tier-1 310==310, Tier-2 3,873==3,873 byte-identical.
- **In CI on live data:** Tier-1 **308==308**, Tier-2 **3,887==3,887**
  byte-identical; snapshot 16,072 sats / 7,769 conj / 1.29 MB gz shipped;
  deploy green; live site verified serving the full catalog, 0 console errors.

**Cron gate-skip (post-launch tuning):** the gate re-runs the OLD slow cascade
as its reference (~40 min on the 4-vCPU runner — Tier-2's sieve-off medium is
the long pole), which tripled the robot job to ~52 min. The gate guards CODE
changes, not data refreshes, so **scheduled (cron) rebuilds now skip it**
(`if: … && github.event_name != 'schedule'`) and just re-screen with the
validated engine (~10 min); the full gate still runs on every manual dispatch
and any push-triggered rebuild. Cron stays 3×/day.

## Part 2 — Frontend perf + conjunction UX round

Showing the full catalog made weak machines lag: the default view propagated
**~6,900 conjunction participants every frame** (up from ~557 at the 5k cap) —
the client-side SGP4, not Cesium's drawing, was the cost.

**Perf (decouple DISPLAY from SCREEN — the screen stays complete):**
- **Display cap** (`snapshot-data.js`, `CONJ_DISPLAY_CAP = 500`): the UI works
  with the closest 500 conjunctions (~800 participant dots vs ~6,900); list,
  arcs, participant set, and the worker mask all follow from that one slice.
  Header reads "closest 500 of N pairs" so it stays honest; the full set lives
  in the snapshot + the `data`-branch archive.
- **Focus isolation** (`conjunctions.js` `isolateSats`/`isolationMask`):
  focusing a conjunction / selecting a satellite hides every other dot AND
  masks the propagation worker to just the involved objects — **2–3
  propagations/frame instead of ~800**. The big CPU cut.
- **Occlusion throttle** (`satellites.js`): points behind the Earth update at
  1/4 rate (staggered by index, ghost-safe — the fresh lerped position is
  tested, never the stale rendered one). **15 fps** dot animation (was 30).

**UX round:**
- **Escape zoom stack:** All Conjunctions → Conjunction View → TCA View, and
  Escape walks UP one level (TCA→Conjunction→All), with a top-center **POV
  badge** naming the level + what Escape does next ("TCA View | A × B | Esc →
  Conjunction View"). `tcaZoomed` + `selectedNoradId` encode the level.
- **Pair highlight:** both conjunction participants get the emphasis ring
  (unified point styling — hover > selected/pair > plain — so hover, selection,
  and pair never fight over a point's size/outline).
- **Hover emphasis** + pointer cursor (`MOUSE_MOVE` pick).
- **Transport bar:** rewind (−10/−5/−1×) + pause + forward, **spacebar**
  pause/resume; `|speed|` fixes so the batch cadence + trail refresh don't
  misbehave at negative speeds.
- **TCA ground marker** now shows location + TCA date/time.
- **Labels:** manual toggle removed; auto-on for the isolated handful, sparse
  in bulk (`_wantsLabel`).
- **Duplicate orbit-trail fixed** (the selected sat's info-panel trail and its
  conjunction-focus trail were the same orbit drawn twice); "Click to Jump to
  TCA".

All verified headless (Playwright, 0 console errors) at each step, then on the
live site after deploy.

## Files

- `scripts/build_snapshot.py` (`_ACCEL` flip), `scripts/ab_screen.py`
  (fused baseline honors `--refine`), `.github/workflows/deploy.yml`
  (fetch-once + two-tier gate + cap lift + cron gate-skip).
- Frontend: `snapshot-data.js`, `satellites.js`, `conjunctions.js`,
  `info-panel.js`, `controls.js`, `clock.js`, `css/style.css`.

## Findings (durable)

- **A CI gate should re-run the reference only when the thing it guards
  changes.** The engine is fixed code; data refreshes don't need a 40-min
  classic re-screen every 8 h. Gate on push/dispatch, skip on cron.
- **Full-scale A/B must keep BOTH sides on the memory-safe path.** Comparing
  sieve-off vs sieve-on at 16k means the sieve-off side needs the C++ refine
  too, or it re-creates the #7 dict wall the phase removed.
- **"Screen everything" ≠ "render everything."** Lag scaled with displayed
  *conjunction participants*; capping the DISPLAY (not the screen) to the
  closest N cut per-frame work ~8× while keeping the achievement (full screen)
  and honesty (header shows N of total).
- **Isolation is a propagation lever, not just a visual one** — masking the
  worker to the focused handful is where the CPU actually drops.

## Commits

`d55ebff` (flip + gate + cap), `b8c1427` (frontend round + cron gate-skip),
plus this docs commit. Rebuild run 29382705402 = the first full-catalog CI
screen + gate (green).
