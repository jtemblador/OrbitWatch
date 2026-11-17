# Task 11.3 (a, b, d) — Ops & hygiene: offline tests, deploy-upload retry, flaky benchmark

**Date:** Nov 17, 2026
**Status:** DONE (a, d verified locally; b lint-clean, not runnable here). 11.3c (archive
retention) is a decision, not in this task.
**Tests:** fresh offline clone **147 failing → 0** — `649 passed, 5 skipped`, 3× clean.

---

## 11.3a — The suite passes on a fresh, offline clone

### Before
On a clone with no network (and no gitignored `backend/data/`):
**`90 failed, 496 passed, 7 skipped, 57 errors`** — 147 broken, from two causes:
1. **No catalog cache.** The default dataset is the cached CelesTrak `stations` group;
   `backend/data/tle/*.parquet` is gitignored → `load_cached` raises "No cached data for
   group 'stations'" in every propagator/API test. CI papered over it by fetching the live
   group before pytest — so CI's pytest *also* depended on CelesTrak being up (the same
   CelesTrak outages that made 7% of cron runs re-serve stale data in 10.10).
2. **No SPICE kernels.** `ecef_to_geodetic` called `_ensure_kernels()`, which raises if
   `naif0012.tls` / `pck00011.tpc` / `earth_latest_high_prec.bpc` are missing.

### The catalog: a frozen, real fixture
`tests/fixtures/stations_omm.json` (**15.9 KB**, 28 records) — raw CelesTrak gp.php-shape
OMM, cut from the archived snapshot `snapshots/2026-10-28T18-55-42.371916Z.json.gz` on the
`data` branch (the last-but-few pre-10.10 *full* records; slim records carry no OMM). The
file is self-describing (`source`, `captured_at`, `selection` fields next to `records`).

- **Membership** — the CelesTrak group list isn't in the snapshot (it's the `active` group),
  so the station set is rebuilt by a documented, mechanical rule: `OBJECT_ID` `1998-067*`
  (ISS assembly + ISS-deployed objects) **or** sharing the exact ISS (25544) / CSS Tianhe
  (48274) element set (attached modules + docked vehicles: Soyuz/Progress/Dragon,
  Shenzhou/Tianzhou). 11 ISS-family + 5 CSS-family + 12 ISS-deployed cubesats/objects.
  Includes NORAD ids > 99,999 (SOYUZ-MS 29 = 100057…), so the 10.7 satnum path is exercised.
- `OBJECT_TYPE` stripped (gp.php omits it) → the real name-derivation runs.
- **Difference from the live group:** the source is active payloads only, so **no debris /
  rocket bodies** (the live group carries a `FREGAT DEB`). See the one test change below.

`tests/fixture_catalog.py` seeds `stations.parquet` through the **real**
`GPFetcher._parse_json` + `_cache_to_parquet`, pinning `fetch_time`/`epoch_age_days` to the
capture time (deterministic bytes; reads as stale, so a live `fetch()` would re-download).

`tests/conftest.py` (`pytest_configure`, i.e. before test_api.py builds the app at import)
seeds a temp dir and points `tle_fetcher.DATA_DIR` at it — **both** module objects
(`backend.core.tle_fetcher` and `core.tle_fetcher`; test files import it both ways). The one
production change enabling this: `GPFetcher.__init__(cache_dir=None)` now resolves
`DATA_DIR` at call time (was bound at def time). Side benefit: the suite can no longer read
or rewrite the developer's real cache (a past bug class — key_information "Testing
Gotchas"). Escape hatch: `ORBITWATCH_TEST_LIVE_CACHE=1` runs against the real cache
(verified: 648 passed against a live-shaped cache).

### Time: tests anchor to the catalog, not the wall clock
A frozen catalog ages. Measured on the fixture (objects that still propagate / ISS alt):
+0 d 28/416.8 km · +180 d 25/421 km · +1 y 22/415 km · +2 y 17/392 km — wall-clock tests
would pass today and rot within ~1–2 years. So:
- `test_propagator.py` — all 13 `datetime.now()` → `catalog_now()` (the loaded catalog's
  `fetch_time`; for a live cache that's within hours of now, so the tests mean what they did).
- `test_api.py` — propagation tests pin `?time=<catalog_now>` (`T_CATALOG`). The wall-clock
  **default** is now locked explicitly by new `TestDefaultTime` (batch/single/track: no
  `?time=` ⇒ resolves to within minutes of now) — previously only implied.
- `test_epoch_age_is_reasonable` — freshness judged **at fetch time** (served age − time
  since fetch; same check for a live cache), **plus** a new assertion that the served age
  really is recomputed from the wall clock (`epoch + age ≈ now`).
- `test_coordinate_transforms.py` / `test_sgp4_cpp.py` read the parquet by hardcoded path →
  now via `GPFetcher().load_cached`. Two former *skips* (`TestEndToEnd`) now run.

**Verified future-proof:** whole suite under libfaketime at **2031-06-01**: everything
passes except 3 cache-freshness tests that compare a just-written file's *mtime* (real
kernel clock) against the faked clock — a faketime artifact; they pass on the real clock.

