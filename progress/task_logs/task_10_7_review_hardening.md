# Task 10.7 — Post-launch code review + hardening (5 findings fixed)

**Date:** Jul 15, 2026
**Status:** DONE — 5 review findings fixed, 583 tests passing (+2), frontend fix
previewed locally + headless-verified, live via the deploy.
**Live:** https://jtemblador.github.io/OrbitWatch/

---

## Goal

A full-project code review (four parallel reviewers over the C++ core, Python
backend, frontend JS, and CI/scripts) surfaced a handful of real defects in a
mature codebase. This task fixes the five that mattered: one latent C++ crash,
one CI gap that let engine code reach production ungated, one backend race, one
gate blind spot, and one frontend stale-data bug. No feature work — pure
hardening of what already shipped in Phase 10.

## The five findings + fixes

### #1 (High) — `satnum` buffer overflow past the 5-digit catalog cap
`elsetrec::satnum` was `char[6]` (5 digits + NUL), but Vallado's `sgp4init`
unconditionally `strcpy`s the incoming id into it and our binding let up to 8
chars through. Any NORAD id ≥ 6 chars overflows the struct — undefined behavior,
and imminent: the 5-digit cap runs out ~now (the reason we already ship OMM/JSON,
not TLE). The file's own changelog (`SGP4.h:15`) says satnum was "chg[d] to
string for alpha 5 or 9-digit" — upstream *intended* 9-digit support but never
widened the buffer.

**Fix (widen to what upstream meant, and bound every copy into it):**
- `SGP4.h` — `satnum[6]` → `satnum[10]` (a full 9-digit id + NUL). POD struct, so
  the OpenMP per-thread `elsetrec` copies and everything else are unaffected; no
  numeric behavior changes.
- `bind_satrec.cpp` — the setter and the `sgp4init` lambda now bound their copies
  to `sizeof(field) - 1` (was hard-coded `5`/`8`) and always NUL-terminate; the
  local `satn` buffer is sized to the field so the downstream `strcpy` can't
  overflow whatever comes in.
- `SGP4.cpp` — the MSVC `strcpy_s` size literal `6` → `sizeof(satrec.satnum)`.

Identity preserved (no truncation) — `test_propagator.py:953` reads
`int(satrec.satnum)` back as the real id, which a truncating fix would corrupt.

### #2 (High) — engine code could reach production ungated
`deploy.yml`'s `push` trigger only fired on `frontend/**` + the workflow file, so
a commit to `backend/**` or `orbitcore/**` triggered **no run at all**; and even
a triggered push only *reuses* the snapshot (no rebuild → the A/B gate never
runs). The next 3×/day cron then rebuilt with the new engine and **skips the
gate**, shipping unvalidated screening logic. deploy.yml's comment claiming the
gate "runs on any push-triggered rebuild" was false for exactly the code it
protects.

**Fix:** a dedicated `.github/workflows/ci.yml` that runs on push/PR touching
CODE (`orbitcore/**`, `backend/**`, the screening scripts, `tests/**`,
`requirements.txt`). It builds the `.so`, runs pytest (fast fail), then the same
two-tier A/B gate on live data. Read-only perms, no deploy, no secrets. This
gates code at push time, independent of the data-refresh cron — which is what
makes the cron's "engine already validated" gate-skip actually true. deploy.yml
stays frontend-triggered for fast UI redeploys; its stale gate comment corrected
to point at ci.yml. **Residual (documented in ci.yml):** on a solo repo pushing
straight to `main` without required checks, a red CI run is a loud *alert*, not a
physical block — branch protection (via a PR flow) would make it a hard block.

### #3 (High, local-dev only) — `/api/refresh` race + event-loop block
`refresh_data` called the blocking CelesTrak fetch inline on the event loop, then
`propagator.reload_data()` (which nulls the Satrec cache + name/norad indexes)
**without `_propagator_lock`** — the lock every other handler holds to read those
indexes. A position/conjunction request mid-reload could deref a half-cleared
index (`self._name_index.get(...)` on `None` → `AttributeError` → unhandled 500).

**Fix:** fetch via `run_in_threadpool` (off the event loop), and run
`reload_data()` under `async with _propagator_lock`. Prod is static so this is a
local-dev endpoint, but it's a genuine race.

