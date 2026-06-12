# Week 6 Notes — Conjunction Screening (Final Sprint)

## Session 2026-06-11 — Pivot finalization + baseline hardening (pre-build)

This was a planning/strategy session, not a feature build. No numbered task (6.x) was implemented
yet; the goal was to get unstuck on the long-overdue pivot and set up a clean runway for Phase 6.

### Decisions made

- **Pivot finalized: ML dropped → conjunction screening is the headline.** The original ML
  risk-classifier idea was abandoned (full CDM data needs an SSA Sharing Agreement, operators only;
  without it ML can't beat a threshold). Confirmed this is the *right* call by reviewing Jose's 135
  job applications: the targeted aerospace/defense roles (Boeing, Northrop, Castelion, RTX Collins,
  L3Harris, Hadrian, Radiant, Epirus) ask for **C++, orbital-mechanics/GNC, and test/validation
  rigor** — none ask for ML.
- **Scope narrowed further: NO Pc (Probability of Collision).** Pc needs covariance we don't have;
  claiming it would be dishonest. The project is framed as an honest *geometric screener built on
  SGP4*, validated against real data — not operational collision avoidance. This resolves the old
  "are we dressing up inferior data?" doubt: knowing the SGP4-vs-SP / screening-vs-Pc distinction is
  itself the domain awareness recruiters want.
- **Validation anchor = CelesTrak SOCRATES** (open, no-auth, SGP4-based → same-method comparison).
  Space-Track `cdm_public` is an *optional* Phase 8 stretch (SP-based cross-method check,
  detection-only, no Pc).
- **Build strategy = Approach A (vertical slice first):** prove the whole chain end-to-end on ~300
  sats before scaling/validating, so the hard layers surface early and the project stays demoable.
- **Timeline:** Final Sprint Jun 12 – Jul 10 (Phases 6–9). Roadmap reworked with checkboxes and
  `Phase N` → `N.x` numbering.
- **Fine filter stays in Python** (not C++): it runs on only a handful of survivors and each
  distance eval already calls the C++ propagator. C++-where-hot (medium filter, O(N²)),
  Python-where-not. Better engineering-judgment story than "everything in C++."

### SOCRATES / Space-Track research (full reference in `key_information.md`)

- SOCRATES-Plus: open query endpoint + raw-CSV download, no account. CSV columns give us both NORAD
  IDs, TCA, miss distance, relative speed (we ignore MAX_PROB/DILUTION — Pc-related).
- **Epoch-matching gotcha:** must screen with GP data from the *same time* SOCRATES used, or epoch
  drift alone causes TCA/miss-distance disagreement.
- SFS Handbook (RTN screening-volume source) copied into `misc/` alongside other reference docs.

### Baseline hardening (the only code change this session)

Two tests failed against the live June catalog (not regressions — the suite was authored against a
March data snapshot). Both fixed by making assertions robust, not by weakening them:

- **`test_propagator.py::test_all_stations_match_python_sgp4`** — was failing because it propagates
  to a hardcoded `2026-03-21` date, ~70 days *before* the current TLE epochs; back-propagating the
  decayed object `ISS OBJECT XY` throws SGP4 error 6. Fix: skip objects that fail to propagate (a
  decayed object has no position to cross-validate), assert ≥1 validated. Now validates 24/25 to
  sub-meter, skips only the decayed one.
- **`test_api.py::test_epoch_age_is_reasonable`** — flaky (passed alone, failed in full suite). Fix:
  check the *freshest* object is recent (proves the cache is live) instead of requiring *every*
  object < 30 days, and allow marginally future-dated epochs. Robust to catalog churn.
- Result: **279 passing, stable across 3 consecutive full-suite runs.**

### Durable finding → root cause of the flakiness (added task 6.0)

The real source of the flaky epoch test: `TestDataRefresh` (test_api.py) calls `POST /api/refresh`
**unmocked** — every full-suite run hits **live CelesTrak** and overwrites the production
`stations.parquet`. That makes the suite network-dependent, non-deterministic, and mutating of real
data; running it repeatedly during Phase 6 risks CelesTrak rate-limiting / IP-block (100 MB/day cap,
no-retry policy). **Added Phase 6.0** (do first): mock the fetcher in `TestDataRefresh` so the suite
runs offline and deterministic, optionally keeping one env-gated live integration test.

### Deferred (intentionally)

- **Stale ML references** in `PROJECT_PLAN.md`, `requirements.txt` (`xgboost`),
  `progress/week0_setup.md`, `progress/notes/week0_notes.md`, and the `1plan.md` instruction file →
  scheduled for **Phase 9.3** cleanup. Not touched now to keep this checkpoint focused.

---

## Task 6.0 — Mock the Refresh Fetcher (DONE, Jun 11)

Full write-up: `progress/task_logs/task_6_0_mock_refresh_fetcher.md`. Summary:

- **Problem:** `TestRefresh` called `POST /api/refresh` unmocked → live CelesTrak hit + `stations.parquet`
  overwrite on every full-suite run (when cache >2 h stale). Source of the earlier flaky epoch test.
- **Fix (test-only):** `_offline_fetch_patch()` + `setUp` patch `fetcher.fetch` to return cached data
  unchanged → deterministic `rate_limited`, no network, no Parquet write. Added
  `test_refresh_makes_no_network_call` (invariant guard) and opt-in `TestRefreshLive` (`RUN_NETWORK_TESTS`).
- **Result:** 280 passing, 1 skipped, stable ×3; `stations.parquet` md5 byte-identical before/after.
- Phases run: plan (`@1plan`) → build (`@2build`) → review+test (`@3review`/`@4test`) → document (`@5document`).
- Commits: `1ae0cb2` (build), `c0fb5b8` (review+test).

---

## Task 6.1 — C++ Batch SGP4 (DONE, Jun 11)

Full write-up: `progress/task_logs/task_6_1_batch_sgp4.md`. Summary:

- **Built:** `orbitcore.propagate_batch(satrecs, tsince_list)` → list of TEME `(pos, vel)` tuples
  with `None` sentinels for per-sat failures (decayed orbits don't kill the batch). Items passed by
  reference; `tsince` is per-sat (epochs differ — caller maps UTC → per-sat tsince).
- **Review caught a segfault:** pybind11 converts `None` → `nullptr` on *pointer* casts without
  throwing; dereferencing crashed the process. Fixed with a nullptr check → indexed `TypeError`;
  permanent regression test added.
- **Honest perf finding:** batch is only **~1.05×** the Python loop (sgp4 compute dominates;
  Python tuple building remains per-sat). The order-of-magnitude win belongs to 6.3's all-C++
  medium-filter loop. Perf test asserts "not slower" + records the ratio (no flaky 5%-margin race).
- **Validation:** bit-identical to single `sgp4()` calls (LEO/MEO/HEO, incl. backward); sub-meter
  vs python-sgp4; error-reset semantics proven (SGP4.cpp:1779 clears `error` per call).
- 13 new tests → **293 passing, 1 skipped**. `get_all_positions()` wiring deferred to Phase 7.
- Commit: `5f0c184` (code+tests). Gotcha: `backend/*.so` is now gitignored (build artifact).

---

## Task 6.2 — C++ Coarse Filter (DONE, Jun 11)

Full write-up: `progress/task_logs/task_6_2_coarse_filter.md`. Summary:

- **Built:** `orbitcore.coarse_filter(periapsis_km, apoapsis_km, pad_km)` → `(i,j)` pairs whose
  altitude bands overlap (touching counts; gap ≤ pad bridges). Plain arrays in — screener derives
  bands from `alta/altp × Re` or parquet columns (agree to ~0.5 km).
- **Perf finding with 6.3 consequence:** O(N²) scan is only **40 ms at 6,000 sats**, but a dense
  catalog returning 5.4M pairs spends **~2 s converting tuples to Python** (378 ns/pair). The
  survivors feed straight back into C++ in 6.3 → don't round-trip them through Python at scale;
  6.3 should run the coarse cut internally (decide in its plan phase).
- **Honest expectation:** within one Starlink shell, ~100% of pairs survive coarse filtering (one
  shared band) — the filter pays off across mixed catalogs (Phase 7 "active", LEO→GEO).
- 16 tests incl. property-check vs brute force + real-parquet integration → **309 passing**.
- Commit: `e6cfda9`.

---

## Task 6.3 — C++ Medium Filter (DONE, Jun 11) — 6.4 done by construction

Full write-up: `progress/task_logs/task_6_3_medium_filter.md`. Summary:

- **Built:** `orbitcore.medium_filter(satrecs, pairs, jd_start, jd_end, step_sec, threshold_km)` →
  one `(i, j, jd, d)` row per close-approach window. Per-sat epoch→tsince handled internally; GIL
  released during the scan.
- **Decision 1 — time-major loop:** propagate each sat once per step, evaluate pairs from cache.
  300 sats / 44,850 pairs / 24 h @ 60 s = **0.68 s** (pair-major would be ~4 min here, ~6 h at
  Phase 7).
- **Decision 2 — velocity-aware no-skip bound:** `min(d_k,d_k+1) − v̂·Δt/2 − margin < threshold`.
  Fixture proof: true miss **8 km** at v_rel **12 km/s** sampled as **521/200 km** on the 60 s
  grid — naive thresholding misses it, the bound flags it, window brackets true TCA ±1 step
  (verified vs independent 1 s brute force).
- **Review fixes:** squared-distance pre-check vs universal 25 km/s bound (~96% of pair-steps,
  zero sqrts → 2.7× speedup, semantics-identical); sub-step scan window raises instead of silent [].
- 12 tests → **321 passing**. Commits: `ade01ca` (code).
- **6.4 closed:** all three C++ functions were bound inline with docstrings during their tasks.

---

## Task 6.5 — RTN Coordinate Transform (DONE, Jun 11)

Full write-up: `progress/task_logs/task_6_5_rtn_transform.md`. Summary:

- **Built:** `teme_to_rtn(pos_p, vel_p, pos_s)` → `(r_km, t_km, n_km)` in
  `coordinate_transforms.py`. Vallado RSW frame (= CDM RTN), right-handed, pure `math`,
  degenerate states raise. Position-only (Δv projection = noted extension).
- **Validated:** exact hand case, orthonormality invariant on real SGP4 states, geometry
  semantics (radial/along-track/retrograde), independent numpy cross-check ×50.
- **Bonus catch — 6.0 escape:** `TestRefreshMocked::test_rate_limited_skips_reload` had an
  unmocked `fetcher.fetch`; it went live (24 s hang) the moment the cache crossed 2 h staleness
  mid-session. Fixed with `_offline_fetch_patch()`; assertion strengthened to
  `assert_not_called()`. Suite 27 s → **2.6 s**. Lesson: "mocked the class" ≠ "mocked every call
  site"; TTL crossings hide network deps for hours.
- 8 tests → **329 passing**. Commit: `4b5afbc`.

---

## Task 6.6 — Python Fine Filter (DONE, Jun 11)

Full write-up: `progress/task_logs/task_6_6_fine_filter.md`. Summary:

- **Built:** `fine_filter(satrec_a, satrec_b, jd_lo, jd_hi)` in new `backend/core/conjunctions.py`
  — bounded scipy minimization (over minutes-from-bracket-start, not raw JD), each evaluation
  propagates via C++ sgp4. Returns exact TCA (jd + UTC datetime), miss, rel speed, and both TEME
  states (feed `teme_to_rtn` — integration-tested, norm == miss to 1e-9).
- **Edge-widening:** minimum on a bracket edge → widen once, re-run; verified to recover a TCA
  *outside* a deliberately wrong bracket.
- **Key finding:** sampled grids overstate miss for fast crossers — the 1 s brute-force grid said
  8.14 km where the true continuous minimum is **6.60 km** (d(t) ≈ √(d_min² + (v_rel·Δt)²)).
  Phase 8 SOCRATES comparisons must compare refined minima, not sampled ones.
- Validated vs independent 0.01 s brute force: TCA within 0.05 s, miss within 10 m.
- 9 tests in new `tests/test_conjunctions.py` → **338 passing**.
- **Pipeline status: all four computational stages done and interoperating**
  (coarse → medium → fine → RTN). Next: 6.7 screener + endpoint.
