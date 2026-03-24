# Scaling Tracker

**Purpose:** Track code that works at Phase 1 (~30 sats) but needs attention before Phase 3 (~6,000 Starlink). Each entry has a location, what needs to change, and which phase forces the fix.

---

## Active Items

| # | File | Line | Issue | Fix | Phase |
|---|------|------|-------|-----|-------|
| 1 | `backend/core/propagator.py` | — | 6,000 individual Python→C++ boundary crossings in batch propagation | Add `orbitcore.sgp4_batch(satrecs, tsinces)` C++ function that loops in native code and returns all positions in one crossing. Requires new pybind11 bindings + moving coordinate transforms to C++ (or returning TEME bulk). Est. ~3-5x speedup at 6k sats. | Phase 3 |
| 2 | `backend/routers/satellites.py` | — | `POST /api/refresh` is synchronous — blocks until CelesTrak fetch completes | Switch to **202 Accepted + background task** pattern: POST returns immediately with a job ID, fetch runs via FastAPI `BackgroundTasks` or Celery, client polls `GET /api/refresh/status/{id}`. At 6k Starlink sats, fetch + parse could take 5-10s — too long to block a request. Also add scheduled auto-refresh (cron/APScheduler every 6-8h) so no client request ever directly triggers a CelesTrak fetch in the critical path. Phase 1 is fine synchronous (30 sats, <2s fetch). | Phase 3 |

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
