# Task 8.3 — SOCRATES validation report (the credibility headline)

**Date:** Jun 27, 2026
**Status:** DONE (Stage A current-GP report + Stage B `gp_history` epoch-matched lever)
**Tests:** 540 passing, 4 skipped (was 472) — +68 across 4 new files + 2 extended.

---

## Goal

Turn 8.2's comparison engine into the portfolio headline: a reproducible report
that states *what fraction of CelesTrak SOCRATES conjunctions our SGP4 screener
reproduces and how closely*, segmented by element age (`DSE`), across three
slices (ISS / top-N closest / Starlink). Then add the **lever** that proves the
method: pull each object's epoch-matched historical element set from Space-Track
`gp_history` and show reproduction lift from the current-feed baseline.

---

## Approach

- **Two stages.** Stage A = current-GP baseline (the newest elset per object —
  what the free feed gives, which has rolled past the epoch SOCRATES used).
  Stage B = the lever: fetch the *epoch-matched* historical elset (epoch
  ≈ `TCA − DSE`) so we screen the **same elements** SOCRATES did.
- **Reuse the 8.2 orchestrator unchanged.** `BulkGPAdapter` presents a
  pre-fetched GP frame through `compare_against_socrates`'s `fetch_by_catnr(nid)`
  seam — with epoch targets it returns each object's nearest-epoch elset, without
  it the latest. So Stage B required *no* change to the screen/match/epoch_ok core.
- **Pure formatters + headless plots, one live runner.** Report rendering
  (`socrates_report.py`) and figures (`socrates_plots.py`) are pure/offline and
  unit-tested; `scripts/validate_socrates.py` is the only live glue.
- **Space-Track auth = cookie session, no API key.** Creds from a gitignored
  `.env` (`SPACETRACK_USER`/`SPACETRACK_PASS`), auto-loaded; rate-limited
  (<30/min); `gp_history` immutable → Parquet-cache forever.
- **`--current-source spacetrack` escape hatch.** CelesTrak's TLS was blocked on
  the dev VPN; sourcing the current baseline from Space-Track's `gp` class let
  the full report run anyway (Space-Track reachable, CelesTrak not).

---

## Implementation

| File | Change |
|------|--------|
| `backend/core/spacetrack_fetcher.py` | **new** — `SpaceTrackFetcher` (cookie login, throttle, `fetch_history`/`fetch_latest`, `_coerce_numeric`), `BulkGPAdapter` (nearest-epoch / latest through the fetch_by_catnr seam) |
| `backend/core/socrates_report.py` | **new** — pure Markdown formatters (summary, by-`DSE`, per-event sample, current-vs-matched comparison, full report) |
| `backend/core/socrates_plots.py` | **new** — matplotlib (Agg) figures: reproduction-by-`DSE`, current-vs-matched grouped bars, |ΔTCA|/|Δmiss| hists, ours-vs-SOCRATES scatter |
| `backend/core/socrates_compare.py` | added `build_epoch_targets` (per-object `TCA − DSE`) + `_mean_datetime` |
| `scripts/validate_socrates.py` | **new** — runner: 3 slices, `--epoch-matched`, `--current-source`, `--epoch-pad-days`, built-in `.env` loader |
| `requirements.txt` | + `matplotlib` |
| `validation/socrates_report.md` + `validation/figures/*.png` | **new (generated)** — the committed artifact |
| `tests/test_spacetrack_fetcher.py`, `test_socrates_report.py`, `test_socrates_plots.py`, `test_validate_socrates.py` | **new** — 63 offline tests |
| `tests/test_socrates_compare.py` | + `TestEpochTargets`, + Stage-B integration test |

---

## Validation

**Live, current-GP → epoch-matched (the headline):**

| slice | current GP | epoch-matched | matched ΔTCA / Δmiss |
|------|-----------|---------------|----------------------|
| ISS (single primary) | 3/9 (33%) | **8/9 (89%)** | 0.000 s / 0.000 km |
| Top 25 closest | 8/25 (32%) | **25/25 (100%)** | 0.000 s / 0.000 km |
| Starlink (40 closest) | 8/40 (20%) | **40/40 (100%)** | 0.000 s / 0.000 km |

On the **same elements SOCRATES used**, our screener reproduces every matched
conjunction to the displayed precision (e.g. ISS×TIANMU-1: 3.249 vs 3.249 km).
Current-feed reproduction is 20–33 % *purely from epoch drift* (`epoch_ok=0`
everywhere current; `=9/25/40` epoch-matched). The grouped by-`DSE` figure shows
current collapsing at >3 d age while epoch-matched holds ~full.

