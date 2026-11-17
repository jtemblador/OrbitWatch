# Task 11.0a–c — Trust fixes: element age, horizon flag, persistent formations (Nov 17, 2026)

**Status:** done on a worktree branch, not pushed. One additive snapshot-schema
change. Every new field is optional and left out when it doesn't apply, so an old
frontend reading a new snapshot works, and so does a new frontend reading an old
one. The second case matters because the frontend-only REUSE deploy serves the
live, older snapshot to new code.

## Schema (per conjunction; all optional)

| Field | Meaning | Present on |
|---|---|---|
| `age_a_d`, `age_b_d` | days from each object's EPOCH to `meta.generated_at`, 0.01 d | every event whose object is in the catalog |
| `beyond_horizon` | `true`: the pair comes closer just past the window end, so the miss is an upper bound | 24.7% of events (measured) |
| `runs` | consecutive runs this **pair** has been in the snapshot (missing = 1) | 79% of events |
| `persistent` | `true`: runs ≥ 9 AND rel speed < 0.01 km/s | 1 event (TerraSAR-X/TanDEM-X) |

Plus `meta.run.horizon_seconds`. `archive_record` keeps all of these, because it drops
only names and types. Snapshot size grows by **+6.5% gz** (1.750 → 1.863 MB on the
Oct 28 full snapshot). Most of that is the two ages on all 17k events.

## 11.0a: element age
`snapshot._element_ages` measures age from the epochs at generation time, not from
the frozen `epoch_age_days` column (same reason as `meta.max_epoch_age_days`).
If an object or the timestamp is unknown, that side is left out rather than set to 0.
On real data: median event age is **1.08 d**, p95 **1.66 d**, and **56 of 17,132**
events (0.33%) have a side older than 3 d. This matches the roadmap's 0.4%.
Frontend: the detail row shows "elements 14 h · 1.3 d old". If either age is over 3 d,
the row dims to opacity 0.55, the detail row gets the tag
**"low confidence (stale elements)"**, and the list row gets a "stale" tag with the
full text as a tooltip.

## 11.0b: horizon flag. The planned test didn't work, so I replaced it.
The plan said to flag "TCA within 1 h of the window end AND range still decreasing at
the end". I checked that test on the full Oct 28 snapshot (17,132 events): I rebuilt
the satrecs from its OMM and brute-forced each last-hour pair over the hour after the
window end on a 2 s grid.

| Test on the 4,656 last-hour events | Closer pass found in the next hour |
|---|---|
| range-rate < 0 at end (1,851 events) | 51.9% |
| range-rate ≥ 0 at end (2,805 events) | 39.5% |

The range-rate sign barely separates the two groups. A co-altitude pair's range
oscillates with the half-orbit node geometry, so the sign at the window end mostly
reflects orbital phase, not the trend. The fine stage's TCA also can't tell us
anything here: Newton solves Δr·Δv = 0, so range-rate at the TCA is ~0 by
construction.

**What shipped:** `conjunctions.annotate_horizon` runs a real look-ahead screen.
It takes only the participants of last-hour events and screens them with the same
engine (`run_screen`, legacy Euclidean cut at the largest candidate miss, same
accelerators) over [end, end + 130 min]. It flags an event if its pair has a pass
after the end that is closer than the reported miss.
- **130 min, not 1 h.** A pair converging orbit to orbit meets again at the same
  node one period after its TCA. A 1 h look-ahead misses that pass (there is a test
  for this). 130 min covers every LEO period, up to ~127 min at 2,000 km. GEO isn't
  covered, so a missing flag there makes no claim.
- **Check of the 1 h variant:** of 2,238 events it left unflagged, 4 (0.2%) had a
  closer pass on the brute-force grid. These were boundary cases (passes that
  straddle the window end). Of the events it flagged, 85% were confirmed by the 2 s
  grid. The rest are fast crossings that a 2 s grid samples too coarsely (at 12 km/s
  that is ±12 km), so grid misses there don't count as disagreement.
- **Result with 130 min:** 4,234 of 17,132 events flagged (24.7%), which is 91% of
  the last-hour events. That fits the explanation for the pile-up: a pair's best
  pass lands in the final hour *because* the pair is still converging.
  Cost: **35.6 s** here, on top of a ~5 min CI screen. It is logged as
  `meta.run.horizon_seconds`.
- **Annotation only.** It works on copies, and `run_screen`'s output (what the A/B
  gate compares) is untouched. In `build_snapshot.py` it is wrapped so that a failure
  costs only the label (`::warning::`), never the refresh.
- Frontend: "≥ X km" with an upper-bound tooltip in the list, the detail panel and
  the TCA orb label. Only 1 of the closest 500 events is flagged. Flagged events tend
  to be the slower, wider converging pairs, not the near-zero crossings.

