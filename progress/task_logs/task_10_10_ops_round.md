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

_Pending — filled in when the local 32,816-object screen finishes._

## Not done (decisions)

- Rewriting the `data` branch to slim the 318 pre-10.10 full snapshots (force-push).
- Debris screening in production (see measurement above).
