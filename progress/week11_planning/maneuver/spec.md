# 11.2a Maneuver detection: prototype findings and spec

**Recommendation: GO.** Ship it as a **label** ("an object maneuvered recently, so the prediction may be stale").
Do not use it as a filter. The tag is well calibrated against false positives (0 flags on 10 passive objects,
≤ 0.07 per object per 30 d on non-propulsive cubesats). It roughly **doubles to triples the run-to-run
|Δmiss|** of the events it marks, and the effect holds within every pair class and every element-age bin.
It covers only a minority of unstable events (13% of the >5 km jumps), so the UI must not imply
that untagged events are stable.

Data: the 374 archived snapshots (Jul 8 to Nov 17), giving 3,695,741 distinct (object, EPOCH) element sets
for 17,217 objects. Each set is attributed to the first run that saw it, so the replay sees exactly what
production would see. Scripts: `extract.py`, `detector.py`, `impact.py`, `figs.py` (this folder).

## Detector (per object, causal, needs only n / i / EPOCH, so it works from the slim-v1 `elements`)
`a = (μ/n²)^(1/3)` with n in rad/s and μ = 398600.4418. Each new distinct element set k is compared with the previous one:

| Quantity | Definition |
|---|---|
| expected Δa | `min(trend, 0) · Δt`. Drag can only lower a, so a learned positive trend never excuses a raise |
| residual | `res = Δa − expected` |
| threshold | `thr = max(100 m, 6 · scale_a)`, where `scale_a` = EWMA(α 0.2) of \|res\| on quiet steps, clamped to [5, 50] m (thr is therefore 100 to 300 m) |
| RAISE | `res > thr` |
| LOWER | `res < −max(thr, 1.0·|expected|)`. Storms and attitude changes can double drag decay |
| PLANE | `|Δi| > max(0.01°, 6 · scale_i)`, scale_i = EWMA of \|Δi\| clamped to [0.0005°, 0.003°] |
| trend | EWMA (α 0.1) of Δa/Δt on quiet steps with Δt ≥ 0.1 d. Flagged steps do **not** update trend or scale, so sustained thrust stays flagged. After 6 consecutive flags the trend is reset to the latest rate (handles a regime change) |
| warm-up | 3 quiet steps before the detector can flag (about 1 day of runs). Sets less than about 1 min apart are merged |
| **confirm** | The step is a maneuver only if the **next** set keeps the new level: `a_{k+1} − a_{k−1}` minus the expected change has the same sign, more than half the magnitude, and more than thr (the same test applies to i). This adds a one-run lag (≤ 8 h) and removes **24% of raw a-flags**. Those removed flags are source flip-flop: Starlink alternates between operator-ephemeris-derived fits (round-hour epochs) and 18 SDS fits that differ by ±300–500 m (see STARLINK-4795, Sep 7–19) |

Event tag: `maneuver_h` = hours since the most recent confirmed maneuver (post-burn set epoch) of either
participant, using only information known at the run. Show the tag when `maneuver_h ≤ 48`.

## Justification (measured)
- **Noise floor.** The 10 passive objects (Calsphere 1/2/4A, LCS 1, Rigidsphere 2, Starlette, Stella, Ajisai,
  LARES, SwissCube) have step residual p99 ≤ 3.6 m. HST at 471 km, with no propulsion, reaches p99 15 m and max 17 m,
  so the 100 m floor has about 6× margin. Starlink sets that are station-kept flat show step |Δa| p95 of 131 m and p99 of 245 m,
  so the per-object scale lifts their threshold to about 300 m.
- **False positives.** Passive objects: **0 / 2,807 steps**. LEMUR (Spire, no propulsion): 0.01 per object per 30 d.
  FLOCK (Planet, differential drag only): 0.07 per object per 30 d (35 confirmed flags across 112 sats, clustered on a
  few dates, which is consistent with geomagnetic-storm drag spikes). Before the `BETA·|expected|` rule, FLOCK was
  at 0.30. Iridium: 0.02.
- **True positives.**
  - ISS: **2/2** reboost-sized steps detected (Aug 28 +2.15 km, Sep 26 +1.93 km) and no other flags.
    The dates are not cross-checked against NASA notices.
  - CSS/Tianhe: +0.66 km (Sep 28) and +3.4 km (Nov 14), plus one marginal −104 m (Oct 25).
  - Starlink orbit raising, per step: >1 km steps 88% confirmed, 0.5–1 km 75%, 0.2–0.5 km 40%, <0.1 km 3%.
    Raising objects are tagged nearly continuously.
  - Rates in confirmed steps per object per 30 d: Kuiper 6.2, Qianfan 5.8 (both campaigns in orbit-raising),
    **Starlink 2.7 (94% of Starlinks ≥ 1)**, OneWeb 0.29, Globalstar 0.46, other 0.72.