### The one assertion that changed shape
`test_object_type_is_derived_not_all_unknown` asserted `"DEBRIS" in types` — a census of
the live group's `FREGAT DEB`, impossible on an active-only fixture. Replaced with a
**per-object** lock: every served `object_type == _derive_object_type(name)` (stronger for
the chain it guards; would also catch a DEB object mis-served). The DEB/R-B derivation
itself stays locked in `test_gp_fetcher` (incl. through `load_cached`). **Judgment call —
flagging it:** the API-level test no longer *exercises* a DEBRIS row on the fixture.

### SPICE: the kernels were never inputs
`sp.recgeo(rectan, re, f)` takes the ellipsoid as arguments and never reads the kernel
pool — checked: it returns identical output with `ktotal('ALL') == 0`. The three kernels
were furnsh()ed and never consulted. NAIF (`naif.jpl.nasa.gov`) is **unreachable from this
sandbox** (proxy 403; CelesTrak + Space-Track too), and the `.bpc` changes daily anyway, so
committing them wasn't the right fix. **Fix:** removed `_ensure_kernels()` from
`coordinate_transforms.py` (comment explains why); still SPICE `recgeo`. New lock
`test_needs_no_spice_kernels` runs the conversion in a **fresh interpreter** and asserts
0 kernels loaded + exact geodetic output. `tests/test_spice.py` (which tests kernel loading
itself) still skips cleanly without kernels; CI keeps downloading them for it.

### CI (`ci.yml`)
Dropped the "Provision the stations test catalog" step (pytest no longer needs CelesTrak —
one fewer outage-sensitive step). SPICE step kept (for `test_spice.py`), comment updated.
The A/B gate still fetches the live `active` catalog — live data is still exercised.

## 11.3b — Guarded retry for the Pages artifact upload (`deploy.yml`)
Nov 11: `actions/upload-pages-artifact` → "Failed to CreateArtifact … Unexpected token '<'"
(GitHub's artifact API returned HTML for ~10 min, beyond the action's internal retries).
- Attempt 1 (`id: upload`) is `continue-on-error: true`.
- If `steps.upload.outcome == 'failure'`: a `::warning::` + `sleep 120`, then **one** retry
  (`id: upload_retry`, no continue-on-error) under a **different artifact name**
  `github-pages-retry`. Why a new name: if attempt 1 failed *after* creating its
  `github-pages` artifact, re-using the name could collide; I couldn't verify the action's
  overwrite semantics offline (the action repo isn't readable from this session), and a
  distinct name sidesteps the question.
- `build` exposes `outputs.pages_artifact` = `github-pages` if attempt 1 succeeded else
  `github-pages-retry`; `deploy-pages` takes it as `artifact_name`.

Outcome table: ok → retry skipped, deploys `github-pages` (behaviour unchanged) ·
fail→ok → job green, archive runs, deploys `github-pages-retry` · fail→fail → retry step
fails the job → archive skipped + `deploy` (needs: build) never runs — identical to today's
single-failure behaviour. Permissions untouched (least-privilege per job as before).
**Validated:** `actionlint` 1.7.12 clean on both workflows. **Not run** — needs a real
Actions run (and a real outage to hit the retry branch).

## 11.3d — Flaky micro-benchmark
`TestPropagateBatch::test_batch_faster_than_python_loop` → renamed
**`test_batch_equals_python_loop`**: the `t_batch < t_loop * 1.10` assert on two ~2–4 ms
timings is gone (it failed ~1 in 8 full-suite runs under load); kept the unconditional
`batch_result == loop_result` (+ a length check) and the printed ratio (`-s`). The Backlog
entry refers to the old name.

## Files
- new: `tests/conftest.py`, `tests/fixture_catalog.py`, `tests/fixtures/stations_omm.json`
- `backend/core/tle_fetcher.py` (late-bound default cache dir),
  `backend/core/coordinate_transforms.py` (no kernel precondition)
- `tests/test_api.py`, `test_propagator.py`, `test_coordinate_transforms.py`,
  `test_sgp4_cpp.py`; `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`

## Lessons
- **A frozen fixture needs a frozen clock.** Swapping the data without anchoring "now" just
  moves the failure 1–2 years out (measured). Anchor to the catalog's own fetch time.
- **Check whether a "dependency" is actually read.** The kernels looked load-bearing (a
  hard FileNotFoundError) but `recgeo` never touches the pool.
- **Census asserts on live groups don't survive a fixture** — replace with a per-object
  invariant rather than deleting.
