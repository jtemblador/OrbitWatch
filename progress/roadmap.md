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
- [ ] **6.3 C++ medium filter** — for surviving pairs, step through time (~60 s steps over 24–72 h) and flag windows where the TEME distance drops below a threshold. This is the O(N²) hot loop — belongs in C++.
- [ ] **6.4 pybind11 bindings** — expose batch SGP4 + coarse + medium filters to Python.
- [ ] **6.5 RTN coordinate transform** — convert a pair's relative position from TEME XYZ to Radial / In-Track / Cross-Track (the frame every real conjunction report uses). ~20 lines, large credibility payoff.
- [ ] **6.6 Python fine filter** — `scipy.optimize.minimize_scalar` inside each flagged window for exact TCA + minimum miss distance. Stays in Python by design: runs only on a few survivors, and each distance evaluation already calls the C++ propagator. C++-where-hot / Python-where-not.
- [ ] **6.7 `/api/conjunctions` endpoint** + Pydantic `ConjunctionEvent` / `ConjunctionResponse` schemas (TCA, miss distance, relative speed, RTN components, both satellites' metadata).
- [ ] **6.8 Minimal globe viz** — draw a line between one flagged pair + a bare alert list, to prove the data reaches the frontend.
- [ ] **6.9 Dataset wiring** — load a ~300-sat dense subset (e.g., one Starlink shell). If no natural close approach appears early, use a known/synthetic close pair to prove the plumbing.
- [ ] **6.10 Tests** — each filter stage + a full-pipeline integration test.

**✅ Done when:** one real conjunction is detected and visible end-to-end in the browser.

## Phase 7: Scale Up + Screening Volumes (Jun 21 – Jun 27)
Make it run on a real, dense catalog and use industry-standard geometry.

- [ ] **7.1 Scale to dense LEO** — Starlink (~6,000) / active LEO catalog. Profile the run; confirm the coarse filter eliminates the large majority of pairs so the medium filter stays tractable.
- [ ] **7.2 Asymmetric screening volumes** — replace the single distance threshold with SFS Handbook per-regime RTN boxes (e.g., LEO 1: R = 0.4 km, T = 44 km, N = 51 km). This is *why* RTN matters — tight radially, loose along-track.
- [ ] **7.3 Performance pass** — batch SGP4 carries the load; record timing numbers ("screened N objects, M pairs, in T seconds").
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
- [ ] **9.4 Docker** — `Dockerfile` + `docker-compose.yml` for one-command startup (backend + frontend + SPICE kernels + C++ build).
- [ ] **9.5 Demo** — record a GIF/video walkthrough for the portfolio.
- [ ] **9.6 Cleanup** — remove dead code, all tests passing, docstrings on public APIs.

**✅ Done when:** one-command startup works, demo is recorded, repo reads as portfolio-ready.

---

## Milestones

| Date | Milestone | Demoable? |
|------|-----------|-----------|
| ✅ Apr 30 | Foundation done — C++ SGP4, FastAPI, interactive 3D globe, 279 tests | Yes |
| Jun 20 | **Phase 6:** one conjunction detected end-to-end, shown on globe | Yes |
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
