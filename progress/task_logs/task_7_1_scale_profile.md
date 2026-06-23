# Task 7.1 — Scale to dense LEO (profile-first)

**Date:** Jun 22, 2026
**Status:** DONE
**Tests:** 386 passing, 1 skipped (was 381) — +5 (timings hook + harness smoke)

---

## Goal

Phase 7.1 is the roadmap's "scale to dense LEO" step. We scoped it **profile-first**
(confirmed with Jose): *measure* the full cascade (coarse → medium → fine → RTN) as the
catalog grows toward the whole Starlink set, find where the cost concentrates, and pick the
demo operating point — so the Phase 7.2 / 7.3 optimizations are driven by numbers, not
guesses. We deliberately did **not** optimize here (no C++ change, no threadpool) — those
are 7.3, now scoped by these measurements.

---

## Approach

- **A passive timing side-channel, not a parallel benchmark.** `run_screen()` takes an
  optional `timings` dict; when provided it records per-stage wall time (`t_coarse` /
  `t_medium` / `t_fine`) and counts (`n_sats` / `n_pairs` / `n_windows` / `n_events`).
  `None` (the default, and what the API endpoint passes) → no measurement, byte-identical
  result. So the profile measures the *real* production path, with zero behavior change.
- **A reusable harness over the real catalog.** `scripts/profile_screening.py` sweeps N over
  the on-disk Starlink parquet (densest-shell slices via `max_sats`, or the full multi-shell
  catalog with `--full`), or a deterministic `--source synth` shell offline. It prints a
  per-stage table and the same harness is 7.3's before/after benchmark.
- **Measure, don't assume.** The roadmap assumed the coarse filter would "eliminate the
  large majority of pairs." We tested it on the real 10,544-sat catalog instead.

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/conjunctions.py` | `run_screen(..., timings=None)` — optional dict filled with per-stage times + counts; early-return (no-pairs) path sets all keys. Refreshed the `# ⚠ PERF` note with the measured survivor numbers. No behavior change when off. |
| `scripts/profile_screening.py` *(new)* | Offline profiling harness: `--source starlink|synth`, `--sizes`, `--full`, `--hours/--threshold/--step`; per-stage table + peak RSS. Missing-data guard points to `--source synth`. |
| `tests/test_conjunctions.py` | `TestRunScreenTimings` (4) + `TestProfileHarness` (1). |

---

## The profile (the deliverable)

Real Starlink catalog (`backend/data/tle/starlink.parquet`, 10,544 objects, epochs Jun 8–13).

**Coarse-filter survival — does it eliminate the majority? No.** (standalone, full catalog)

| pad | survivors | % of 55.6 M pairs |
|-----|-----------|-------------------|
| 5 km | 10.5 M | 18.9 % |
| 25 km | 25.2 M | 45.3 % |
| 50 km (endpoint default) | 27.3 M | 49.1 % |

**Per-stage sweep** (intra-shell slices of the densest 43°/~475 km shell; seconds):

| window | N sats | pairs | windows | events | t_load | t_coarse | t_medium | t_fine | total | peak RSS |
|--------|-------:|------:|--------:|-------:|-------:|---------:|---------:|-------:|------:|---------:|
| 24 h / 50 km | 300 | 44,850 | 14,743 | 1,588 | 0.10 | 0.00 | 0.47 | **2.10** | **2.6** | 182 M |
| 24 h / 50 km | 500 | 124,750 | 44,155 | 4,872 | 0.10 | 0.02 | 1.15 | **6.31** | **7.5** | 211 M |
| 24 h / 50 km | 800 | 319,600 | 120,381 | 14,271 | 0.16 | 0.06 | 3.18 | **18.20** | **21.4** | 279 M |
| 3 h / 25 km | **10,544 (full, cross-shell)** | 25.2 M | **1.43 M** | 124,810 | 2.01 | 5.99 | 28.23 | **223.51** | **257.7** | **4.5 GB** |

**Three findings:**

