# Task 10.3 — Formal Stage-1 validation + re-profile on fresh catalogs

**Date:** Jul 9, 2026
**Status:** DONE — Stage 1 (fused + sieve) formally validated on catalogs it was
never built against; production flip cleared for 10.6 (after 10.4/10.5).
**Tests:** 568 passing + 4 skipped (no new tests — this phase *re-runs* the
existing locks on fresh data; the property suite is 10.5).
**Wall clock:** measurement batch 55m 37s (+ ~9 min fetch/suite prep — per-step
times below; the two slowest steps run the *baseline* path the sieve exists to kill).

---

## Goal

10.2 proved the C++ time sieve byte-identical on the catalogs it was developed
against. 10.3 is the independence check: fetch catalogs the sieve has **never
seen**, re-run the event-identity gate at every scale, and produce the
same-day classic-vs-Stage-1 profile rows (5k/10k/full) that 10.6 will cite
when flipping production. This is also the standing check for 10.2's residual
risk (three-sample node-curvature aliasing): new elements, new geometry, new
node swings.

## Fresh data (the point of the phase)

- **CelesTrak is TLS-blocked on this network** (the 8.3 failure mode, both
  IPv4/IPv6, requests + curl). Fallback: **one Space-Track full-catalog `gp`
  query** (`/class/gp/decay_date/null-val/epoch/>now-30`, their recommended
  bulk pattern — 36 MB, 31,885 objects), coerced via `_coerce_numeric` and
  parsed by the canonical `GPFetcher._parse_json`, then sliced:
  - **active** = the *same 15,928 NORAD ids* as the cached CelesTrak active
    group (population definition preserved, 0 missing) with fresh elements —
    **median epoch age 0.54 d** (was ~12 h–stale mix from Jul 8);
  - **starlink** = `OBJECT_NAME.startswith("STARLINK")` → 10,722 sats
    (**+230 new / −52 deorbited** vs the Jun-22 cache; the newest are the
    Jul-2 launch batch still orbit-raising at ~305–313 km — exactly the
    drag-heavy class that stressed the 10.1b margins).
- Full suite re-run on the fresh caches: **568 passed in 15.3 s** — this
  re-executes `test_cpp_anchors_match_python_oracle` (C++ anchors ≡ oracle on
  the fresh CI catalog) and every sieve/fused/scale lock on new data.

## What shipped

**`scripts/ab_screen.py`** — the Stage-1 A/B harness, promoted from 10.2's
scratch script to a committed CLI because 10.6's CI flip runs exactly this:
load one catalog → screen twice (baseline vs fused+sieve) → diff the sorted
event lists with `==` → **exit 0 iff byte-identical** (CI-gateable), with a
set-level diff printed on failure. `--baseline classic` (production today) for
the CI point; `--baseline fused` for full catalogs (classic materializes the
~5 GB Python pair list — measuring memory pressure, not the sieve; fused ≡
classic is already test-locked from 10.1a).

## Results — event identity (the gate), all on fresh data

| Catalog (operating point) | Events off == on | Medium wall | Step time |
|---|---|---|---|
| CI point 4,821 (SFS 24 h/30 s, classic vs Stage-1) | **312 == 312** | 109.2 → 20.5 s (5.3×) | 4m 09s |
| Full Starlink 10,722 (EUC 51 km 3 h/30 s) | **312,042 == 312,042** | 132.0 → 31.7 s (4.2×) | 5m 10s |
| Full active 15,709 (SFS 6 h/30 s) | **4,445 == 4,445** | 350.0 → 68.4 s (5.1×) | 15m 32s |

## Results — same-day profile rows (fresh catalog, start 2026-07-09T07:00)

| Row (active, SFS) | t_total | medium | fine | peak RSS | events | Step time |
|---|---|---|---|---|---|---|
| classic 4,821 / 24 h | 146.8 s | 93.0 | 53.0 | 1.72 GB | 312 | 2m 31s |
| **Stage-1 4,821 / 24 h** | **69.7 s (2.11×)** | 18.9 | 50.8 | **1.32 GB (−23%)** | 312 | 1m 14s |
| classic 9,795 / 24 h | 874.9 s | 488.0 | 382.9 | 8.69 GB | 3,171 | 14m 46s |
| **Stage-1 9,795 / 24 h** | **393.5 s (2.22×)** | 73.8 | 319.7 | **6.47 GB (−26%)** | 3,171 | 6m 42s |
| **Stage-1 15,709 / 6 h (full)** | 323.7 s | 68.8 | 254.9 | 5.27 GB | 4,445 | 5m 33s |

The classic re-baselines reproduce 10.0 (CI point 157 s/1.7 GB → 146.8/1.72
here; 10k 852 s/8.7 GB → 874.9/8.69) — same operating point, fresh elements,
so the Stage-1 comparisons are same-day, same-catalog, not cross-epoch.

**Reading the numbers:**
- **Post-sieve, fine is 73–81% of the cascade** (50.8/69.7 at 5k; 319.7/393.5
  at 10k; 254.9/323.7 full) — 10.4's case, measured three ways.
- **Medium wall speedup (4.9–6.6×) ≠ the −41× pair-step work reduction** from
  10.1b: the sieved scan still propagates every used sat every step (the v1
  deliberate floor, ~flagged refcount follow-up). Don't conflate the metrics.
- The sieve also *drops* 1–2% of medium windows (e.g. 967,784 → 946,305 at the
  CI point) with zero event change — the cross-shell spurious windows 10.2's
  two-shell fixture locks.
- **Full active at 24 h still doesn't fit pre-10.4** (fine dicts extrapolate
  ~13 GB vs 11 GB free): the full row ran at 6 h deliberately. Lifting the cap
  needs the streamed C++ fine stage — unchanged conclusion, now re-measured.
- Cross-checks internal to the batch: P5's sieved window count equals AB3's
  B-run exactly (4,262,792); profiler event counts equal the A/B counts at
  every scale.

## Residual-risk check (from 10.2)

The three-sample node-curvature aliasing concern required "huge relative
precession AND tiny sin I_R simultaneously" to bite. Fresh catalogs = new
plane geometry across 15,928 + 10,722 objects including 230 never-before-seen
sats: **0 event differences anywhere**. The risk stays theoretical; the next
scheduled check is 10.6's in-CI A/B (which runs on that day's live fetch).

## Findings

- **CelesTrak-blocked fallback is now a pattern, not an improvisation:** one
  Space-Track bulk `gp` query + population-preserving slice through the
  canonical parser refreshes any group cache (scratch: `st_refresh.py`,
  session scratchpad; promote to `tle_fetcher` only if it recurs).
- **`ru_maxrss` is process-wide**, so ab_screen's RSS line is dominated by the
  baseline (run A) — profiler rows are the per-path memory source of truth.
- **Time the long runs** (user feedback, saved to memory): every step is
  `date -u`-stamped in the runner; report per-step wall time + ETA as results
  land, and state the expected batch duration before launching.

## Function reference

```bash
# The 10.6 CI A/B (exit 0 iff events byte-identical):
python scripts/ab_screen.py --source active --mode sfs --max-sats 5000 \
    --hours 24 --step 30                      # classic vs fused+sieve
python scripts/ab_screen.py --source active --mode sfs --hours 6 \
    --step 30 --baseline fused                # full catalog, memory-safe
# Profile rows:
python scripts/profile_screening.py --source active --mode sfs --sizes 5000 \
    --hours 24 --step 30 --start <now> [--fused --sieve]
```