- **Thresholds are not knife-edge.** Sweeping floor/K over 50/4, 100/6, 200/6, 100/10 and 300/8 (pre-confirm),
  the tagged-to-untagged ratio of median |Δmiss| stays between 2.0× and 2.9×. Passive objects stay at 0 in every
  configuration. Lower thresholds tag more events (20% at 50/4) and raise recall; higher thresholds raise contrast.

## Impact (the evidence the tag is worth showing)
The archive was relinked run-to-run on (pair, TCA ± 900 s), which gives 696k links. The analysis restricts to
**fresh-element re-predictions** (52%), because identical elements give Δmiss ≡ 0.

| Fresh re-predictions | n | median \|Δmiss\| | > 1 km | > 5 km |
|---|---|---|---|---|
| tagged, maneuver ≤ 24 h | 8,459 | **1.98 km** | 63% | **29.0%** |
| tagged, ≤ 48 h | 24,036 | **1.57 km** | 59% | **24.6%** |
| untagged | 336,013 | 0.62 km | 41% | 11.9% |
| SL-SL tagged / untagged (48 h) | 6.6k / 28.7k | 3.38 / 1.84 | 76 / 64% | 39.8 / 25.1% |
| SL-other | 15.0k / 252k | 1.36 / 0.73 | 56 / 44% | 20.6 / 12.6% |
| other-other | 2.4k / 55.5k | 0.28 / 0.21 | 26 / 15% | 7.8 / 1.8% |

The effect is not a proxy for element age. Median |Δmiss| for tagged vs untagged events by age bin:
≤ 0.5 d 1.34 vs 0.33, 0.5–1 d 1.49 vs 0.61, 1–2 d 2.38 vs 1.25, > 2 d 2.26 vs 0.95 km (fig 2).

How much of the event list gets tagged: **10.5%** of events at 48 h and 3.8% at 24 h (latest run 6.3% / 3.5%).
This is not overwhelming. Recall is limited: tagged events make up 6.7% of fresh re-predictions but
only **12.9% of the > 5 km jumps**. Most instability is ordinary element noise and age, which 11.0a/11.0d cover.

## Snapshot fields (additive, no screening change, A/B gate unaffected)
- `conjunctions[i].mnv_h`: hours since the latest confirmed maneuver of either participant, **present only when ≤ 48**,
  plus `mnv_who` ("a" | "b" | "ab"). The UI shows "maneuvered Xh ago, so the prediction may be stale" and the
  11.0d tier drops one level.
- `maneuver_state` (top level, columnar like slim `elements`), one row per object:
  `id, ep, a_m, i, trend, scale_a, scale_i, nq, nc, pend` (the pending unconfirmed step) and `last_mnv_ep`.
  This is about 17k × 11 values, roughly 150–200 KB gz (+~25% snapshot). It is carried forward through `--prev`
  exactly like 9.10's `hist`. Missing or malformed state means a cold start (3-run warm-up, tags absent for about 1 day).
  This is warn-only and never fatal. Add it to the slim archive record so it can be replayed.
- **Bootstrap:** replay the slim archive (it has n/i/EPOCH) once to seed the state. Do not wait for a cold warm-up.

**Cost:** O(N objects) per run. The full 4-month replay of 3.7 M sets took 30 s in pure Python, so a single run
is under 0.1 s. No network and no extra fetch.

## Failure modes / limits (state in the UI tooltip and docs)
1. **Sampling:** the detector sees only one set per run (3×/day), so maneuvers below about 100–300 m Δa, such as
   most small collision-avoidance burns of a few cm/s, are **invisible**. "No tag" does not mean "did not maneuver".
2. **Lag:** confirmation adds one run, and the maneuver is seen only once 18 SDS or the operator publishes a post-burn set.
   An event whose object maneuvers before TCA but after the run cannot be tagged. The hindsight version (maneuver
   confirmed at the next run) shows median |Δmiss| 2.45 km and 32% > 5 km.
3. **Source flip-flop:** without confirmation, about a quarter of flags would be noise. Keep confirmation even though it costs latency.
4. **Lowering under thrust** (for example Starlink's 550→480 km campaign) is learned as "trend" after the first
   6 flagged steps and then goes quiet. Raising never goes quiet, because of the clamp. This asymmetry is documented and accepted.
5. **Drag storms** can still trip LOWER on decaying cubesats below about 450 km (FLOCK 0.07 per object per 30 d).
6. **No e / RAAN / along-track (phasing) maneuvers.** The slim record has e, but e-steps were not needed and were not
   validated. RAAN is not stored. Pure phasing maneuvers that leave a unchanged are missed.
7. GEO station-keeping was not evaluated. GEO events are rare in the list, so leave GEO untagged initially or validate it separately.

Figures: `fig1_examples.png` (ISS reboosts / Starlink raising / FLOCK silent), `fig2_dmiss.png` (|Δmiss|
exceedance tagged vs untagged + age control), `fig3_rates.png` (per-group rates, FP check).
