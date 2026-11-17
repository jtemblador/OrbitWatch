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
| Re-predictions reusing identical elements | **49.4%** (Δmiss p99 0.006 km; one outlier 7.9 km) |
| Fresh-element \|Δmiss\| | median **0.644 km**, p90 6.0 km, 41% > 1 km, 12.5% > 5 km |
| …by element age | 0.22 km (≤ 6 h) → 1.27 km (1–2 d) |
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

Actual-volume re-screens reproduce the archived Starlink–Starlink **pair sets exactly** on both
dates. A fixed box grows *faster*; archive-wide RTN counterfactual agrees (×2.11 actual vs ×2.80
held). **Verdict: crowding, not the box** (9,002 of ~10.8k Starlinks were already LEO 1 in July).
Written up in `docs/findings/starlink-shell-lowering.md` ("Is it the screening box? No.").
The forced-LEO-1 runs (a third policy) were still running at write-up time — a cross-check only;
the verdict doesn't depend on them.

## Notes / follow-ups
- Two archive scripts now exist: `scripts/analyze_archive.py` (10.10, feeds the Starlink write-up
  and `archive_trends.csv`) and `scripts/archive_analysis.py` (11.1). Candidate to consolidate in
  the post-Phase-11 review.
- Run-to-run instability is tracked by element age, so the per-event age field (11.0a) is the
  right UI signal; the < 1 km count is dropped from the figures.
