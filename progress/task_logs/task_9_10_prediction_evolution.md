# Task 9.10 — Prediction-evolution sparkline (archive-driven, self-propagating)

**Date:** Aug 18, 2026
**Status:** DONE — 17 new tests (607 passing, 3× flake-clean), frontend
headless-verified against both a history-bearing and a pre-9.10 snapshot,
0 console errors. CI wired; not yet fired on a live cron.
**Live:** https://jtemblador.github.io/OrbitWatch/

---

## Goal

Show how a conjunction's **predicted miss distance moved as the elements
refreshed under it** — the last stretch item from Phase 9, and the legitimate
version of the old CDM-evolution idea. Each conjunction in the detail panel gets
a small inline chart of up to 4 predictions (3 prior + the live value).

Automated by the existing 9.5 cron, with no new per-visit work: zero extra
browser fetches, and no touch of the `data`-branch archive on the cron path.

## What the archive actually held (measured first, Aug 18)

Before designing anything, a full pass over the 9.5 archive — 119 snapshots,
Jul 8 → Aug 18, **147 MB**, parsed in 33 s:

- Cron health: median inter-run gap **7.7 h** vs the 8 h target; 7 missed slots
  of ~123 → **94% slot uptime** (the CelesTrak `continue-on-error` path working).
- 97 runs post-10.6-flip at full catalog: **16,072 → 16,344 objects**
  (+272 net = 344 arrivals, 71 departures).
- **984,577 conjunction records → 802,786 distinct events** (97% LEO 1, 3% LEO 2,
  299 deep-space); 46,499 with a final predicted miss **< 1 km**; 14,520 of
  16,416 objects flagged at least once.

**The finding this view exists to show:** re-screening the *same* encounter every
8 h on refreshed elements measures our own uncertainty. Consecutive-run
**|Δmiss| median 0.3–0.5 km, p90 ~5 km — and flat against lead time** (error
tracks *element age*, not how far ahead you look). Of predictions **< 1 km, only
70% were still < 1 km eight hours later; 8% had moved past 5 km.** That is the
empirical version of what `validation/sgp4_uncertainty.md` argues from citations
alone — our own operational output showing why we report geometry, never Pc.

## The structural constraint (the thing that would have been got wrong)

`_dedupe_to_unique_pairs` keeps **one event per pair per 24 h window** (the
closest), and co-orbiting Starlinks conjunct repeatedly — so as the window
slides, "the closest encounter for this pair" is a *different encounter*.
Matching on the pair alone splices unrelated approaches into one trail.

Measured on a real snapshot: 9,501 events, 9,501 unique pairs (confirming the
one-per-pair invariant), and matching consecutive runs by pair alone gives a
median |ΔTCA| of **22,597 s** — i.e. mostly different encounters.

→ The link key is **(unordered pair, TCA ± 900 s)**. Realized carry-forward:
**~16% of all events**, but **31–33% of the displayed top-500** — the closest
events are the sticky ones, which is exactly where the sparkline is wanted.
Max observed track depth **4** (so `hist` caps at 3 prior points).

⚠ **Do not "fix" the de-dupe to raise that rate** — the engine is CI-gated
byte-identical (10.6); a richer trail is not worth reopening it.

## Design: a self-propagating chain, not an archive scan

Each rebuild reads **only the previous `snapshot.json`** and carries `hist`
forward one run. Cost is O(current events) and constant in the archive's size,
so the cron stays ~10 min and the (147 MB, +3.6 MB/day) `data` branch is never
fetched. Reading the archive instead would put a ~150 MB clone on every cron run.

```
deploy.yml ──curl live snapshot.json──> prev-snapshot.json   (|| warn, non-fatal)
build_snapshot.py --prev ─────────────> build_snapshot(..., prev=…)
    └─> [_conjunction(e) …] ─> carry_forward_history(conj, prev_conj, prev_gen_at)
              match (pair, |ΔTCA| ≤ 900 s); hist = prev.hist + [[prev_run_epoch, prev.miss]]
snapshot.json  "hist": [[1787042904, 0.31], …]      (absent when there is none)
    └─> snapshot-data.js  history: c.hist || null
              └─> conjunctions.js historySparkline() → inline SVG, log y-axis
```

**Failure is graceful by construction.** Missing (first deploy), 404 (Pages
redeploying), truncated, or malformed `--prev` → a warning and no history; a
broken chain refills over the next ~3 runs. History is layered *on top of* the
snapshot and must never cost us the data refresh.

## Engineering decisions worth recording

- **`carry_forward_history` works on the COMPACT snapshot schema**, not on
  `run_screen` events. That makes it a pure snapshot→snapshot transform — unit
  testable with dict literals (no pandas, no screen) and reusable by a future
  archive backfill without rework.
