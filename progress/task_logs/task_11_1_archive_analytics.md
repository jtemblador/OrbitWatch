# Task 11.1 (a–c) + 11.0e — Archive analytics, three figures, the box confounder, doc corrections

**Date:** Nov 17, 2026
**Status:** DONE. 11.1d (stats page on the site) not started.
**Tests:** +24 (`tests/test_archive_analysis.py`, offline, two compiled-in mutations each turned tests red); full suite 673 passed / 5 skipped after the 11.3 merge.

## Why
An analyst-perspective review of the 4-month `data`-branch archive (374 snapshots, Jul 8 – Nov 17) asked
three questions the site couldn't answer: how much do predictions move run to run, what drove the
~2× rise in conjunctions per run, and how old are the elements behind a flagged event.

## What shipped
- **`scripts/archive_analysis.py`** — offline CLI over an extracted archive directory
  (`git fetch origin data && git archive FETCH_HEAD | tar -x -C DIR`). Emits
  `docs/findings/archive_metrics.json` + three figures; reruns are byte-identical. The
  same-constellation rule is a faithful port of `frontend/js/snapshot-data.js` `constellationOf`.
  The 22 capped 5k-object runs (Jul 8–14) are excluded.
- **Figures** (`docs/img/`): `archive-dmiss-vs-age.png` (|Δmiss| vs element age, lead-time
  companion), `archive-events-by-class.png` (stacked by pair class), `archive-element-age.png`
  (event element age vs catalog max). These replace the session's first-pass
  `plot_archive_trends.py` / `archive_trends.png`, now deleted.
- **`scripts/box_confounder.py`** — re-screens one archived catalog's Starlinks under actual /
  held-LEO-2 / forced-LEO-1 volumes with the production engine.
- **11.0e doc corrections** — dated notes (original text kept) in CLAUDE.md, `key_information.md`,
  the 9.10 task log; `validation/sgp4_uncertainty.md` §4b; README states the debris / maneuver /
  MEO-HEO limits plainly.

## Measured
| Metric | Value |
|---|---|
| Re-predictions reusing identical elements | **49.4%** (Δmiss ≈ 0: p99 0.006 km; 121 of 332,354 > 0.1 km and 16 > 1 km, max 7.9 km — likely a different encounter linked within ±900 s) |
| Fresh-element \|Δmiss\| | median **0.644 km**, p90 6.0 km, 41% > 1 km, 12.5% > 5 km |
| …by element age | 0.22 km (≤ 6 h) → 1.27 km (1–2 d); flat-to-lower beyond 2 d (0.99 / 0.93 km, small bins) |
| …by lead time | 0.50 km (4–8 h) → 0.84 km (20–24 h) — **weak, not flat** (corrects the draft) |
| < 1 km predictions re-matched on fresh elements | 56% stay < 1 km, 11% move > 5 km |
| Growth by pair class (week Jul 15–21 → Nov 10–17 medians) | Starlink–Starlink 5,154 → 10,866 (×2.11, 60% → 76% share); Starlink–other 2,986 → 2,869; other–other 426 → 480 |
| Event element age (older of the pair) | median 0.85 d, p95 1.71 d, 0.4% > 3 d — vs catalog max 16.9–30.4 d |

## The box confounder (11.1c)
Below 500 km perigee a Starlink gets the LEO 1 box (~3.6× the LEO 2 volume), so lowering could
inflate counts by itself. Re-screen, 24 h, production engine, Starlinks only:

| Policy | Jul 16 | Oct 29 | Growth |
|---|---:|---:|---:|
| Actual volumes | 4,670 | 12,219 | ×2.62 |
| Held in LEO 2 | 1,191 | 4,910 | ×4.12 |
| Forced into LEO 1 | 4,817 | 12,221 | ×2.54 |

Actual-volume re-screens reproduce the archived Starlink–Starlink **pair sets exactly** on both
dates. A fixed box grows *faster*; archive-wide RTN counterfactual agrees (×2.11 actual vs ×2.80
held). **Verdict: crowding, not the box** (9,002 of ~10.8k Starlinks were already LEO 1 in July).
Written up in `docs/findings/starlink-shell-lowering.md` ("Is it the screening box? No.").
Forced-LEO-1 (every Starlink in the big box) grows ×2.54 — essentially the actual ×2.62, so a
fixed box of either size leaves the rise intact.