1. **Coarse altitude-band filtering is weak for a megaconstellation** (45–49 % survive, not a
   majority cut). It's *inclination-blind* — Starlink stacks its 43° (3,264 sats), 53°
   (2,824) and 97° (515) shells all into ~475 km, so co-altitude survival is high regardless
   of plane. The 25 M survivor tuples are the **memory** driver (4.5 GB), which is what the
   7.3 C++ fusion (scaling_tracker #3) actually buys back — less so wall time.

2. **The fine stage is the bottleneck — 82–87 % of wall time at every scale**, driven purely
   by window count (14.7 k → 1.43 M), each window a Python `scipy.minimize_scalar`. This
   promotes the roadmap's "watch the fine stage" footnote to the *primary* 7.3 target, and
   makes **7.2 (de-dupe + co-located/persistent-proximity suppression) a major performance
   lever** — fewer windows → less fine work — not just output cleanup.

3. **Operating point: keep the interactive demo at `MAX_SATS=300`** (2.6 s @ 24 h/50 km —
   the profile validates the existing default). ~500 is the click-and-wait ceiling (7.5 s);
   the full catalog (258 s / 4.5 GB) is **batch-only** until 7.2 + 7.3.

---

## Validation

- **No-op guarantee:** events identical with `timings` on vs off (`off == on`, 109 events on
  the seeded stations catalog; codified in `test_timings_do_not_change_result`).
- **Full suite:** 386 passing, 1 skipped — offline/deterministic. No cross-validation needed
  (pure timing instrumentation; no new orbital math).
- **Harness exercised** on the real catalog (the tables above) and the offline synth path.

## Test coverage

| Test | Covers |
|------|--------|
| `TestRunScreenTimings.test_populates_every_key` | all 7 keys present, `n_sats` correct, times are non-negative floats |
| `TestRunScreenTimings.test_timings_do_not_change_result` | the byte-identical no-op guarantee (on == off, non-vacuous) |
| `TestRunScreenTimings.test_counts_match_result` | `n_events == len(events)`, `n_windows >= n_events`, `n_pairs == 1` |
| `TestRunScreenTimings.test_no_pairs_early_return_still_populates_zeros` | coarse-cut early return fills every key, un-run stages at 0 |
| `TestProfileHarness.test_profile_one_synth_returns_wellformed_row` | harness `_profile_one` synth path; guards against `run_screen` signature drift |

---

## Lessons learned

- **Profile before optimizing — the assumed bottleneck was the wrong one.** The tracked
  scale item (#3, the coarse→medium boundary) turned out to be the **memory** driver (4.5 GB),
  while the **time** went almost entirely to the **fine stage** — a one-line footnote in the
  roadmap. Building the C++ coarse fusion first (the obvious move) would have spent effort on
  ~12 % of the wall time.
- **Altitude-band coarse screening is inclination-blind**, so it barely culls *within* a
  constellation (one altitude → ~all co-altitude pairs survive). It only earns its keep on a
  mixed-altitude catalog (LEO→GEO). For dense LEO, the medium filter — and the fine stage —
  carry the load.
- **7.2 is a performance lever, not cosmetics.** Co-located/persistent-proximity suppression
  + de-dupe to unique pairs directly cut the window count that dominates the fine stage.
- **Buffered stdout hides progress** on a multi-minute run piped through `grep` — use
  `python -u` for live rows.

---

## Remaining risks (tracked)

- **Full-catalog screening is batch-only** (258 s / 4.5 GB) until **7.2** (window reduction)
  and **7.3** (C++ coarse fusion → memory; fine-stage batching → time; `run_in_threadpool` →
  event loop). scaling_tracker #3/#4 + a new #6 (fine-stage Python loop).
- **Footgun:** a plain `ORBITWATCH_GROUP=starlink` run with no `MAX_SATS` screens all 10,544
  → a 258 s / 4.5 GB synchronous hang on the first `/api/conjunctions`. Fix = scaling_tracker
  #4 (cap / `run_in_threadpool` / warn) → 7.3. Flagged, not patched (profile-first scope).
- `resource.getrusage` is Unix-only (fine — linux/Docker target).

---

## Function reference

```python
# Passive profiling hook — None (default) is a no-op; the API passes None.
run_screen(satrecs, meta, start_utc, duration_hours, threshold_km,
           step_sec=60.0, pad_km=None, timings=None) -> list[dict]
# timings (if a dict) gets: n_sats, n_pairs, n_windows, n_events,
#                           t_coarse, t_medium, t_fine

# Harness (from project root):
#   python scripts/profile_screening.py                 # default sweep, offline
#   python scripts/profile_screening.py --full          # + full 10,544-sat run (slow)
#   python scripts/profile_screening.py --source synth   # deterministic, no data file
#   python scripts/profile_screening.py --hours 24 --threshold 50 --sizes 300,500,800
```
