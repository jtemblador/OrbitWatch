# Task 10.10 — Ops audit + hardening round (Oct 29, 2026)

**Status:** done, merged to `main`. Branch `overnight/ops-round-10-10`.

## Audit (Jul 8 – Oct 29)

| Check | Result |
|---|---|
| Cron slots | 337 of 339 ran; 0 failed builds since 10.9 (last failure Aug 16) |
| Stale refreshes | 16 of 229 runs since Aug 12 (7%) hit a CelesTrak outage and re-served the old snapshot; worst gap 31.7 h (Oct 22–23) |
| Cron start delay | median 2.0 h (Jul), 0.8 h (Aug), 4.0 h (Sep), 4.6 h (Oct); worst 7.9 h |
| Archive | 318 snapshots, 480 MB, ~4.5 MB/day |
| Analytics | repo 0 views / 14 d, 0 stars; no site analytics |
| Basemap | CARTO dark tiles now return "API KEY REQUIRED" to keyless requests — live globe affected |
| 9.10 | complete but uncommitted for 72 days |

## Shipped

1. **9.10 deployed** (prediction-evolution sparkline).
2. **Space-Track fallback** — `spacetrack_fetcher.fetch_catalog` + `refill_group_cache` (population = live snapshot's ids, ≥95% coverage, keeps CelesTrak names/types). `deploy.yml`: CelesTrak → Space-Track → re-serve-live; `force_spacetrack` dispatch input. Live-tested: 16,819/16,819 in 4 s.
3. **Slim archive record** — `snapshot.archive_record` (meta + conjunctions + columnar name/type/epoch/n/e/i): 0.88 MB vs 1.78 MB full.
4. **`meta.run`** — screen seconds, catalog source, run id/event, history %.
5. **"Hide same-constellation pairs"** — filtered before the 500 cap; swaps list, dots, worker mask, arcs. 15,312 → 3,058 pairs on the live snapshot.
6. **GoatCounter** — https://orbitwatch.goatcounter.com
7. **Basemap** — NASA GIBS Black Marble (keyless), brightness 1.8 / gamma 1.15.
8. **CI upkeep** — Node-24 action majors, runner pinned `ubuntu-24.04`.
9. **Write-up draft** — `docs/findings/starlink-shell-lowering.md` + `scripts/analyze_archive.py`.
10. Strays gitignored.

## Verification

- 624 pytest passing (+14), Node harnesses 22/22 + 9/9 (filter harness mutation-proven).
- End-to-end local build from the Space-Track cache: 16,829 conjunctions, 443 s, 3.1 GB peak, history 28.4%, citation in `meta.source`.
- CI gate on the branch (ubuntu-24.04, new actions): suite green, Tier 2 **8,402 == 8,402** byte-identical.
- Headless: filter (incl. paused toggle, focus teardown, Top-20 arcs), real-data sparklines (292/500 with trails, 0 NaN), basemap at globe + TCA zoom; 0 console errors.
- Independent code review of the branch: nothing serious. Noted (not changed): during a multi-day CelesTrak outage the fallback keeps the last CelesTrak population, so new launches wait for CelesTrak to return.

## Debris / full-catalog feasibility (measured, not shipped)

Full Space-Track catalog (32,816 objects: 18,955 payloads, 10,376 debris, 2,212
rocket bodies, 1,273 unknown), 24 h SFS screen, this Mac (16 GB, = the CI runner):
**did not finish.** After 3,701 s wall it had used 340 s CPU vs 2,345 s system
time — thrashing (16 GB swap + ~57 GB in the compressor); killed. At ~5.4e8
candidate pairs the engine's per-pair scan state alone is ~20 GB (the C++
reviewer's independent estimate agrees). **Debris screening needs a memory
redesign (streamed/blocked pair processing), not a bigger cron.** For contrast,
the active catalog (16.6k): 443 s / 3.1 GB locally.

## Review round 1 (full codebase, 5 parallel reviewers)

C++ engine / Python pipeline / CI+deploy / frontend / API+tests. **0 critical.**
Fixed (each confirmed in code first; tests red-before-green where testable):

| Area | Fix |
|---|---|
| Engine | NaN sieve anchor (osculating ecc ≥ 1) produced an empty window list → pair dropped unscanned (no-skip violation). Now a failed anchor → whole-window; NaN-safe guards; NaN fine rows survive; uint32 guards. Old vs new engine on the real 16.6k catalog: **8,412 == 8,412 events**, identical. |
| Frontend | XSS sink: catalog names into innerHTML unescaped (5 sinks) → `escapeHtml`; SRI on CDN tags; jump-to-TCA first batch at stale time; paused time edits didn't move dots; filter tick before load; Escape on focused checkbox; misleading "No conjunctions" text; arc rebuild per click. |
| Pipeline | No conjunction floor (0-event screen published green; 0 == 0 passed the A/B) → `--min-conjunctions` / `--min-events`; non-dict `--prev` was fatal; ages measured at generation; SOCRATES loop kept hitting CelesTrak after a 403; parser null-name crash; open gp_history window cached forever. |
| CI | Cron gate decision used commit dates (old-dated commits skipped the gate) → diff vs the live snapshot's `git_sha`; write token readable by pip/cmake → `persist-credentials: false` + step-scoped push auth; archive ran before publish was certain; CDN-cached snapshot could roll the site back; truncated CelesTrak didn't trigger the fallback; timeouts; deps pinned. |
| API / tests | Unlocked reads raced `/refresh`; unbounded screen cost; error text leaks; far-future time 500s. Two tests that could not fail, two tautological timing tests, print-and-return skips → real assertions. |

## Review round 2 (what round 1 missed)

Regression review of the round-1 diff + an adversarial tester in an isolated
worktree. Fixed: Space-Track refill sorted by epoch (gate slice ≠ normal run)
→ CelesTrak's NORAD order; the new gate decision failed OPEN on a `git diff`
error → fails closed. Measured Tier-1 floor margin: 2,165 events vs floor 50.

The adversarial tester reproduced 5 more (all fixed test-first): `/track`
resolved by NAME (599 shared names → another object's track); `--prev` still
fatal for some shapes (+ a last-resort rebuild without history); the stale
warning read the frozen age column; one malformed SGP4 field could kill the
Space-Track fallback or draw a fake equatorial orbit (parser now requires
finite SGP4 fields — drops 0 of 60k+ real objects); `max_points=0` kept all.
Held up: ~12k concurrent API requests incl. 759 reloads (0 errors, no deadlock),
every `--prev` junk shape, archive/history on the real 16.8k snapshot, the
20,000-step bound, all innerHTML sinks.

**Tests:** 610 → 645 pytest (+35), Node harnesses 22 + 12.

## Not done (decisions)

- Rewriting the `data` branch to slim the 318 pre-10.10 full snapshots (force-push).
- Debris screening in production (see measurement above).
