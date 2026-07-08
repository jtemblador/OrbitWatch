# Task 9.7 — README rewrite

**Date:** Jul 8, 2026
**Status:** DONE
**Tests:** none (docs-only; a README push doesn't match the deploy path filter).

---

## Goal

Replace the stale README (still pitched the dropped ML classifier, Orekit,
Docker, "Week 6 in progress", 279 tests) with a concise portfolio front page for
a recruiter/engineer arriving from the resume: what it is, that it's **live**,
and that it's **rigorous** — in under a hundred lines.

---

## What shipped

`README.md` (~95 lines) + `docs/img/` (2 screenshots captured from the live site
with Playwright):

1. Live URL + one-paragraph pitch + clickable hero screenshot (the startup
   "All conjunctions" view).
2. "What it is / not collision avoidance" — the honest no-Pc framing.
3. Compact architecture diagram (robot job → `snapshot.json` → static globe) +
   one paragraph on the cascade; focused-conjunction screenshot (group-colored
   trails + TCA orb + ground marker).
4. **Validation table** + links to `socrates_report.md` / `sgp4_uncertainty.md`.
5. Tech table, verified run-locally commands, pointer to the `progress/` journal.

Numbers all traced to CLAUDE.md / the validation report; the
`build_snapshot.py` command was run before being written down; all relative
links/images checked.

---

## The one real lesson (user caught it)

The first validation table showed only the epoch-matched end state — three rows
of `0.000` — and Jose's reaction was *"why does this have no real data?"* The
zeros are genuine (SGP4 is deterministic: same method + same input elements ⇒ a
correct implementation agrees exactly; nonzero residual = our bug), but **a
table of perfect zeros reads as a placeholder**. Fix: show the controlled
contrast the report actually contains — current-feed elements (3/9 · 8/25 ·
8/40, median Δmiss 1.1–2.6 km = epoch drift) vs. epoch-matched (8/9 · 25/25 ·
40/40 at Δ 0.000) — plus one sentence saying *why* exact zeros are the expected
pass mark. The contrast is both more credible and the better story: a controlled
experiment isolating element vintage from method error.

**Durable takeaway:** when a validation result is "suspiciously perfect,"
present the variable you controlled *alongside* the result, or readers assume
fabrication.

---

## Deferred

- Demo GIF slot (9.8) — the hero screenshot is its placeholder.
- Screenshots will drift as the UI evolves; recapture at 9.9 if anything
  user-visible changes.
