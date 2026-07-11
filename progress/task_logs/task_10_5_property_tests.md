# Task 10.5 — Mutation-checked property tests for the Stage-1+2 flip

**Date:** Jul 9, 2026
**Status:** DONE — the exact 10.6 production contract is now locked offline, on
hostile geometry, with every lock proven to bite.
**Tests:** **581 passing + 4 skipped** (+5 `TestFlipContract`), 3× flake-clean.

---

## Goal

10.6 flips production onto `fused + sieve + refine` on the strength of the test
suite, so 10.5's job is to make that suite *bite*. The pairwise equivalences
(classic≡fused, sieve on≡off, refine on≡off) were already locked, but (a) the
exact production path — all three at once — was proven only by the live A/B,
never offline; (b) every synthetic fixture was well-conditioned (circular,
fresh-epoch, no drag), exactly the geometry the 10.1b margins *don't* exist
for; and (c) nothing proved the identity tests would notice a broken engine.

## What shipped — `TestFlipContract` (5) + the hostile composite fixture

**The fixture (`_composite_satrecs`):** three shells at different
altitudes/inclinations (53°/15.05, 97°/15.35, 72°/14.85) for real cross-shell
geometry — spurious medium windows, mixed relative-inclination node lines —
plus two adversarial singletons the uniform shells lack: a high-drag orbit
(large bstar/ndot, the 10.1b reentering class) and an eccentric one (e=0.03,
perigee ~345 / apogee ~760 km, spanning all three shells → drives the
equinoctial-chord + EoC-widening sieve paths). Module-cached (five locks share
one build; screens mutate only transient satrec `.t`/`.error`, always
recomputed before use, and refine works on copies — no cross-test
contamination). Deterministic + offline.

**The locks:**
1. **`test_flip_identity_on_hostile_composite`** — THE 10.6 contract:
   `classic == fused+sieve+refine` event-for-event (1,826 events), with
   **both stages asserted engaged** — sieve dropped windows (flip's flagged
   count 11,583 < classic full scan 12,671) AND refine dropped rows
   (survivors 1,826 < flagged 11,583).
2. **`test_flip_identity_sfs_ellipsoid_path`** — the same contract through the
   SFS report cut (asymmetric custom volumes, since Table 3 excludes the
   synthetic radial-dominated geometry — 7.2), so the ellipsoid pre-cut +
   suppression + de-dupe path is locked non-vacuously (149 events, floor 50).
3. **`test_flip_path_run_to_run_deterministic`** — the flip path against
   itself, byte-identical (OpenMP + activation-list order are nondeterminism
   surfaces the classic determinism lock didn't cover).
4. **`test_sieve_margins_are_wired_and_bite`** — zeroing the sieve's tunable
   margins strictly shrinks the row set (they demonstrably reach the interval
   builder).
5. **`test_refine_pre_cut_mutation_bites`** — deflating the pre-cut ellipsoid
   to 40% of the report volume drops rows the report cut keeps, while the
   correctly-sized pre-cut stays a superset on the same rows — the committed
   proof that the identity locks are *sensitive*, not vacuously green.

## Validation — the tests provably bite (the whole point of the phase)

Two independent bite proofs:

- **Compiled mutations** (dev-time, engine reverted after): removed the sieve's
  ±1-step interval pad → `test_sieve_events_identical_at_shell_scale` went red;
  swapped the T/N axes in the refine ellipsoid → **three** locks went red (both
  SFS identity tests + the pre-cut bite test). Engine restored from git,
  rebuilt, re-verified pristine.
- **In-suite deflation** (`test_refine_pre_cut_mutation_bites`, committed): the
  40%-deflated pre-cut drops kept rows every run.

## Findings (two review rounds)

- **Round 1 — verify the fixture, don't trust its framing.** A "hostile"
  fixture is worthless if the adversarial cases silently decay or coarse-filter
  out. Measured: both singletons propagate the full window and appear in medium
  windows (DRAG 33, ECC 70) — genuinely exercised. Also caught + fixed a
  docstring overclaim (the drag sat does NOT make curvature margins "do real
  work" over 3 h — that was a 21.8-*day* epoch-age finding in 10.1b).
- **Round 2 — verify the assertions, not just the fixture.** The flip test's
  "both stages engaged" claim was only half-asserted: `n_survivors < n_windows`
  proves *refine*, but nothing proved the *sieve* dropped windows — a
  regression silently disabling the sieve would still pass. Added
  `flip n_windows < classic n_windows` (free — classic already runs). Now both
  stages are tripwired.
- **Honest limit — the sieve zero-margin event bite is unconstructible
  offline.** Zeroing the sieve margins shrinks the row set but drops **zero
  events** on synthetic geometry (1,826 == 1,826): every sieve conservatism
  except the 0.5° base is a *constructive bound*, so only real-catalog
  pathologies bite at the event level (10.1b measured 1/59/256/351 dropped
  events there). The test asserts the row-level bite + documents why the
  event-level one needs real data; the standing event-level check is 10.6's
  in-CI A/B. No fake assertion was committed to paper over this.

## Intentional coverage gap (documented, not tested)

The C++ pre-cut's "propagation failure → hand the row back to Python" branch is
unexercised (nothing on the composite decays). Deliberately not tested: even a
*wrong* C++ decision there produces identical events — Python's
`fine_filter_batch` returns None for a decayed row and `run_screen` drops it
too — so a branch that cannot change events isn't worth a fixture that forces a
mid-window decay.

## Files

- `tests/test_conjunctions.py` — `_composite_satrecs` fixture + `TestFlipContract`
  (5 tests). No engine or `backend/` changes (the sieve-margin kwargs still
  aren't plumbed through `run_screen`; the mutation test uses `screen_pairs`
  directly, so that 10.2 residual stays deferred — nothing needed it).

## Test coverage

| Lock | What it pins |
|---|---|
| flip_identity_on_hostile_composite | classic == full flip; both stages engaged |
| flip_identity_sfs_ellipsoid_path | same through SFS cut + suppress + de-dupe |
| flip_path_run_to_run_deterministic | OpenMP/scan-order determinism |
| sieve_margins_are_wired_and_bite | margin params reach the interval builder |
| refine_pre_cut_mutation_bites | pre-cut is load-bearing (deflate → events drop) |