**Offline:** 540 passing / 4 skipped. **Mutation-checked** the load-bearing fix —
without `_coerce_numeric` the parser drops every Space-Track record (0 rows vs 1),
reproducing the live bug; the test asserts `==1`, so it provably bites.

---

## Test coverage

| File / class | Covers |
|------|--------|
| `TestCoerceNumeric` (6) | all-string→numeric; `EPHEMERIS_TYPE "0"→0`; empty/invalid left; end-to-end parse succeeds only with coercion |
| `TestBulkGPAdapter` (7) | nearest-epoch / latest selection, missing→empty, helper-col dropped, naive-tz target, numpy-int key lookup |
| `TestAuth` (5) | no-creds guard, success, 401, "failed" body, **password never in error** |
| `TestQueryRetry` (2) | 401 → re-login → retry; HTTP error raises |
| `TestFetch` (3) | `fetch_history` parse + immutable cache hit; `fetch_latest` uses `gp` class; empty-ids no query |
| `TestEpochTargets` (4) + Stage-B integration (1) | `TCA − DSE` averaging; adapter selection drives `epoch_ok` end-to-end |
| `socrates_report` (17) | `_f` −0.0 normalization, stats/rate/pair, summary/events/comparison, `format_report` (error / Stage-A / Stage-B / matched_error) |
| `socrates_plots` (12) | each plotter render/skip-empty, `render_all` rel-paths, `compare_to` lead-figure switch |
| `validate_socrates` (9) | `.env` loader (inline-comment strip, quoted-`#` kept, no-override), `_distinct_ids` |

---

## Lessons learned

- **Space-Track `format/json` is ALL strings** (`"MEAN_MOTION": "15.49"`), unlike
  CelesTrak's real JSON numbers. The killer: `EPHEMERIS_TYPE` arrives `"0"`, and
  `"0" != 0` is `True`, so `_parse_json` silently skips **every** record. Coerce
  numerics (`_coerce_numeric`) before reusing the parser. Caught only by the live
  run — pure offline tests using CelesTrak-shaped (numeric) records would miss it.
- **Space-Track has no API key.** Auth is a cookie session (POST to
  `/ajaxauth/login`, reuse the cookie). The thing to "get from the account page"
  doesn't exist — it's just username + password (→ gitignored `.env`).
- **`gp_history` "1/lifetime"** in their bandwidth table = *pull a given query
  once and cache it* (history is immutable), NOT a one-shot lifetime quota. Real
  request throttle is <30/min, <300/hr.
- **CelesTrak TLS can be blocked on a VPN** (`SSL_ERROR_SYSCALL`) while
  Space-Track stays reachable — hence `--current-source spacetrack`, sourcing
  both current and historical from the one auth'd endpoint.
- **A low reproduction number, explained, is a strength.** Current-feed 20–33 %
  with `epoch_ok=0` + the by-`DSE` curve reads as rigor (we *measured* epoch
  drift), and the epoch-matched 89–100 % proves the method. The honest framing is
  the headline, not a weakness to hide.

---

## Deferred / remaining risk

- **`epoch_eps_days = 0.05 d` (1.2 h):** if an object's nearest `gp_history`
  elset is >1.2 h off target, `epoch_ok=False` despite best-effort — honest, not
  a bug.
- **Comparison denominators** can differ if current vs epoch-matched runs drop
  different missing objects (shown as `m/n`, so visible). Didn't occur live.
- **Live HTTP path** (real login + query) is exercised by the opt-in skipped test
  + the manual runs, not in CI (by design — no network in CI).
- **8.4** (SGP4-uncertainty doc) is next — the report's caveats + by-`DSE` curve
  feed it directly.

---

## Function reference

```python
# backend/core/spacetrack_fetcher.py
SpaceTrackFetcher(user=None, password=None, cache_dir=…)   # env creds; cookie login
  .fetch_history(norad_ids, epoch_start, epoch_end, force=False) -> df  # gp_history, cached
  .fetch_latest(norad_ids) -> df                                        # class/gp (current)
BulkGPAdapter(gp_df, epoch_targets=None)   # fetch_by_catnr seam: nearest-epoch | latest

# backend/core/socrates_compare.py
build_epoch_targets(socrates_df) -> {norad_id: datetime}   # per object, TCA − DSE

# backend/core/socrates_report.py
format_report(sections, generated_at, mode_note) -> str    # pure Markdown
comparison_to_markdown(current, matched, label_a=…) -> str

# backend/core/socrates_plots.py
render_all(summary, results, figures_dir, slug, compare_to=None) -> [rel_paths]

# scripts/validate_socrates.py
#   python scripts/validate_socrates.py --epoch-matched [--current-source spacetrack]
```