- **`hist` stamps are epoch seconds, not ISO strings.** Compact, and it sidesteps
  the 9.3 Safari trap entirely (WebKit's `Date` rejects a non-3-digit
  fractional-second field that V8 accepts → silent NaN, invisible in local dev).
- **Stamp the previous RUN's time, not its TCA.** The sparkline's x-axis is "when
  was this predicted"; lead time is derived downstream as `(tca − run time)`.
  Stamping the TCA would collapse every point onto ~the same x. Test-locked.
- **Naive timestamps are read as UTC.** `datetime.timestamp()` on a naive stamp
  silently uses LOCAL time — hours of drift, and invisible in a UTC CI runner.
  Test-locked.
- **`hist` is ABSENT, not `null`/`[]`, when there is none** — the ~69% of events
  with no trail cost zero bytes.
- **`SCHEMA_VERSION` not bumped:** an additive optional field, and nothing (in
  particular no frontend code) gates on the version.
- **Flat-trail guard in the renderer:** if max/min < 3, the log domain is padded
  to a minimum span so a barely-moving trail (0.18 → 0.18 → 0.07) doesn't get
  auto-scaled into a dramatic-looking swing.

## Measured

Chain simulated across 7 consecutive real archived runs using the production
function:

| | |
|---|---|
| Carry-forward, all events | 15.3–17.6% (7.2% across one 16 h cron gap) |
| Carry-forward, displayed top-500 | **33.2%** (166 of 500) |
| Trail depth at steady state | `{1: 1022, 2: 695, 3: 10}` |
| Snapshot size | 1.441 → 1.455 MB gz = **+1.01%** |

Real trails: `HORS 1 × STARLINK-36599  2.42 → 2.40 → 0.11 km`;
`TIANMU-1 20 × STARLINK-34298  0.021 → 0.874 → 0.398 km` (a 41× swing).

## Files

| File | Change |
|---|---|
| `backend/core/snapshot.py` | `carry_forward_history` + `_pair_key` + `_epoch_seconds`; `build_snapshot(..., prev=)`; `HISTORY_MAX_POINTS`, `TCA_MATCH_WINDOW_S` |
| `scripts/build_snapshot.py` | `--prev PATH`, tolerant load, per-run carry-forward stats |
| `.github/workflows/deploy.yml` | fetch live snapshot → `prev-snapshot.json` on the REBUILD path (non-fatal); `--prev` on the build |
| `frontend/js/snapshot-data.js` | `history: c.hist \|\| null` |
| `frontend/js/conjunctions.js` | `historySparkline()` + render in each `.cd-row` |
| `frontend/css/style.css` | `.cd-hist` / `.cd-hist-cap` |
| `tests/test_snapshot.py` | `TestCarryForwardHistory` (14) + 3 in `TestBuildSnapshot` |

## Verification

- **607 passing + 4 skipped, 3× flake-clean** (baseline was 590, +17). Written
  test-first: the suite failed to import before the implementation existed.
- **CLI end-to-end** on the cached `starlink_shell` (44 conjunctions), all four
  paths: no `--prev` → 0 trails; valid `--prev` → 44/44; **missing** file →
  warning + build succeeds; **truncated** JSON → warning + build succeeds.
- **Frontend headless** (Playwright, real 16,344-sat snapshot): 166/500 events
  render a sparkline, geometry checked against the emitted SVG (log mapping,
  lead-hour labels, red final point), **0 console errors**.
- **Reuse-path regression** — the one real risk: a frontend-only push redeploys
  in ~1 min *reusing the live snapshot*, which has no `hist`. Verified against
  the pre-9.10 snapshot (16,030 sats, no `hist` anywhere): 14 detail rows across
  12 satellites, **0 sparklines, 0 console errors** — the block is omitted, not
  broken.
- Fixed during the round: `preserveAspectRatio="none"` was stretching the SVG
  labels ~1.3× horizontally in the 260 px panel. Now uniform scale (`height:auto`).

**Not yet verified:** a real cron firing. The chain's first live rebuild reads a
`hist`-less snapshot, so trails start at one prior point and fill to four over
~24 h. Two-point sparklines on day one are expected, not a bug.

## Deferred → 9.11

The standalone `/uncertainty` stats page (the 70%/8% headline + a
closest-approaches-ever table). `hist` is designed to support it with **no schema
change**. *Dropped from scope:* the bucketed |Δmiss|-vs-lead bar chart — it
tested badly for legibility in mockup review; the same fact reads better as one
sentence.

