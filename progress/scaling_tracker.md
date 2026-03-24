# Scaling Tracker

**Purpose:** Track code that works at Phase 1 (~30 sats) but needs attention before Phase 3 (~6,000 Starlink). Each entry has a location, what needs to change, and which phase forces the fix.

---

## Active Items

| # | File | Line | Issue | Fix | Phase |
|---|------|------|-------|-----|-------|
| 1 | `backend/routers/satellites.py` | 25 | `iterrows()` in `/api/satellites` response building | Vectorized DataFrame → list-of-dicts (e.g. `df.to_dict(orient="records")` + rename) | Phase 3 |
| 2 | `backend/core/propagator.py` | 125 | `iterrows()` in `_build_indexes()` | Vectorized: `dict(zip(df["object_name"].str.upper(), df.index))` | Phase 3 |
| 3 | `backend/core/propagator.py` | 247 | `iterrows()` in `get_all_positions()` batch propagation | Unavoidable per-sat SGP4 call, but loop overhead matters at 6k. Consider C++ batch path. | Phase 3 |

## Resolved Items

| # | File | Issue | Resolution | Date |
|---|------|-------|------------|------|
| — | — | — | — | — |

---

## How to Use This File

- When you add a `# ⚠ PERF` comment in code, add a matching row here.
- When scaling to a new phase, scan this list and resolve items for that phase.
- Move resolved items to the Resolved table with a date.
