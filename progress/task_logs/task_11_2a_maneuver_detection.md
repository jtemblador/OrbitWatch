# Task 11.2a: Maneuver detection as a label (production)

**Date:** Nov 17, 2026 · **Status:** done (not yet deployed; first deploy is seeded from the archive)
**Spec / oracle:** `progress/week11_planning/maneuver/` (spec.md, detector.py, impact.py)

## What shipped
| Piece | Where |
|---|---|
| Incremental, causal detector: faithful port of `detector.py` (n / i / EPOCH only; drag-aware expected Δa capped at 0; thr = max(100 m, 6·scale_a); LOWER also beyond 1×\|expected\|; plane steps; 3-step warm-up; <3 min re-fits merged; 6-flag trend re-learn; one-run confirmation). Per-object state is a dataclass of a few numbers, so a run is O(objects) | `backend/core/maneuvers.py` |
| Lossless columnar state codec (`encode_state` / `decode_state`, ints for epochs, floats at full repr), per-row validation, never raises | same |
| `annotate_conjunctions` → per-event `mnv_h` (hours since the latest confirmed maneuver of either object, 0.1 h, only when ≤ 48 h) + `mnv_who` (`a`/`b`/`ab`); absent otherwise | same |
| CLI `python -m backend.core.maneuvers replay <dir> [--last N] [--state-out F]` (archive replay / seed) | same |
| `apply_maneuvers` + `build_snapshot(prev_maneuver_state=…)`; `meta.maneuvers` telemetry (objects, armed, carried, confirmed, tagged_events, cold_start, or error) | `backend/core/snapshot.py` |
| `--prev-maneuver-state` / `--maneuver-state-out` (default `maneuver_state.json` next to `--out`), all warn-only | `scripts/build_snapshot.py` |
| Fetch the live sidecar; re-publish it on the REUSE and fallback paths; **seed it from the newest 180 archive records when missing** | `.github/workflows/deploy.yml` |
| "&lt;name&gt; maneuvered N h ago" tag in the conjunction detail (trust line), tooltip: prediction may be stale + no tag ≠ no maneuver + 100–300 m floor at 3x/day | `frontend/js/{snapshot-data,conjunctions}.js`, `css/style.css` |

Annotation only: the labels go on copies of the compact records after the screen; the screened events (what the A/B gate compares) are untouched (test-locked).

## Design choices
**Where the state lives: a sidecar file, not snapshot.json, not the archive.** I measured the state over the real archive at ~2.1 MB raw / **0.75 MB gz**, not the spec's 150–200 KB estimate. The epochs and the full-precision floats don't compress well. That would have added ~40% to the 1.86 MB gz snapshot every visitor downloads, and the browser never needs it. Two changes:
- `lm` (last-maneuver epoch) is pruned once it is older than the 48 h tag window. This is lossless for the labels (test-locked against an unpruned replay) and brings the state to **1.8 MB raw / 0.65 MB gz**.
- The state is written to `frontend/maneuver_state.json`, which Pages publishes and the cron reads back the same way it reads `snapshot.json` (`LIVE_MANEUVER_STATE_URL`). The frontend never fetches it.

The **per-visit cost is only the event fields: +14.6 KB gz (+0.8%)** on a real Oct 28 snapshot with 18.7% of events tagged. I left the state out of the archive record because it is a pure function of the archived `elements` (the replay reproduces it). The archive still keeps the `mnv_h`/`mnv_who` labels.

**Seeding: from the archive, not a cold start.** Cold starts are measured below. A cold start gives no labels for ~2 days, then over-tags while the per-object noise scales settle: at day 4–5 there are up to 2× the warm count of tags (Oct 24: 3,105 cold vs 1,527 warm). Because of that, `deploy.yml` replays the newest **180 records (~60 days)** whenever the live sidecar is missing (the first deploy, or a broken chain). It does this with the same `git fetch --depth=1 origin data` the archive step already runs every cron. The step is skipped while the chain is intact and is `continue-on-error`, so a failed seed just means a cold start.

Convergence of a seed vs the full-history state, labels at the latest run (recall / extra):

| History | 60 d | 30 d | 14 d | 7 d | 3 d |
|---|---|---|---|---|---|
| Recall | 100% | 98.8% | 94.7% | 93.6% | 56.5% |
| Extra tags | 0 | 8 | 73 | 298 | 401 (vs 863 warm) |

**Out-of-order sets are ignored.** A set whose EPOCH is ≤ the last one seen for that object is dropped (1.28% of archive sets, mostly operator-ephemeris vs 18 SDS fits arriving late, or source switches). The prototype instead sorted the whole history by EPOCH, which uses hindsight. That is the only behavioral difference (see validation).

Objects that are absent from a run keep their state for up to 30 days (`MANEUVER_STATE_RETAIN_D`).