**Backlog raised by this review:** the `data` archive grows ~3.6 MB/day
(~1.3 GB/yr) — worth a prune/downsample policy (drop the bulky re-fetchable
`satellites` OMM block beyond N days; the screen output is *not* reproducible
after the fact). Added to the roadmap backlog.

---

## Review round (3review.md, same day)

Four findings, all fixed; two mutation-proven. Plus one open plan question closed
with measurement rather than left as a judgement call.

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 1 | Med | **`historySparkline` dereferenced `snapshotMeta` unguarded** — and it runs *inside* the `.cd-row` template, so a null would throw mid-render and take down the **whole detail panel**, not just the chart. `showConjunctionsFor` guards the same global two lines earlier (for `window_hours`), so this was an inconsistency, not a judgement call. | Added `|| !snapshotMeta` to the early return. **Mutation-proven:** removing the guard reproduces `TypeError: Cannot read properties of null`. |
| 2 | Med | **Inherited trails were validated only as `isinstance(list)`, not per point.** Because the trail is *copied forward every run*, one malformed point would ride the chain for `HISTORY_MAX_POINTS` runs and reach the browser each time. Realistic source is schema drift (a future point-shape change meeting an older snapshot), not corruption — a truncated download fails JSON parsing first. | New `_clean_trail()` validates each point to `[finite number, finite number]`. +2 tests; **mutation-proven** (reverting to the `isinstance` check fails `test_malformed_trail_points_are_dropped_not_propagated`). |
| 3 | Perf | **TCA parsed for every current event** though only ~17% can possibly match — `datetime.fromisoformat` + `.timestamp()` was the loop's dominant cost. | Parse only when the pair actually recurs. **379 ms → 80 ms per call at 10k events (4.7×)**, byte-identical output (1,727 trails both ways). |
| 4 | Clarity | `hit[0]/[1]/[2]` needed mental mapping at 4 use sites; a redundant `list()` around an already-copying `+`; a genexp nested inside a listcomp in `_clean_trail`; `p` as a record name. | Unpack to `prev_tca, prev_miss, prev_trail`; drop the `list()`; plain loop; rename to `prev_rec`. |

**Open question from the plan, now closed with data.** The plan flagged "a panel
could show many sparklines" and proposed falling back to focused-row-only if it
looked noisy. Measured across **all 866 conjunction participants** in a real
snapshot: the **worst panel has 3 rows / 3 sparklines** (332 rendered in total,
no NaN in any SVG, 0 console errors). Focus isolation keeps panels small, so the
simple render-in-every-row approach stands — **no expand/collapse state needed**.

**Verified not a problem (checked, no change made):**

- **`prev-snapshot.json` is not published.** It lands at the repo root; the Pages
  artifact uploads `frontend/` only.
- **NaN in the log scale.** A miss of 0 (possible — `miss_km` rounds to 3 dp)
  would give `log10(0)`; the existing `Math.max(min, 0.001)` clamp already covers
  it. Probed directly: current-miss-0 renders fine, all-zero omits.
- **Ordering.** `carry_forward_history` preserves the closest-first order
  `run_screen` produces, so the frontend's `slice(0, 500)` still takes the
  closest — verified on real data.
- **The fetch-failure fallback path.** When the catalog fetch fails the build is
  skipped and the live snapshot is redeployed as-is (with whatever `hist` it
  already had), and it is deliberately not re-archived. No interaction.

**Left as-is deliberately:** the `pts.length === 1` ternary in `x(i)` is
unreachable (`pts` is always ≥1 history point + the live value), but it guards a
division by zero for ~nothing, so it stays as insurance if the shape changes.

**Result: 609 passing + 4 skipped, 3× flake-clean** (+2 review tests). Frontend
re-verified after the JS change: 25 panels exercised, no throw, 0 console errors.

---

## Review round 2 (3review.md)

Round 1 covered the code; this round went after the **integration and the failure
modes that produce no error at all**. Two findings, plus four properties verified
by construction.

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 5 | Med | **A broken link would silently and permanently delete the feature.** If `a`/`b`/`tca`/`miss_km` ever drift, `carry_forward_history` matches nothing, `hist` vanishes from every snapshot, and the only trace is one line in a green cron's log that nobody reads. The site keeps working, so no alarm ever fires. | `build_snapshot.py` emits a **`::warning::` annotation** when the rate is *exactly* 0 against a prev of ≥1000 conjunctions. Warn-only — history is cosmetic and must never block a data refresh. Verified both ways: fires on a deliberately unmatchable 10,252-conjunction prev, silent on a healthy run (56.8%). |
| 6 | Low | **The test fixture could drift from the real schema.** Every history test fed `_cj()`, a *hand-written copy* of `_conjunction()`'s output. Rename `miss_km` in production and the fixture would keep the old name, the tests would stay green, and the site would quietly lose history. | Added `test_round_trips_real_conjunction_output_not_just_the_fixture`, which builds the previous run through the real `_conjunction()`. **Mutation-proven:** renaming the `tca` key now fails 3 tests including this one. |