## 11.0c: persistent formations
`snapshot.carry_forward_runs` links runs on the **pair alone**, on purpose. This is
different from `hist`'s (pair, TCA ± 900 s) link. A co-moving pair's range is nearly
flat, so its TCA jumps between orbits from run to run. TerraSAR-X/TanDEM-X's TCA
moved by hours between consecutive snapshots. Over the last 30 archived runs, of 351
events with v_rel < 0.01 km/s, 217 recur by pair but only **81 by (pair, TCA)**, so a
TCA-linked counter would almost never reach 9. The speed gate stops pair-only linking
from flagging recurring Starlink *crossings*: they keep counting runs but stay
unflagged.

- Bootstrap: a pre-11.0 previous snapshot has no `runs`, so the count falls back to
  `1 + len(hist)`. That is a sound lower bound and means the deploy that introduces
  11.0 doesn't restart every chain at 1.
- If a pair misses one run, its count resets. This is strict, by design.
- I replayed the chain over all 316 archived runs before Oct 28 18:55. Across the
  archive, 20 distinct pairs were flagged at some point. TerraSAR-X/TanDEM-X led with
  315 flagged runs, followed by GEO co-located slots: Intelsat 30/31, Optus D3/MEV-1,
  Turksat 3A/6A, Galaxy 36/ViaSat-3 F1. On the target snapshot, only
  **TerraSAR-X × TanDEM-X (0.084 km, 0.0 km/s, 244 runs)** is flagged. That is 1–2 per
  run, so the toggle hides very little and is not used to hide data.
- The 7.2 co-located suppression is **unchanged**. These pairs stay in the snapshot
  with a label and are not dropped.
- Frontend: a "Hide persistent formations (N)" checkbox, **on by default**. It is
  built the same way as the 10.10 filter: `filterConjunctions` filters **before** the
  500 cap, and the toggle swaps the whole set (list, dots, worker mask, arcs). The
  detail row gets a "persistent formation · N runs" tag. The panel's "none here"
  message now also counts filter-hidden events.

## Tests
- pytest: **+27**. `TestHorizonAnnotation` (8, converging/diverging drag-free
  crossers built from the ISS fixture), `TestElementAge` (5), `TestCarryForwardRuns`
  (11, including the TerraSAR replay through the real `build_snapshot` and a gap
  reset), 2 `main()` wiring tests (a spy confirms the annotation input equals the
  screen; an injected failure is non-fatal), and 1 `_conjunction` test.
  `test_snapshot.py` + `test_conjunctions.py`: **152 passed**. Full suite: 522 passed,
  7 skipped. The 147 failures/errors are this sandbox's missing catalog cache and
  SPICE kernel, the same set as before, and none are in touched classes.
- Mutation-checked: **11/11** Python mutations caught (look-ahead 1 h, near-band,
  the comparison, input mutation, the `is True` gate, the runs increment, the speed
  gate's strictness, the bootstrap, a TCA-keyed link, age source, rounding). The
  first draft's diverging test passed for the wrong reason: the event was never a
  candidate. The mutation run caught this, and the test was rebuilt.
- Node: new `tests/test_11_0_trust_fields.js` (**22 checks**, **10/10** mutations
  caught: filter-before-cap, typed adapter, strict `> 3 d`, default-on, "≥" + tooltip,
  pre-11.0 renders nothing extra). The 10.10 harness was updated for the shared filter
  (12/12). 9.10 sparkline 22/22. Not in CI, same as the earlier harnesses.
- Headless (Chrome + Playwright; the CDNs are blocked here, so Cesium 1.139.1 and
  satellite.js 6.0.1 were served from their npm tarballs). Two runs:
  1. A real-data 11.0 snapshot, rebuilt through the real builder from the Oct 28
     archive with the runs chain replayed over 316 runs. Results: the default view
     hides TerraSAR-X and the toggle brings it back; the "≥ 0.08 km" tooltip and the
     persistent tag render; stale rows dim and get their tag.
  2. The unmodified pre-11.0 snapshot. It renders as before: no trust line, filter
     count (0).

  **0 app console errors.** The only failure was the sandbox-blocked GoatCounter
  script.

## Not done / follow-ups
- `progress/roadmap.md` in this worktree has no Phase 11 section (the planning edit
  is uncommitted in the main checkout), so the checkboxes there were not ticked.
  CLAUDE.md is not updated either.
- The CI A/B gate wasn't run. The engine is untouched, but `scripts/build_snapshot.py`
  is in `ci.yml`'s path filter, so the push will start the ~40 min gate.
- The horizon pass adds ~36 s to each cron. If the cron ever needs trimming, the
  first lever is a per-regime threshold instead of the single largest candidate miss.
- A stale event never ranked in the closest 500 on the test snapshot, so the stale
  UI was checked by loading real stale events into the set.
