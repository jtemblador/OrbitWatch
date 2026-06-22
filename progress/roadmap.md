# OrbitWatch — Roadmap

**Timeline:** Mar 20 – Jul 10, 2026  ·  Weeks 0–5 ✅  ·  Final Sprint = Phases 6–9

---

> **Pivot (Apr 9):** Original plan included an ML risk classifier trained on CDM data from
> Space-Track. After researching data access, the full CDM class (with covariance matrices,
> RTN positions, and observation quality metrics) requires an SSA Sharing Agreement — access
> restricted to satellite operators and government agencies. Without that data, an ML model
> would lack the features to outperform a well-tuned threshold, making it decoration rather
> than engineering. We pivoted to making the conjunction detection pipeline itself the
> centerpiece: industry-standard RTN coordinates, asymmetric screening volumes matching
> 19 SDS tables, and validation against CelesTrak SOCRATES. The technical depth is in the
> orbital mechanics and systems engineering, not in bolting on ML without a genuine purpose.

> **Scope refinement (Jun 11) — the final sprint.** Reviewing the aerospace/defense roles being
> targeted (Boeing, Northrop, Castelion, RTX Collins, L3Harris, Hadrian, Radiant, Epirus) confirmed
> the direction: every one asks for **C++, orbital-mechanics / GNC knowledge, and test/validation
> rigor** — none ask for ML. The headline is **conjunction screening validated against SOCRATES**.
> We further narrowed scope: **no Pc (Probability of Collision) computation** — that needs
> covariance data we don't have, and claiming it would be dishonest. This is an honest *geometric
> screener built on SGP4*, not operational collision avoidance, and we say so plainly. Knowing that
> distinction is itself the kind of domain awareness these recruiters look for.

---

## Week 0–1: Setup + C++ Foundation (Mar 20 – Apr 2) ✅
- [x] Create accounts (Space-Track, Cesium Ion)
- [x] Init repo, virtual env, Python dependencies
- [x] Install C++ toolchain (g++/clang, CMake, pybind11)
- [x] Build and test a minimal pybind11 "hello world" extension
- [x] Install spiceypy, download SPICE kernels, verify coordinate conversion works
- [x] Verify CelesTrak TLE fetch works
- [x] Research Cesium.js Sandcastle examples

## Week 2: TLE Data + C++ SGP4 Propagation (Apr 3 – Apr 9) ✅
- [x] Build TLE fetcher (CelesTrak stations group)
- [x] Implement SGP4 propagation in C++, expose via pybind11
- [x] Use SPICE for TEME → ECEF → lat/lon/alt coordinate transforms
- [x] Verify ISS position matches known trackers
- [x] Unit tests for propagation accuracy

## Week 3: FastAPI Backend (Apr 10 – Apr 16) ✅
- [x] FastAPI app skeleton with uvicorn
- [x] `/api/satellites` and `/api/positions` endpoints
- [x] Wire propagator.py to call C++ extension + SPICE
- [x] Serve Phase 1 satellite data (stations) via API
- [x] Unit tests for API endpoints

## Week 4: Cesium.js Globe — Basic (Apr 17 – Apr 23) ✅
- [x] Set up Cesium.js frontend with globe + Ion token
- [x] Render Phase 1 satellites (stations) as points on globe
- [x] Click satellite → show info popup with orbital metadata
- [x] Orbit trail at orbital altitude (TEME API + client-side GMST rotation)
- [x] Connect frontend to FastAPI backend

## Week 5: Cesium.js Globe — Polish (Apr 24 – Apr 30) ✅
- [x] Nadir line (altitude stalk from ground to satellite)
- [x] Time controls (play/pause/speed) — simulated clock, adaptive refresh, TEME re-rotation
- [x] Display controls (labels toggle + type filters ready for Phase 2)
- [x] Selection indicator, info panel auto-refresh

---

# 🏁 Final Sprint — Conjunction Screening (Jun 12 – Jul 10)

**Strategy: vertical slice first.** Get the entire chain working end-to-end on a small set before
scaling or validating, so every hard layer (C++ batch propagation, the all-pairs scan, the SOCRATES
match) is proven early — and the project stays demoable the whole way.

> **Standing non-goals:** no Pc computation, no ML, no Orekit. Geometric screening validated
> against SOCRATES is the spine. Space-Track `cdm_public` is an *optional* Phase 8 stretch
> (detection-only cross-check, not a Pc dependency). Defer anything else.

## Phase 6: Vertical Slice — Pipeline End-to-End (Jun 12 – Jun 20)
Prove the whole pipeline on a ~300-sat subset. Heaviest phase — most new code lives here.