## Validation on the real archive (374 snapshots, Jul 8 – Nov 17, slim + full)
The production path was replayed run by run, with the state pushed through JSON between every run as the cron does: **169,484 confirmed maneuvers, 19.4 s of detector time for 374 runs** (~50 ms/run).
- **vs the prototype's `flags.parquet` as-is:** 167,389 identical (id, epoch, confirming run); 2,960 only in the oracle and 2,095 only in production (~1.7%).
- **Cause, verified:** I re-ran the oracle on the same input with the 47,380 out-of-order arrivals removed (1.28%, on 14,076 objects). The result was **169,484 == 169,484, symmetric difference 0**. Every difference is the hindsight sort.
- Per group (oracle → production): Starlink 125,843 → 125,089, Kuiper 10,030 → 10,023, Qianfan 6,032 → 6,008, OneWeb 812 → 807, Globalstar 67 → 67, FLOCK 30 → 29, LEMUR 8 → 8, Iridium 6 → 6, other 27,499 → 27,425.
- **ISS:** exactly 2 confirmed. The maneuver sets are 2026-08-28 15:03 (confirmed at the Aug 29 11:40 run) and 2026-09-26 00:18 (confirmed Sep 26 17:15). **CSS:** Sep 28, Oct 25 (the marginal −104 m), Nov 14.
- **Passive objects: 0 flags each** on Calsphere 1/2/4A, LCS 1, Rigidsphere 2, Starlette, Stella, Ajisai, LARES, SwissCube and HST.
- Tagged share: mean 10.4% of events at 48 h and 3.7% at 24 h since Jul 16 (spec: 10.5% / 3.8%). Latest run: 863 of 14,073 (6.1%) at 48 h and 3.2% at 24 h.

## Tests
- `tests/test_maneuvers.py` (**+50**). The headline is oracle equivalence: a seeded 36-object × 150-run sequence goes through the production chain and through `detector.py`. The sequence covers passive objects, raising, lowering, plane changes, flip-flop, merges, storm-like fast decay and late joiners. The two must confirm the same maneuvers at the same runs (282 confirmations; 454 raw flags, so confirmation is exercised), plus 3 more seeds. The rest:
  - unit tests (reboost, flip-flop, passive, warm-up, drag cap, BETA, stale/same epoch, merge, plane)
  - epoch parsing
  - codec lossless + malformed
  - annotate (48 h cutoff, who, purity)
  - snapshot wiring: the chain reproduces the oracle; labels equal an unpruned replay; screened fields unchanged; old snapshot as prev; malformed state never fatal; detector failure costs only the label; retention; archive keeps labels not state; strict JSON
  - CLI seed equals the incremental state, a corrupt record is skipped, `--last`
- `tests/test_snapshot.py` (+2) runs through the real `main()`: the sidecar is written next to `--out`, absent from snapshot.json, and carried run to run; junk/missing state files are not fatal.
- `tests/test_11_2a_maneuver_tag.js` (Node, **28 checks**) on the real frontend sources. `test_11_0_trust_fields.js` now loads the tag helpers (still 22/22).
- **Mutation-checked:**
  - Python, 10/10 bite: no drag cap, no confirmation, no BETA rule, scale updated on the first quiet step, no trend re-learn, lossy state encoding, no re-fit merge, no 48 h cutoff, absent objects dropped, same-epoch re-step. The BETA mutation initially *survived*; I added the storm-decay fixture kind and the unit case, and it now bites.
  - JS, 5/5 bite: unescaped name, adapter accepting any `mnv_who`, coercing `isFinite`, tooltip dropping the "no tag ≠ no maneuver" caveat, a label alone not rendering.
- Full suite: **753 passed, 5 skipped** (was 701 + 5), offline.

## Frontend verification
Headless Chromium (swiftshader, vendored Cesium / satellite.js, route-mocked snapshot) on the worktree frontend:
- **Labeled Oct 28 snapshot** (17,132 events, 114 of the displayed 500 tagged): selecting STARLINK-3955 shows "STARLINK-3955 maneuvered 36 h ago" with the full tooltip, and there are 0 console errors.
- **Pre-11.2a snapshot:** no tag and 0 errors.

Screenshot: scratchpad `mnv11/shot_snapshot_mnv.json.png`.

## Not verified / limits
- The `deploy.yml` changes (fetch/re-publish the sidecar, archive seed) pass actionlint but **have not run on GitHub**. The seed step was exercised locally only as its CLI half (5 records incl. a corrupt one), not the `git ls-tree`/`git show` half against the real `data` branch.
- Pushing touches `scripts/build_snapshot.py` and `backend/`, so `ci.yml` will run its full ~40-min A/B gate. That gate is expected to pass, since screening is untouched.
- The spec's known limits stand:
  - Δa below ~100–300 m (most small avoidance burns) is invisible.
  - There is a one-run confirmation lag.
  - No phasing / RAAN / e maneuvers are detected.
  - A thrust-driven lowering goes quiet after 6 flags (re-learned as the trend).
  - Low cubesats can show storm FPs.
  - GEO station-keeping has not been evaluated. GEO events are labeled if detected, not excluded.
- 11.0d's "tier drops one level" coupling is not implemented, because 11.0d does not exist yet.
- Archive quirk: the Oct 23 17:54 / 23:51 records are full-format re-serves of an older snapshot. Their stale epochs are ignored by design.