**Verified by construction (no change needed):**

- **Idempotent against a stale prev.** If a deploy fails to publish, the next
  rebuild reads the *same* live snapshot again. Because each run rebuilds the
  trail from `prev` rather than appending to its own, re-running is a no-op —
  asserted equal on a real 10k-event pair.
- **Self-reference is bounded.** Feeding a snapshot its own output 8× leaves the
  max trail at 3 (`HISTORY_MAX_POINTS`) — no runaway, so `--prev X --out X` is
  safe.
- **The chain survives a frontend-only deploy.** The REUSE path downloads the
  live snapshot (trails included) and republishes it byte-for-byte, so
  `generated_at` and `hist` both carry over and the next rebuild links against
  them correctly.
- **The gz budget is untouched** — `_GZ_BUDGET_BYTES` is 5 MB; the snapshot is
  1.46 MB after the +1.01%.

⚠ **Operational note:** `scripts/build_snapshot.py` is in `ci.yml`'s path filter,
so pushing this change fires the **full engine A/B gate (~40 min)** on top of
pytest. That is the designed behavior — the gate guards screening code, which
this change does not touch, so it should pass — but expect the slow CI run.

**Result: 610 passing + 4 skipped, 3× flake-clean.**

---

## Testing round (4test.md)

Most of `4test.md` does not apply here — 9.10 has no propagation, unit
conversions, frames, or SGP4 to cross-validate, and the Python side was already
covered (20 tests, 4 mutation-proven, CLI verified on all four `--prev` paths).
More pytest would have been padding. **One gap was real, and it was on the
frontend**, for two reasons:

1. **The `hist` ↔ `c.hist` contract crosses a language boundary with nothing
   locking it.** `test_snapshot.py` locks the Python half; rename either side and
   the sparkline silently disappears from the site — no error, no failing test.
2. **The sparkline math was verified only by ad-hoc probes** — which the
   scratchpad wipe had already destroyed once. `historySparkline` is the most
   logic-dense new code (log axis, flat-trail guard, zero-clamp, point filter),
   and every one of those guards prevents a *silent* failure: NaN coordinates
   render as an invisibly broken chart, not an exception.

**Added `tests/test_9_10_sparkline.js` — 22 checks, exit-code-gateable.** Follows
9.3's harness pattern: it `extract()`s the functions from the **real** frontend
sources by name (throwing if one is renamed), so it fails when production drifts
rather than testing a stale copy.

### It immediately found a real bug in my own defensive code

The trail filter used the **global `isFinite`, which coerces**:

```js
isFinite(null) === true      // Number(null) === 0
isFinite("")   === true
isFinite([])   === true
```

So a `[null, missKm]` point *passed* the "is this a valid point" filter and was
plotted at a nonsense lead time (`null * 1000` → epoch 0 → ~496,000 h before
TCA). Fixed to `Number.isFinite`, which does not coerce, and made the sibling
`tcaMs`/`genMs` guard consistent. Python's `_clean_trail` (finding 2) means this
could not reach production *today*, but the JS filter is the defensive boundary
and it did not do what it claimed.

### Every sparkline guard is now mutation-proven

| Mutation | Check that fails |
|---|---|
| `Number.isFinite` → coercing global `isFinite` | "only the valid point survives (2 dots)" |
| flat-trail guard removed | "flat trail is NOT stretched to full height" — *added this round; the FIRST version of the harness went 20/20 green against this mutation* |
| `Math.max(..., 0.001)` zero-clamp removed | "a zero live miss does not produce NaN" |

The middle row is the point of mutation testing: 20/20 green did not mean the
guard was locked. The check now asserts the guard's actual contract — a
near-flat trail must use **less** than the full plot band, while a genuine ~100×
swing must use **all** of it (so the guard can't just be flattening every chart).

**Verified after the fixes:** 22/22 Node checks; pytest **610 passing + 4
skipped**; browser re-verified across all 866 participants — 332 sparklines, no
NaN, no throw, 0 console errors (identical to before the JS changes).

**Not wired into CI, deliberately.** `ci.yml`'s path filter is engine code, so it
would not fire on a frontend-only change; `deploy.yml` runs on `frontend/**` but
does no testing. Wiring it means changing trigger topology — a bigger decision
than this task. Run it with `node tests/test_9_10_sparkline.js`. Same
"candidate for a CI contract check" status as 9.3's harness, except this one is
committed rather than left in session scratch.
