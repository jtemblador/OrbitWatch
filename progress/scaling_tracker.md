# Scaling Tracker

**Purpose:** Track code that works at Phase 1 (~30 sats) but needs attention before Phase 3 (~6,000 Starlink). Each entry has a location, what needs to change, and which phase forces the fix.

---

## Active Items

| # | File | Line | Issue | Fix | Phase |
|---|------|------|-------|-----|-------|
| 1 | `backend/core/propagator.py` | — | Per-point Python→C++ boundary in `get_all_positions` (rendering ~6,000 points/refresh) | ⚠ **Revised (6.1 measurement):** `propagate_batch` at the Python boundary is only ~1.05× a Python loop — sgp4 compute dominates and tuples are still built per-sat, so a "batch" call alone is NOT a big win. The O(N²) hot loop already lives entirely in C++ (6.3 medium filter). Remaining lever **only if rendering 6k points is slow**: NumPy bulk output + GIL release from `propagate_batch`. Re-profile before building. | Phase 7 |
| 2 | `backend/routers/satellites.py` | — | `POST /api/refresh` is synchronous — blocks until CelesTrak fetch completes; no scheduled refresh exists | Switch to **202 Accepted + background task** (POST returns a job id, client polls status) so a 6k-sat fetch+parse (5–10 s) doesn't block, **plus** a scheduled auto-refresh (lifespan asyncio task / APScheduler ~2–6 h) so data stays current with no manual step. Phase 1/6 is fine synchronous (30–300 sats). → roadmap **9.7**. | Phase 9 |
| 3 | `backend/core/conjunctions.py` | `run_screen` | Coarse→medium boundary round-trip: `coarse_filter` returns survivor `(i,j)` pairs to Python, immediately passed back to `medium_filter`. Within one dense shell survival ≈100% → millions of tuples cross C++↔Python twice (~378 ns/pair each way, measured at 6.2). | Fuse the coarse cut inside `medium_filter` (pass periapsis/apoapsis arrays + pad, let C++ skip non-overlapping pairs internally) so survivor pairs never materialize as Python objects. Marked with `# ⚠ PERF` at the call site. Fine at ≤300 sats. | Phase 7 |
| 4 | `backend/routers/satellites.py` | `get_conjunctions` | `GET /api/conjunctions` runs CPU-bound screening **synchronously inside the async handler** — blocks the event loop. `medium_filter` releases the GIL during its C++ scan, but the Python-side coarse round-trip + per-window `fine_filter` loop do not. At 6k sats / 72 h a screen could take many seconds, stalling all other requests. | Run the screen via `fastapi.concurrency.run_in_threadpool` (or a 202 + background-job pattern like refresh). Phase 1/6 is fast (30–300 sats, sub-2s). | Phase 7 |
| 5 | `backend/core/tle_fetcher.py` | `starlink_shell` | **Data staleness:** the `starlink_shell` shell is a hand-sliced *derived snapshot* (cache-only), so it does NOT auto-refresh — even `POST /api/refresh` serves the cached parquet. SGP4 error grows ~5–10 km/day from epoch, so a week-old shell drifts tens of km. Fine for the Phase-6 demo (current data); stale otherwise. Also: the app has **no scheduled refresh at all** yet (only manual `POST /api/refresh`), so every group ages until manually refreshed. | Two parts: (a) **screen the live `starlink`/`active` group directly** instead of a static slice, so the hand-built shell and its staleness go away → roadmap **7.0**; (b) general scheduled auto-refresh → roadmap **9.7** / item #2. Until then, rebuild with `python -m backend.core.tle_fetcher starlink-shell`. | 7.0 / 9.7 |

## Resolved Items

| # | File | Issue | Resolution | Date |
|---|------|-------|------------|------|
| 1 | `backend/routers/satellites.py` | `iterrows()` in `/api/satellites` | Replaced with `iloc[i]` index-based iteration | 2026-03-24 |
| 2 | `backend/core/propagator.py` | `iterrows()` in `_build_indexes()` | Vectorized with `dict(zip(...))` | 2026-03-24 |
| 3 | `backend/core/propagator.py` | `iterrows()` in `get_all_positions()` | Replaced with `iloc[i]` index-based iteration | 2026-03-24 |

---

## How to Use This File

- When you add a `# ⚠ PERF` comment in code, add a matching row here.
- When scaling to a new phase, scan this list and resolve items for that phase.
- Move resolved items to the Resolved table with a date.