### #4 (Med-High) — the A/B gate / snapshot could pass on a truncated catalog
The gate only checks `A == B`. A valid-but-truncated fetch (rate-limited / partial
upstream response — non-empty, so the empty-check misses it) would let both sides
screen the same tiny catalog, agree trivially, PASS, and then `build_snapshot`
(no min-count check) would publish a near-empty snapshot — silently, green gate.

**Fix:** a `--min-objects` floor in both `ab_screen.py` and `build_snapshot.py`,
checked on the **raw** catalog *before* any `--max-sats` slice (so the cap can't
defeat it). Wired `--min-objects 8000` into both A/B tiers (ci.yml + deploy.yml)
and the `build_snapshot` step — the last guard on the cron path, which skips the
A/B gate. Real active catalog is ~16k. Proven: impossible floor → exit 1 on both
scripts; the 8000 floor still proceeds under `--max-sats 30` (confirming it reads
the raw 16,030, not the slice).

### #5 (High, frontend) — info panel showed stale numbers on failed propagation
`refreshPanelData` early-returned when `computePositionGd` returned null (decayed
/ diverged elements) *after* the title was already set to the new satellite but
*before* the table was rewritten — so the panel showed the new name over the
**previous** satellite's lat/lon/altitude/speed.

**Fix:** on a null position, render an explicit `no data at this time` state
(NORAD id + Position: no data) instead of leaving stale rows. Headless-verified
(Playwright): forcing a satellite's propagation to fail shows the no-data state
under the correct name with zero leakage of the prior sat's numbers; zero console
errors.

## Validation
- Rebuilt the `.so`; full suite **583 passed, 4 skipped** (was 581; +2 satnum
  regression tests).
- **#1 mutation-proven:** reverting the setter bound to `5` made
  `test_satnum_six_and_nine_digit` fail deterministically (`27000` != `270000`),
  then restored + reconfirmed green.
- **#4 proven:** exit-code checks on real cached catalogs (impossible floor → 1,
  normal floor under a small `--max-sats` → proceeds).
- **#5 verified headlessly** on the local server (full 16,030-sat snapshot),
  0 console errors.
- Both workflows YAML-linted; every new CLI flag confirmed present.

## Test coverage
| Test | Covers |
|------|--------|
| `test_sgp4_cpp.py::TestSatrec::test_satnum_six_and_nine_digit` | 6- and 9-digit ids round-trip through the setter without overflow |
| `test_sgp4_cpp.py::TestSatrec::test_satnum_six_digit_via_sgp4init` | 6-digit id survives the internal `strcpy` on the real `sgp4init` path |

Scripts/frontend/CI have no pytest coverage (project convention); #4 was
exit-code-verified against live caches, #5 headless-verified via Playwright.

## Lessons learned (durable)
- **Vendored SGP4's `satnum` is `char[6]` but the code writes up to 9 digits into
  it** — a latent overflow that only bites once catalog ids pass 99999 (≈ now).
  Widened to `[10]`; any re-vendor of `SGP4.cpp/.h` must re-apply. See
  key_information.
- **A `push: paths:` filter that omits engine dirs means engine changes never
  trigger CI** — and a cron that skips its own gate then ships them. Gate CODE at
  push time (ci.yml), separate from the DATA-refresh cron.
- **An `A == B` gate needs an absolute sanity floor** — two identically-degraded
  inputs agree trivially. Check the raw input size, not just the diff.
- **Any handler that mutates the propagator's shared cache/indexes must hold
  `_propagator_lock`** — `reload_data()` nulls them; a lock-free reload races
  every position/screen request.

## Not fixed (out of scope, flagged)
- `propagator.py:75` does `str(int(row["norad_cat_id"]))` — fine for 6-digit
  numeric ids, but would throw on an Alpha-5 *letter* form (`"A1234"`). OMM is
  numeric today; separate latent concern.
- Lower-severity review items (no version pins in `requirements.txt`, reused
  snapshot not JSON-validated before republish, a few dead frontend/CSS bits,
  `http_fetch` curl-fallback raising `RuntimeError` vs the documented
  `HTTPError`) — left as backlog.
