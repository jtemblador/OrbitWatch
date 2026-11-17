# Task 11.2b — Continuous SOCRATES agreement in the cron (Nov 17, 2026)

**Status:** done on a worktree branch, not pushed. **Live behavior is unverified**:
the sandbox cannot reach celestrak.org (the proxy returns 403), so the real SOCRATES
download, its parse at full size, and the agreement numbers on real data will be
seen for the first time on the first cron run on `main` after merge. Everything
below was built and tested offline. The real-scale numbers come from archived
snapshots and a synthetic 148k-row run.

## What it does
Every cron rebuild now scores its new screen against CelesTrak's latest SOCRATES
run. The result goes in `meta.socrates`, which `archive_record` keeps because it
copies `meta`. The validation that 8.2/8.3 ran once becomes a trend over the 3×/day
archive. A step change in the trend flags a problem: schema drift, an engine
regression, or a SOCRATES outage or stale run.

**This is not accuracy against truth.** SOCRATES runs the same SGP4 method on *its
own* element sets, which are hours older than ours. 8.3 measured what that costs:
on the current feed only **20–33 %** of SOCRATES events were reproduced, against
**89–100 %** once the elements were epoch-matched via `gp_history` (ΔTCA/Δmiss
= 0.000). So most disagreement here is **epoch drift**: different elements give a
different miss, sometimes a different closest pass, and an encounter can move across
the 0.4 km / 5 km cuts. A 60 % overlap means "two same-method screens on elements a
few hours apart overlap 60 %". It does not mean "we are 60 % accurate". The UI
tooltip says the same.

## The comparison definition (why each restriction exists)
SOCRATES uses a 7-day window, a 5 km sphere, payloads vs the **full** catalog,
every pass, and its own elements. We use 24 h, SFS ellipsoids, CelesTrak `active`
payloads only, and one event per pair. Both directions are therefore restricted to
the slice where the two screens **must** agree when the elements are identical:

| Restriction | Applies to | Why |
|---|---|---|
| both NORAD ids in our **screened** set | both | SOCRATES's debris/R-B secondaries are outside our population |
| TCA in the overlap of our window and the SOCRATES run (first..last TCA) | both | the other screen didn't look there |
| overlap trimmed by the 600 s match tolerance at each end (denominators only; the candidate pool is the untrimmed overlap) | both | a reference near the edge could have its partner just outside the window, which would make a false miss |
| **SOCRATES → ours:** one row per pair (the closest), `range ≤ 0.4 km`, `v_rel ≥ 0.5 km/s` | reference | we keep one event per pair (`_dedupe_to_unique_pairs`). 0.4 km is the smallest SFS semi-axis (LEO 1–4 radial), so a miss that close is inside **every** handbook ellipsoid in any RTN orientation (test-locked against `screening_volumes`). A 3 km SOCRATES row can lie outside our 0.4 km radial axis and still be correct. Below 0.5 km/s our 7.2 co-located suppression may drop a same-launch pair by design |
| **ours → SOCRATES:** our events with miss `< 5 km` | reference | SOCRATES's own threshold. The candidates are *every* SOCRATES row in the window (all passes) |

Matching reuses 8.2's `match_events`: unordered pair + TCA within 600 s, one-to-one,
closest TCA wins. So the delta stats and DSE buckets mean exactly what they mean in
the validation report. For our side, "DSE" is computed the same way (element epoch
→ TCA, days, max of the two objects).

Known residual unfairness, left on purpose and documented here:
- **5 km boundary (ours → SOCRATES).** If our miss is 4.9 km and theirs is 5.1 km,
  the event counts as unmatched. That is drift, not method, and it pushes the rate
  down a little.
- **Population edge.** SOCRATES's "active payloads" list may differ slightly from
  CelesTrak `active` on a given run. Our event between two objects SOCRATES did not
  treat as primaries would then be unmatched.
- **Closest-pass choice.** If two passes of a pair are nearly tied, SOCRATES's and
  our "closest" can be different passes. `n_pair_flagged` (same pair at any TCA)
  separates this case from "never flagged".

## Schema: `meta.socrates` (optional, new key)
```
{ "version": 1, "status": "ok" | "no_overlap" | "unavailable",
  "origin": "celestrak" | "file", "socrates_fetched_at", "socrates_rows",
  "socrates_first_tca", "socrates_last_tca",
  "match_window_s": 600, "threshold_km": 5, "in_volume_km": 0.4, "slow_km_s": 0.5,
  "overlap": {"start", "end", "hours"},
  "n_socrates_in_scope", "n_ours_in_scope",
  "socrates_to_ours" | "ours_to_socrates": {
      "n", "n_matched", "rate", "n_pair_flagged",
      "median_abs_dtca_s", "median_abs_dmiss_km",
      "by_dse": {"<1d"|"1-3d"|">3d": {"n", "n_matched", "rate"}}},
  "seconds" }
```
- `rate` is `null` when `n = 0`, so an empty bucket never reads as 0 %.
- `unavailable` blocks carry only `version`, `status`, `reason` (the exception
  type) and `seconds`. A failed run leaves a gap with a reason in the trend instead
  of a missing row.