## Notes / follow-ups
- ~~Two archive scripts now exist~~ — consolidated in **11.1e** (below): `scripts/analyze_archive.py`
  is gone; its measures are `archive_analysis.py trends`.
- Run-to-run instability is tracked by element age, so the per-event age field (11.0a) is the
  right UI signal; the < 1 km count is dropped from the figures.

## 11.1e — one archive script (Nov 17)
`scripts/analyze_archive.py` (10.10) is deleted; its measures are now the `trends` mode of
`scripts/archive_analysis.py`, on the same loader as the metrics mode.

- **CLI:** `archive_analysis.py [metrics|trends] (DIR | --ref REF) [--until UTC] [--min-sats N]`.
  `metrics` is the default (the 11.1 `archive_analysis.py DIR` form still works). Both input
  sources are supported for both modes: an extracted directory, or a git ref read blob-by-blob with
  `git cat-file` (no extraction). `--until` takes a timestamp; a bare date means end of that day.
- **Shared pieces:** `list_snapshots` / `read_snapshot` / `elements_frame` (both archive shapes),
  `is_starlink` (= `constellation_of(name) == "STARLINK"`), `mean_altitude` (factored out of
  `orbit_shape`), one `--min-sats` population (10,000; the same 22 capped Jul 8–14 runs drop out
  of both modes). Trends-mode pieces are pure and tested: `band_labels`/`band_counts`,
  `trend_frames`/`trend_rows`, `track_objects` (now returns a dict), `trend_pivot`,
  `crowding_exponent`.
- **Starlink definition:** the word-boundary family rule replaces `startswith("STARLINK")`.
  Measured over all 374 archived snapshots: **0 names** where the two rules disagree, so it
  **moves no number**. Conjunction names now come from the snapshot's own element table (as in the
  metrics mode), with the archived `a_name`/`b_name` as fallback; no output changed.
- **Pin bug found and fixed:** the write-up's `--until 2026-10-29` matched dates, so it picked up
  the 12:26 and 18:59 UTC runs from Oct 29. Those runs came after the draft (Oct 29 06:52 UTC), and
  including them gives 298 runs and different numbers (e.g. 254 rather than 257 still at
  525–575 km, a last week of 11,938 rather than 11,896). The write-up is now pinned to
  `--until 2026-10-29T01:00` (last run 00:58), which reproduces **every number in the write-up
  unchanged**: 296 runs, 1,669 → 257, 6,099 / 3,268 / 2,161 / 334 + 336, 5,154 → 11,896 (×2.31),
  61% → 79%, exponent 2.03 with r = 0.94, 24 of 319 slots empty, last-week range 12,829–18,998.
- **Output identity:**
  - `archive_trends.csv` has the same row set as the committed file. Only the order changed:
    `value_counts` tie order had drifted between pandas versions, so bands are now written in
    altitude order. The old script, run here on the same 296 runs, matches the new one byte for
    byte (before the ordering fix).
  - The three write-up figures are pixel-identical (0 differing pixels) to the old script's
    output in this environment. The committed PNGs were kept: they were rendered with
    Helvetica/Arial on the author's machine, and this container only has DejaVu, so a re-render
    would change the font and nothing else.
  - `archive_metrics.json` and the three `docs/img/archive-*.png` regenerate byte-identically from
    both the directory and `--ref origin/data --until 2026-11-17T04:00`.
- **Tests:** +13 offline (`TestTrendMeasures` 8, `TestArchiveSource` 5: synthetic full and slim
  snapshots, a throwaway git repo for the ref path, and a CLI end-to-end where full ≡ slim row for
  row). Three mutations each turned tests red and were then reverted: `startswith` in place of
  the word-boundary rule, a bare-date `--until` meaning midnight, and dropping the band ordering.
  Full suite **714 passed, 5 skipped**.
