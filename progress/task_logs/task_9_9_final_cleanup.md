# Task 9.9 — Final cleanup (demo seed removed, suite de-flaked)

**Date:** Jul 8, 2026
**Status:** DONE
**Tests:** **556 passing + 4 skipped** (was 563 — 7 tests of the removed feature
deleted with it), 3× flake-clean.

---

## Goal

Portfolio-final tidy per the roadmap: remove the synthetic demo seed now the
real active catalog drives the deployed demo; dead code out; all tests green.

---

## What was removed / changed

| File | Change |
|------|--------|
| `backend/core/demo_seed.py` | **`append_demo_crosser` + `DEMO_CROSSER_ID` removed**; module re-documented as what it now is — the deterministic `build_synthetic_shell` **test/profiling fixture** (kept: the scale-regression tests + `profile_screening.py` depend on it) |
| `backend/core/propagator.py` | `seed_demo` param + `_ensure_data` branch removed |
| `backend/main.py` | `ORBITWATCH_DEMO_SEED` env var + comment block removed (Pyright caught the lifespan call site the grep missed) |
| `tests/test_propagator.py` | `TestDemoSeed` (6 tests) deleted with the feature |
| `tests/test_conjunctions.py` | `TestSeededScreenIntegration` (1 test) deleted; **`TestDenseShellScale` reworked** to assert on the shell's *natural* plane-crossings (7.5 measured ~257 windows at 300 sats/100 km pad) instead of the seeded crosser — gates the RTN-norm check on a genuine crossing (`rel_speed > 1`) |

## The incidental find — census assertions vs. a live-churning catalog

Two Phase-2-era tests failed **before** my changes (verified by stash):
`assertGreaterEqual(len(results), 25)` against the stations cache. Cause: the
9.7 README-command verification ran `build_snapshot.py --group stations`, whose
`fetch()` refreshed the stale cache from live CelesTrak — and the stations
group is down to **23 objects** (modules undock; the catalog churns). The tests
were asserting an **absolute census of a live-mutable dataset** — latent since
Week 2, exposed by any cache refresh. Fixed by making them **relative to the
loaded catalog** (`results + errors == catalog_size`, `results ≥ size − 2` for
genuinely decayed entries) — the tests' true intent ("everything loaded
propagates, fast") with no dependency on orbital-station geopolitics.

**Durable lesson:** never assert absolute counts of a refreshable catalog;
assert *relative* completeness. (Same family as 6.0's test-hygiene pass.)

## Verified

- `grep` sweep: zero remaining `DEMO_SEED` / `append_demo_crosser` /
  `DEMO_CROSSER_ID` / `seed_demo` references in code (one intentional
  docstring note records the removal).
- `compileall` clean; full suite **556 + 4 skipped, 3× consecutive**.
- Docstrings: public APIs already documented as-built; the one module whose
  docstring lied after the change (`demo_seed.py`) was rewritten.

## Deferred

- `demo_seed.py` **filename** now undersells its contents (it's a test fixture,
  not a demo seed) — a rename to `synthetic_shell.py` would touch 5 importing
  files for purely cosmetic gain; skipped deliberately, noted here.
- 9.10 (prediction-evolution view) remains the only open Phase-9 item — a
  stretch goal; the `data`-branch archive it needs is accumulating since 9.5.