- A stale SOCRATES run (its TCAs don't reach our window) gives `no_overlap` plus a
  `::warning::`.

## Non-fatal contract (mirrors `--prev` and `annotate_horizon`)
`build_snapshot.py --socrates` (live) / `--socrates-csv PATH` (offline file, implies
on). `_socrates_block` wraps the fetch, parse, match, **and a `json.dumps(...,
allow_nan=False)` of the block** in one `try`. The final snapshot dump is
`allow_nan=False`, so a NaN that slipped into the block would otherwise kill the
whole refresh at the very last step. Any failure records the `unavailable` stub,
emits `::warning::SOCRATES agreement unavailable (...)`, and the site publishes.
`SOCRATESFetcher.fetch()` falls back to its Parquet cache on a failed download.
A fresh CI runner has no cache, so a failure there raises and is caught. In local
dev a stale cache shows up as `socrates_fetched_at` / `no_overlap`, not silently.
`deploy.yml` passes `--socrates` on the rebuild step. actionlint-clean.

## Cost (bounded)
- **Network:** one GET of `sort-minRange.csv` (~16 MB at ~148k rows). `download_text`
  with the default `attempts=1` makes no retry ladder. The bound is the requests
  timeout (30 s per connect/read) plus one curl fallback (`--max-time 30`, 40 s
  subprocess cap). The step was already doing a `curl -I` of the same file for the
  schedule-alignment log.
- **CPU:** parse **0.63 s** for a 148k-row / 16.3 MB CSV. Match **0.37 s** for a
  synthetic 148k-row run (7-day span, half debris secondaries) against the real
  14,073-event Nov 17 screen (10,432 SOCRATES / 2,371 ours in scope). Under ~2 s
  in total, against a ~10 min cron.
- **Size:** the block is ~1 KB of compact JSON per snapshot and archive record.

## Offline exercise on real data (archived snapshots as a SOCRATES proxy)
The proxy: an **earlier** archived snapshot's < 5 km events, written as SOCRATES
rows (DSE from its own element epochs), scored against the Nov 17 03:54 snapshot
(14,073 events). This is a plumbing and scale check with realistic epoch drift, not
a SOCRATES measurement. The proxy side is SFS-filtered, not a 5 km sphere, so the
ours → "SOCRATES" rate is depressed by the volume mismatch on top of drift.

| Proxy run (age vs ours) | overlap | SOCRATES → ours | ours → SOCRATES | median \|Δmiss\| (s→o) |
|---|---|---|---|---|
| Nov 16 22:56 (~5 h older) | 18.7 h | 117/195 = **60 %** (135 pair-flagged) | 1116/1501 = **74 %** | 0.000 km |
| Nov 16 13:40 (~14 h older) | 9.4 h | 23/94 = **24 %** (29) | 286/626 = **46 %** | 1.22 km |
| Nov 16 01:02 (~27 h older) | none | `no_overlap` | — | — |

The ~5 h row has median Δ = 0 because ~half of consecutive re-predictions reuse
identical elements (the 11.0e correction). Agreement falls sharply as the
element-age gap grows, which is the 8.3 epoch-drift finding seen from the other
side. Expect the live numbers to sit in this range and to move with the cron's lag
behind SOCRATES's upload (cron delay median 4–4.6 h, measured in 10.10).

## Frontend (cheap, optional item: done)
`conjunctions.js` `socratesAgreementText(meta)` puts **"SOCRATES overlap 60% · 74%"**
in the freshness line, between "updated X ago" and the validation-report link. The
tooltip names both directions and says "consistency trend, not accuracy against
truth". It renders only for `status == "ok"` with both rates finite and `n > 0`.
Only `Math.round`ed numbers that passed `Number.isFinite` reach `innerHTML`, so a
string rate is not coerced. Pre-11.2b snapshots (the REUSE path) render exactly as
before. Verified headless in Chrome against a snapshot with the block and one
without (Cesium + satellite.js served from their npm tarballs, since the CDN is
blocked here): correct markup in both, **0 page errors**.

## Tests
- `tests/test_socrates_monitor.py` (+11, pure): identity (100 % both ways, zero
  deltas), in-volume-only reference, `IN_VOLUME_KM` == the smallest SFS semi-axis,
  per-pair collapse, population and slow-pair scoping, the 5 km threshold, the edge
  trim, stale run → `no_overlap`, empty run, drift as deltas vs unmatched
  (`n_pair_flagged`), our-side DSE buckets with `rate: null` on empty buckets.
- `tests/test_snapshot.py::TestBuildSnapshotMain` (+4, through the real `main()`):
  off by default; a CSV round-trip of a real screen event comes back reproduced
  (ΔTCA < 10 ms); a missing CSV costs only the block (stub + warning, snapshot
  published); a NaN in the block is caught by the wrapper, not at the final dump.
- `tests/test_11_2b_socrates_line.js` (9 Node checks on the real `conjunctions.js`,
  not in CI, like the other frontend harnesses).
- **Mutation-checked: 6 of 6 bite** (each reverted): drop the in-volume cut
  (2 fail), drop the NaN dump guard (1), drop the per-pair collapse (1), drop the
  population filter (1), drop the edge trim (1), coerce the frontend rate with
  `Number(r)` (2 Node checks fail).
- Full suite: **716 passed, 5 skipped** (701 + 15).

## Unverified until the first cron run on main
1. The real CSV download from the runner, and its size and time.
2. The real agreement levels. Expectation: tens of percent, moving with the lag
   between SOCRATES's run and ours.
3. Whether SOCRATES's primary list matches CelesTrak `active` closely enough that
   ours → SOCRATES isn't dominated by population edges. If it is, the next step is
   to restrict our side to pairs whose objects appear as SOCRATES participants in
   that run.
4. Note: `scripts/build_snapshot.py` is in `ci.yml`'s path filter and in
   `deploy.yml`'s engine-change list. Merging this fires the ~40-min A/B gate once
   (expected to pass: the screen itself is untouched).

Follow-ups: the trend chart belongs to 11.1d (stats page), which can read
`meta.socrates` from the archive. No schema change is needed.
