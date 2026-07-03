# Task 9.1 — Clean stale ML / Orekit / Docker references

**Date:** Jul 3, 2026
**Status:** DONE (docs + one dead dependency + one comment; historical journal preserved)
**Tests:** 541 passing, 4 skipped (unchanged — no code behavior touched).

*(Task-log slug is `cleanup_stale_refs` to avoid colliding with the earlier
`task_9_1_conjunction_ux.md`, which logged the frontend interactive core.)*

---

## Goal

Make the **current-facing planning docs + dependencies honest** before the Phase-9
README rewrite (9.7): remove ML / Orekit / Docker where the project no longer uses
them (all dropped in the Jun-11 / Jun-24 pivots), while **preserving the historical
journal** — the ML→screening pivot is a *strength* that shows judgment.

---

## Approach

- **Tiered scope** so we clean truth-docs without rewriting history:
  - **Clean fully** (a reader/AI treats these as current): `PROJECT_PLAN.md`,
    `CLAUDE.md`, `requirements.txt`, `backend/main.py` comment, the phase-instruction
    **memory** (`1plan.md`, `2build.md`).
  - **Banner only** (roadmap's explicit 9.1 targets, but historical): `week0_setup.md`,
    `week0_notes.md` — a "⚠ Superseded" note at the top, body untouched.
  - **Leave alone**: task logs, week{2–6} plans/notes, `sfs_handbook_summary.md`
    (journal), `critical_questions.md` (Jose's uncommitted file), and `README.md`
    (its rewrite is 9.7, needs the live URL + screenshots).
- **Verified there's no ML code at all** — 0 `xgboost`/`sklearn` imports, no ML files —
  so dropping `xgboost` is safe and `PROJECT_PLAN`'s "Component 6: ML Risk Classifier"
  is pure stale narrative.
- **`PROJECT_PLAN.md` mirrors `CLAUDE.md`** (the maintained source of truth) so the
  rewrite doesn't introduce drift.

---

## Implementation

| File | Change |
|------|--------|
| `requirements.txt` | dropped `xgboost` (dead — imported nowhere) |
| `PROJECT_PLAN.md` | rewrote title/timeline, "What We're Building", Decisions, architecture diagram, Component 1 (CDM→`gp_history`), **Component 5** (real screening cascade), **Component 6** (ML classifier → "Validation Against SOCRATES", with the honest *why not ML*), Tech Stack table, Project Structure (counts + pointer, dropped planned ML/Docker files), Key Risks, Setup Checklist |
| `CLAUDE.md` | "What This Project Is", Tech Stack line, architecture diagram; updated the "stale refs… not yet done" note to reflect this cleanup |
| `backend/main.py` | stale `# Tighten in Week 8 Docker deployment` CORS comment |
| `progress/week0_setup.md`, `progress/notes/week0_notes.md` | "⚠ Superseded" banner (history preserved) |
| `~/.claude/…/memory/1plan.md`, `2build.md` | the "→ ML risk classifier" blurbs future sessions load (memory, not repo) |
| `progress/roadmap.md` | Phase 9 restructured into build order (from the pre-9.1 review) + 9.1 marked done |

---

## Validation

- **No forward stale refs left:** `rg` over `PROJECT_PLAN.md` / `CLAUDE.md` shows every
  remaining ML / Orekit / Docker mention is now *"dropped"/"replaced"/"why not"* —
  historical, not planned.
- **App boots:** `import backend.main` succeeds with `xgboost` gone.
- **Suite green:** 541 passing / 4 skipped — no behavior changed.

---

## Lessons learned

- **Don't rewrite the journal.** The pivots (ML dropped Jun 11, Orekit dropped, Docker→
  static Jun 24) are the project's *evolution*; task logs / week notes recording them
  were accurate when written. Cleaning is for the docs a reader treats as *current
  truth* (PROJECT_PLAN, CLAUDE, README-later), not the history.
- **The pivot reframed is an asset.** "Component 6: Validation Against SOCRATES
  (replaced the original ML classifier)" — with the covariance/SSA-agreement reason —
  reads as engineering judgment, which is exactly what the target roles want.
- **Memory files count as current-facing.** `1plan.md`/`2build.md` are loaded into every
  future session; leaving "→ ML risk classifier" there would keep misinforming them.

---

## Deferred

- **`README.md`** — full rewrite in **9.7** (live URL, screenshots, architecture diagram,
  report links). Intentionally untouched here to avoid double work.
- A **full `PROJECT_PLAN.md` file-tree refresh** — pointed to `CLAUDE.md` → Key Files
  instead of re-drawing the stale tree (a maintenance sink); fix opportunistically.