- [x] **6.0 Mock the refresh fetcher** (test hygiene, done first) — `TestRefresh` no longer hits live CelesTrak / rewrites `stations.parquet`; suite runs offline + deterministic. 280 passing. See `task_logs/task_6_0_mock_refresh_fetcher.md`.
- [x] **6.1 C++ batch SGP4** — `orbitcore.propagate_batch(satrecs, tsince_list)` with per-sat `None` sentinels (one decayed sat can't kill a screen). Bit-identical to single calls; 13 tests; 293 passing. **Measured honestly: only ~1.05× vs the Python loop** — sgp4 compute dominates, so the real perf win moves to 6.3's all-C++ medium filter. See `task_logs/task_6_1_batch_sgp4.md`.
- [x] **6.2 C++ coarse filter** — `coarse_filter(periapsis_km, apoapsis_km, pad_km)` → `(i,j)` pairs with overlapping altitude bands. Scan = 40 ms at 6,000 sats; measured gotcha: millions of survivor pairs cost ~2 s in C++→Python conversion → 6.3 should keep the coarse cut internal at scale. 16 tests; 309 passing. See `task_logs/task_6_2_coarse_filter.md`.
- [x] **6.3 C++ medium filter** — time-major scan (each sat propagated once/step) + **velocity-aware no-skip bound** (fixture proof: 8 km true miss sampled at 521/200 km on the 60 s grid — naive thresholding misses it). 300 sats/44,850 pairs/24 h = **0.68 s**; GIL released. 12 tests; 321 passing. See `task_logs/task_6_3_medium_filter.md`.
- [x] **6.4 pybind11 bindings** — completed by construction: all three functions bound inline (docstrings included) during 6.1–6.3.
- [x] **6.5 RTN coordinate transform** — `teme_to_rtn()` (Vallado RSW = CDM RTN frame), validated by exact hand case + numpy cross-check + real SGP4 geometry semantics. Bonus: caught/fixed a 6.0 escape (one refresh test went live once the cache crossed 2 h staleness). 8 tests; 329 passing. See `task_logs/task_6_5_rtn_transform.md`.
- [x] **6.6 Python fine filter** — `fine_filter()` in new `backend/core/conjunctions.py`: bounded scipy minimization, exact TCA within 0.05 s of independent 0.01 s brute force; edge-bracket auto-widening; states feed `teme_to_rtn`. Key finding: sampled grids overstate fast-crosser miss (8.14 km grid vs 6.60 km true) — Phase 8 must compare refined minima. 9 tests; 338 passing. See `task_logs/task_6_6_fine_filter.md`.
- [x] **6.7 `/api/conjunctions` endpoint** + Pydantic schemas + `ConjunctionScreener`. Pure `run_screen()` core (coarse→medium→fine→RTN, sorted by miss) wired via new `propagator.get_all_satrecs()` index-aligned seam. Deterministic crosser proof (8 windows/6h, min miss 6.59 km, RTN norm==miss). 18 tests; 356 passing; 25 sats/24h = 134 ms. See `task_logs/task_6_7_conjunction_api.md`.
- [x] **6.8 Minimal globe viz** — `frontend/js/conjunctions.js`: top-left list (`pair · miss · TCA`) + orange live connecting lines for the widest-separation (visible) flagged pairs. Verified in-browser. See `task_logs/task_6_8_globe_viz.md`.
- [x] **6.9 Dataset wiring** — `slice_to_shell` (densest live Starlink shell, real: inc≈43°/483 km from 10,544) + `append_demo_crosser` seed (`demo_seed.py`), env-selectable via `ORBITWATCH_GROUP`/`ORBITWATCH_DEMO_SEED`. **Real result: 607 natural Starlink conjunctions** in 24 h (closest 0.34 km). Bonus: hardened `_download` (requests+certifi → curl) fixing a VPN-induced TLS failure. See `task_logs/task_6_9_dataset_wiring.md`.
- [x] **6.10 Tests** — coverage audit (per-stage tests already existed, built test-first) + closed the gaps: deterministic full-pipeline anchor (reproduces 6.6's brute-force TCA/miss), empty-catalog, fine-filter error-isolation. 377 passing on two consecutive runs. See `task_logs/task_6_10_tests.md`.

**✅ Done when:** one real conjunction is detected and visible end-to-end in the browser.

## Phase 7: Scale Up + Screening Volumes (Jun 21 – Jun 27)
Make it run on a real, dense catalog and use industry-standard geometry.

- [ ] **7.0 Live & epoch-matched data** — **screen the live `starlink`/`active` group directly** instead of the static hand-sliced `starlink_shell`, so the snapshot and its staleness (`scaling_tracker.md #5`) go away, and support **epoch-matched fetch-on-demand** (pull GP data fresh right before a screen). This is the real prerequisite for Phase 8's SOCRATES comparison — without epoch-matching, TCA/miss disagree from epoch drift alone. *(The background auto-refresh **scheduler** — keeping a deployed demo current with no manual step — is deferred to Phase 9.7; fetch-on-demand is sufficient through Phase 8.)*
- [ ] **7.1 Scale to dense LEO** — Starlink (~6,000) / active LEO catalog. Profile the run; confirm the coarse filter eliminates the large majority of pairs so the medium filter stays tractable.
- [ ] **7.2 Asymmetric screening volumes + co-located suppression** — (a) replace the single distance threshold with SFS Handbook per-regime RTN boxes (e.g., LEO 1: R = 0.4 km, T = 44 km, N = 51 km) — *why* RTN matters, tight radially / loose along-track. (b) **Suppress co-located / persistent-proximity pairs** (docked modules, parked constellation sats) via a min-miss floor + low-relative-velocity (or shared international-designator) guard. Asymmetric volumes alone do **not** catch these — both objects sit inside the box — so without this the screen counts non-crossing clusters as conjunctions, distorting the Phase-8 false-positive analysis. *(Found in 6.7/6.9: stations docked modules at ~0 km; Starlink 0.34 km @ 1.26 km/s — currently only hidden client-side by the viz floor.)*
- [ ] **7.3 Performance pass** — record timing ("screened N objects, M pairs, in T s"). Address the tracked scale costs: coarse→medium boundary round-trip (`scaling_tracker.md #3`) and the synchronous screen blocking the event loop (`#4` → `run_in_threadpool`). **Watch the fine stage** — with many flagged windows the Python `fine_filter` loop, not the C++ medium filter, can dominate (measured: ~13k windows → ~7 s).
- [ ] **7.4 Enable type filters** — turn on the PAYLOAD / ROCKET BODY / DEBRIS checkboxes in `controls.js` (code already exists; useful once the catalog has real types).
- [ ] **7.5 Tests** — screening-volume logic + scale/perf regression check.

**✅ Done when:** a full-catalog screen runs in reasonable time and produces a realistic list of close approaches.

## Phase 8: Validate Against SOCRATES (Jun 28 – Jul 4)
The credibility anchor: prove our detections match reality. SOCRATES (now "SOCRATES-Plus") is
**open access, no account**, and uses **SGP4 — the same method we do** (apples-to-apples).

- [ ] **8.1 SOCRATES fetcher** — pull CelesTrak conjunctions via the open query endpoint or raw-CSV download (no auth). CSV columns: `NORAD_CAT_ID_1/2`, `OBJECT_NAME_1/2`, `DSE_1/2`, `TCA`, `TCA_RANGE` (miss km), `TCA_RELATIVE_SPEED`, `MAX_PROB`, `DILUTION`. We use the first 7 (objects + TCA + range + speed); ignore `MAX_PROB`/`DILUTION` (Pc-related, de-scoped). Cache to Parquet with a ~6–12 h TTL (SOCRATES updates ~2×/day) — same fetch/serve pattern as `tle_fetcher.py`.
- [ ] **8.2 Comparison logic** — run our pipeline on the flagged objects and check: do we detect the same events? Compare our TCA vs theirs, our miss distance vs theirs. Track agreements, discrepancies, and false positives. **Epoch-match gotcha:** screen using GP data from the *same time* SOCRATES used, or epoch differences alone will cause TCA/miss-distance disagreement (see Notes).
- [ ] **8.3 Validation report** — a short report/notebook with match rate and TCA / miss-distance deltas.
- [ ] **8.4 SGP4 uncertainty doc** — state limits plainly ("~1 km accuracy near epoch; suitable for screening, not operational collision avoidance").
- [ ] **8.5 Tests** — fetcher parsing + comparison logic.
- [ ] **8.6 (Stretch) Space-Track `cdm_public` cross-check** — *optional.* Fetch real operational CDMs (SP-pipeline, higher fidelity than SGP4) from the existing Space-Track account; check whether our SGP4 screener also flags those events. A *cross-method* "we catch real threats" signal. Detection-only — we do not use the `PC` field. Skip if time is tight.

**✅ Done when:** a report shows what % of SOCRATES events we reproduce and how closely.

## Phase 9: Polish, Package, Demo (Jul 5 – Jul 10)
Turn it into something a recruiter can run and watch.

- [ ] **9.1 Frontend** — alert table sorted by miss distance with TCA countdown; conjunction lines color-coded by severity; camera fly-to on click; detail panel (RTN components, TLE ages, object types, "matched SOCRATES?" status).
- [ ] **9.2 README rewrite** — remove all ML framing, add architecture diagram, screenshots, run instructions.
- [ ] **9.3 Clean stale ML references** — `PROJECT_PLAN.md`, `requirements.txt`, `progress/week0_setup.md`, `progress/notes/week0_notes.md`, and the `1plan.md` instruction file.
- [ ] **9.4 Docker** — `Dockerfile` + `docker-compose.yml` for one-command startup (backend + frontend + SPICE kernels + C++ build). **Handle data bootstrap:** the catalog parquets are gitignored, so the image has no data — fetch the catalog on first startup (or bake a snapshot), otherwise a fresh container serves an empty screen.
- [ ] **9.5 Demo** — record a GIF/video walkthrough for the portfolio.
- [ ] **9.6 Cleanup** — remove dead code, all tests passing, docstrings on public APIs. **Remove the synthetic demo crosser seed** (`ORBITWATCH_DEMO_SEED` + `append_demo_crosser`) now that real conjunctions drive the demo — keep `build_synthetic_shell` only if still used by tests.
- [ ] **9.7 Background auto-refresh** *(moved from 7.0)* — scheduled refresh (FastAPI lifespan `asyncio` task / APScheduler, ~2–6 h) on top of the 2 h cache-TTL + the 202-Accepted background-task refactor (`scaling_tracker.md #2`), so the deployed demo stays current with no manual step.

**✅ Done when:** one-command startup works, demo is recorded, repo reads as portfolio-ready.

---

## Milestones

| Date | Milestone | Demoable? |
|------|-----------|-----------|
| ✅ Apr 30 | Foundation done — C++ SGP4, FastAPI, interactive 3D globe, 279 tests | Yes |
| ✅ Jun 21 | **Phase 6:** full conjunction pipeline end-to-end on real Starlink data — 607 natural conjunctions (closest 0.34 km), visible on globe, 377 tests | Yes |
| Jun 27 | **Phase 7:** full-catalog screening with industry screening volumes + perf numbers | Yes |
| Jul 4  | **Phase 8:** detections validated against CelesTrak SOCRATES | Yes |
| Jul 10 | **Phase 9:** polished, Dockerized, demo recorded — portfolio-ready | Portfolio-ready |

---

## Notes & Flags

> **Perf is now a feature, not a flag — with a measured caveat (6.1).** Benchmarking showed the
> Python-loop-over-C++-sgp4 overhead is only ~5% (sgp4 compute dominates; Python tuple building
> remains per-sat), so `propagate_batch` alone doesn't buy big speedups at the Python boundary.
> The order-of-magnitude win is keeping the **entire pairs×timesteps scan inside C++** (6.3 medium
> filter) where positions never become Python objects. That's the perf story we tell — backed by
> measurement, which is itself part of the story.

> **C++ where it's hot, Python where it's not.** The medium filter (O(N²) over all pairs × timesteps)
> is C++; the fine filter runs on only a few survivors and already calls the C++ propagator for each
> evaluation, so it stays in Python. Profile-driven, not dogmatic.

> **Dataset assumption:** "dense LEO" = Starlink and/or the active catalog. SOCRATES screens the
> full catalog, so Phase 8 fetches the specific objects SOCRATES flags and confirms we detect them.

> **Conjunction data sources (Phase 8).** SOCRATES-Plus = open/no-auth, SGP4-based (same method),
> ~14,000 primaries × ~29,600 secondaries, 108,000+ conjunctions, refreshed ~2×/day. Query:
> `https://celestrak.org/SOCRATES-Plus/table-socrates.php?CATNR=25544,&ORDER=MINRANGE&MAX=25`
> (params: `NAME`|`CATNR`, `ORDER`=MINRANGE/MAXPROB/TCA/RELSPEED/SSC, `MAX`≤1000) — plus a raw-CSV
> full-run download. **Epoch matching is essential**: SOCRATES screens near-future windows from a
> specific TLE epoch; to compare fairly we must use GP data from the same time. Space-Track
> `cdm_public` (account required) is an optional higher-fidelity SP cross-check — see Phase 8.6.
> Full SOCRATES/Space-Track reference lives in `progress/notes/key_information.md`.
