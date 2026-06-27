# OrbitWatch — Roadmap

**Timeline:** Mar 20 – Jul 12, 2026  ·  Weeks 0–5 ✅  ·  Final Sprint = Phases 6–9  ·  Phase 10 post-launch
**Ships as:** a **static, resume-linkable website** (not a Docker service) — **active payloads (~11k
satellites)**, screening pre-computed offline, **zero upstream calls per visit**. See the Phase 9
deployment note.

---

> **Pivot (Apr 9):** Original plan included an ML risk classifier trained on CDM data from
> Space-Track. After researching data access, the full CDM class (with covariance matrices,
> RTN positions, and observation quality metrics) requires an SSA Sharing Agreement — access
> restricted to satellite operators and government agencies. Without that data, an ML model
> would lack the features to outperform a well-tuned threshold, making it decoration rather
> than engineering. We pivoted to making the conjunction detection pipeline itself the
> centerpiece: industry-standard RTN coordinates, asymmetric (ellipsoidal) screening volumes
> matching the 19 SDS HAC tables, and validation against CelesTrak SOCRATES. The technical depth is in the
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

- [x] **7.0 Live & epoch-matched data** — `SatellitePropagator(live=True, max_sats=N)` fetches the live `starlink` group fresh on load (`ORBITWATCH_LIVE=1`), slices a dense shell in-app, and surfaces `last_fetched` + `data_max_epoch_age_days` on `/api/conjunctions`. Verified live: 10,544 → 300, freshness exposed (file fresh, epochs 13.7 d → shows *why* live matters). "Live" = fresh-on-load + manual refresh; the auto-refresh **scheduler** is **9.7** (fetch-on-demand is enough through Phase 8). 5 tests; 381 passing. See `task_logs/task_7_0_live_data.md`.
- [x] **7.1 Scale to dense LEO (profile-first)** — instrumented `run_screen(timings=…)` (passive; no behavior change) + `scripts/profile_screening.py`, then profiled the real 10,544-sat Starlink catalog. **Findings reorder the rest of Phase 7:** (1) the coarse filter does **NOT** eliminate the majority — it's inclination-blind, so **49% of pairs survive at 50 km** on a single constellation (shells stack into ~475 km); the 25 M survivor tuples are the **memory** driver (4.5 GB). (2) The **fine stage is the time bottleneck — 82–87% of wall time at every scale**, driven by window count (300 sats → 14.7 k; full → **1.43 M**), so 7.2's de-dupe/suppression is a perf lever and 7.3's fine-stage batching is the primary speedup. (3) **Operating point: `MAX_SATS=300` = 2.6 s @ 24 h** (demo default, validated); full catalog = 258 s / 4.5 GB → batch-only until 7.2 + 7.3. 5 tests; 386 passing. See `task_logs/task_7_1_scale_profile.md`.
- [x] **7.2 Ellipsoidal screening volumes + co-located suppression** — `screening_volumes.py` (SFS Table 3 **RTN ellipsoids** `(r/R)²+(t/T)²+(n/N)²≤1`, regime by **perigee** + **ecc<0.25**) + `run_screen(volumes=…)`: per-pair ellipsoid cut, medium gross = **largest semi-axis (51 km LEO 1, no-skip)**, **Conservative-drop** co-located suppression (`v_rel<0.5 AND (miss<0.05 OR shared YYYY-DDD launch)` — mirrors 19 SDS's "relative speed too small" Pc skip), **de-dupe to unique pairs** *(closes 9.1's de-dupe item, server-side)*. Default on `/api/conjunctions`; `threshold_km` = optional legacy-Euclidean override; `screening_regime` + `suppressed_count` surfaced; output stays geometric (no Pc). **Validated on real data:** Starlink shell (300) → **7 conjunctions**, all radially tight (r≈±0.3 km) with ~26 km in-track — real co-altitude crossings a 25 km Euclidean cut *misses*; live stations → **0 events, 46 suppressed** (docked modules gone, was 57); synthetic crosser correctly excluded (3.6 km radial ≫ 0.4 km axis → the default demo shifts to a real Starlink shell). The radial coarse-pad tightening (perf) deferred to 7.3 (needs a measured SGP4 radial-drift bound). +23 tests; 409 passing. See `task_logs/task_7_2_screening_volumes.md`.
- [x] **7.3 Performance pass (fine stage + cap/threadpool)** — replaced the per-window `scipy.minimize_scalar` fine stage with `fine_filter_batch`: **Newton on the relative range-rate** (`t ← t − (Δr·Δv)/|Δv|²`, the standard operational TCA solve), refining **all windows together** through one `propagate_batch` crossing per step (GIL released), vectorized in NumPy, chunked by `_FINE_CHUNK`. **Fine stage ~3.7× faster** (same-machine A/B, 300/500/800) with **byte-identical event counts** (124,810 on the full catalog), cross-validated against the scipy oracle *and* a 0.01 s brute-force grid. `/api/conjunctions` now runs in `run_in_threadpool` under `_propagator_lock` (guards the screen **and** the position/track endpoints against a Satrec-cache race — caught by an adversarial review) + a `413` cap (`ORBITWATCH_MAX_SCREEN_SATS`, default 1500). Pure-Python, **no `.so` rebuild**. +12 tests; 421 passing. See `task_logs/task_7_3_fine_stage_batch.md`.
  - **Deferred (still open):** (2) **coarse→medium memory fusion** (`scaling_tracker #3`, the only C++ change — 25 M tuples / 4.5 GB) — the cap removes its urgency for the interactive path. (4) **radial coarse-pad tightening** — shrink the flat ~51 km pad toward `R + SGP4 radial-drift bound`; needs a measured radial-drift margin to stay no-skip. (new) **`#7` fine-stage result-dict streaming** — memory-only, +0.7 GB at full catalog. Full catalog stays batch-only.
- [x] **7.4 Enable type filters** — the `controls.js` checkboxes were already wired; the real blocker was upstream: **gp.php omits `OBJECT_TYPE`** (a SATCAT field), so every object was `None` → `"UNKNOWN"` → no checkboxes. Fix: derive `object_type` from the CelesTrak **name convention** (`DEB`/`R/B` tokens, else PAYLOAD; token-matched so `DEBUT` isn't debris) in `tle_fetcher`, filling only nulls (a real `OBJECT_TYPE` is preserved) across all read paths — including `load_cached`, so existing caches get types with no re-fetch. Default `stations` now surfaces PAYLOAD + DEBRIS; conjunction events read real types too. SATCAT join = authoritative upgrade (deferred); conjunction-line hiding (step 4) deferred. +8 tests; 429 passing. See `task_logs/task_7_4_object_types.md`.
- [x] **7.5 Tests** — `TestScaleRegression` (3 cross-task locks, deterministic + offline): (1) the 7.3 batched Newton fine stage reproduces the scipy oracle across **257 real windows** of a 300-sat shell (<10 m / <50 ms / <1 mm/s) — the "byte-identical on dense catalogs" headline, previously only asserted on ~8 crosser windows; (2) a full shell screen is **byte-identical run-to-run** (locks the count regression signal); (3) the 7.2 **no-skip wiring** — a spy confirms `run_screen(volumes=…)` passes `medium_filter` the largest semi-axis (51 km), not the radial axis or the box corner. **Mutation-checked** (inject +100 ms TCA → all 257 fail; radial-axis bug → gross 0.4 ≠ 51 fails) so the green tests provably bite; 3× flake-clean. +3 tests; 432 passing. See `task_logs/task_7_5_tests.md`. **→ Phase 7 COMPLETE.**

**✅ Done when:** a full-catalog screen runs in reasonable time and produces a realistic list of close approaches. ✅ **Met** (7.1 profile + 7.3 batched fine stage; demo operating point MAX_SATS=300 ≈ 1.7 s @ 24 h, full catalog batch-only 117 s).

## Phase 8: Validate Against SOCRATES (Jun 28 – Jul 4)
The credibility anchor: prove our detections match reality. SOCRATES (now "SOCRATES-Plus") is
**open access, no account**, and uses **SGP4 — the same method we do** (apples-to-apples). **Confirmed
parameters (research Jun 24):** SOCRATES screens **all active payloads vs the full catalog**, **3×/day**,
**7 days forward**, flagging everything **within 5 km at TCA** (148,008 conjunctions as of Jun 15).
**Epoch-matching — SOLVED, see Notes:** SOCRATES reports `DSE` (days-since-epoch) per object, so we can
*verify* an epoch match against the free CelesTrak feed instead of assuming one.

> **⚠ Validation scope ≠ display scope (added Jun 24).** The deployed site is satellites-only, but
> validation fetches the **actual** objects SOCRATES flagged **by `CATNR` — and most secondaries are
> debris / rocket bodies** (payload-vs-debris is the common close approach). Restricting validation to
> payload-payload pairs would be an unrepresentative cherry-pick, so Phase 8 screens **whatever SOCRATES
> lists, debris included**. Satellites-only is a *rendering* choice for the live globe, never a validation
> limit — keep the two scopes separate.

- [x] **8.1 SOCRATES fetcher** — `SOCRATESFetcher` (`backend/core/socrates_fetcher.py`) downloads the **bulk full-run CSV** `https://celestrak.org/SOCRATES/sort-minRange.csv` (~148 k conjunctions, RFC-4180) — **the HTML-scraping plan was obsoleted** when the format-page check found the official static CSV (`FORMAT=csv` on the query endpoint is ignored). Parses to a typed frame (objects + both **`DSE`** + TCA + range + speed; drops `MAX_PROB`/`DILUTION`), Parquet-cached with an **8 h TTL**, atomic write, graceful fallback. Slices: `top_n`/`by_name`/`by_catnr`/`between` — the last two reproduce the useful query semantics (`CATNR`, `NAME=a,b`) locally. Shared `http_fetch.download_text` (requests→curl-TLS-fallback) now used by both fetchers. Gotchas captured: `OBJECT_NAME` has a trailing `[status]` tag (strip trailing only — keep internal parens); **object 1/2 are positional, NOT primary/secondary**; `.str.contains` needs `regex=False`. Live: 147,814 rows, 0 nulls, closest 0.016 km, 11.9 s / 0.03 s cached. +26 tests; 458 passing. See `task_logs/task_8_1_socrates_fetcher.md`.
- [x] **8.2 Comparison logic** — `socrates_compare.py`: **`match_events`** (pure, reusable — Phase 9's badge reuses it) pairs our screen output to SOCRATES rows by `{id1,id2}` + TCA proximity, computes per-event TCA/miss deltas + a summary (reproduction rate + |Δ| stats **bucketed by `DSE`**); **`compare_against_socrates`** (orchestrator) fetches GP per flagged object by `CATNR` (**incl. debris secondaries**; `max_objects` cap so a broad slice can't fire thousands of requests), builds satrecs via the new `propagator.build_satrecs_and_meta`, screens the slice's TCA window in **legacy 5 km Euclidean** (not SFS), matches both `DSE_1`/`DSE_2`, and sets **`epoch_ok` via `DSE`** (our element age at TCA == SOCRATES's `DSE`). **Live ISS:** 3/9 reproduced, matched agree **TCA <5 s / miss <0.32 km**; `epoch_ok=0` (current `gp.php` rolled the epochs SOCRATES used) with misses clustering at higher `DSE` — the epoch-drift degradation, surfaced honestly. **8.3 lever:** the `gp_history` backstop pulls the exact older snapshot to lift reproduction. +14 tests; 472 passing. See `task_logs/task_8_2_socrates_compare.md`.
- [x] **8.3 Validation report** — `scripts/validate_socrates.py` writes `validation/socrates_report.md` + figures over three slices (ISS / top-N closest / Starlink), match rate + TCA/miss deltas **segmented by `DSE`**. **Stage A** = current-GP baseline; **Stage B** (the `gp_history` lever) pulls each object's *epoch-matched* historical elset from **Space-Track** (`spacetrack_fetcher.py` — cookie auth, no API key; `_coerce_numeric` because ST JSON is all-strings; `BulkGPAdapter` reuses the 8.2 orchestrator's `fetch_by_catnr` seam unchanged). **Live (current → epoch-matched):** ISS 3/9→8/9, top-25 8/25→**25/25 (100%)**, Starlink-40 8/40→**40/40 (100%)**; every matched event **ΔTCA = 0.000 s, Δmiss = 0.000 km** (byte-level same-method agreement). Current-feed reproduction is low purely from epoch drift (`epoch_ok=0`); the by-`DSE` curve shows it. Pure formatters/plots + `.env`-loaded creds + `--current-source spacetrack` (CelesTrak TLS was VPN-blocked). +68 tests; 540 passing. See `task_logs/task_8_3_validation_report.md`.
- [ ] **8.4 SGP4 uncertainty doc** — state limits plainly ("~1 km accuracy near epoch; suitable for screening, not operational collision avoidance"). **Corroborating authorities (research Jun 24):** MathWorks' Aerospace Toolbox *Satellite Conjunction Finder* uses the **same TLE/SGP4 + root-find-on-the-distance-gradient TCA method we do** (independent confirmation our pipeline is textbook) and carries the same "don't use public TLEs operationally" disclaimer; cite **NASA/SP-20205011318** (Spacecraft Conjunction Assessment handbook) for the screening-vs-operational line.
- [x] **8.5 Tests** — done per-task across the Phase-8 surface (each sub-task ships its own `@4test`): `test_socrates_fetcher.py` (8.1, saved CSV fixture), `test_socrates_compare.py` (8.2 + `build_epoch_targets`/Stage-B integration), `test_spacetrack_fetcher.py` / `test_socrates_report.py` / `test_socrates_plots.py` / `test_validate_socrates.py` (8.3). All offline/deterministic, network mocked, live fetches opt-in skipped. **540 passing, 4 skipped.**
- [ ] **8.6 (Stretch) Space-Track `cdm_public` cross-check** — *optional.* Fetch real operational CDMs (SP-pipeline, higher fidelity than SGP4) from the existing Space-Track account; check whether our SGP4 screener also flags those events. A *cross-method* "we catch real threats" signal. Detection-only — we do not use the `PC` field. Skip if time is tight.

**✅ Done when:** a report shows what % of SOCRATES events we reproduce and how closely.

## Phase 9: Polish & Deploy as a Static Website (Jul 5 – Jul 12)
Turn it into a live, resume-linkable site that runs the **active-satellite catalog** with **zero upstream calls per visit**.

> **Deployment architecture (decided Jun 24).** Ship as a **static snapshot site** on free hosting
> (GitHub / Cloudflare Pages) — *not* a Docker service. A scheduled **CI job** (GitHub Actions, a
> few×/day) fetches the **active payloads**, runs the **real C++ screening offline**, and publishes one
> compact `snapshot.json`. The browser renders + animates from that single cached file, **propagating positions
> client-side** (`satellite.js`) so we ship *orbital elements, not* bulky position timeseries. Why this
> shape: every visitor reads the same cached snapshot → CelesTrak is hit on a schedule, never per visit
> (no IP-block risk), no server to run or pay for, and the Cesium Ion token is **domain-restricted** so a
> public site can't leak quota. **What goes in the snapshot:** object elements (for in-browser
> propagation) + the conjunction list (TCA, miss, RTN, rel-speed, regime) + freshness/validation metadata
> (`last_fetched`, `DSE`, SOCRATES-match status). **What stays out:** the Python/FastAPI backend, SPICE
> kernels, raw position timeseries, and any live API call. Default dataset = **active payloads (~11k
> satellites)** with **toggleable layers** (by group/type); **debris + rocket bodies (~14k) are dropped
> on purpose** — they're the bulk of the catalog and the heaviest strain, and *debris-collision is a
> future phase* (see Backlog). "Light" = nothing it doesn't need, not feature-stripped. *(Local dev keeps
> the FastAPI backend; the deployed site is the static export.)*
>
> **CI capacity (the real question) — it fits, with a fallback ladder.** Heaviest screen we've measured
> is **~117 s / ~5.2 GB** (worst case, dense full Starlink; satellites-only sits at/below it). A **public**
> repo's GitHub-hosted Linux runner is **4 vCPU / 16 GB / unlimited minutes** (6 h/job cap) → fits with
> headroom. ⚠ A *private* repo runner is only 2 vCPU / **7 GB** / 2,000 min/mo — so **keep the repo
> public** (which the resume link wants anyway). If it ever outgrows that: (1) trim the set → (2) **Phase
> 10 path filter** (cuts time *and* memory — its main job) → (3) **self-hosted runner** (your machine/VM)
> → (4) escape hatch: GitHub Actions isn't load-bearing — *any* scheduler that runs the script and pushes
> `snapshot.json` works (e.g. a local cron). Memory, not time, is the thing to watch.

**Ordered by dependency:**

- [ ] **9.1 Clean stale ML references** *(do first — docs/README start clean)* — `PROJECT_PLAN.md`, `requirements.txt`, `progress/week0_setup.md`, `progress/notes/week0_notes.md`, the `1plan.md` instruction file.
- [~] **9.2 Frontend: interactive core + active-satellite scale-up** — *core done early (Jun 22, `task_logs/task_9_1_conjunction_ux.md`):* click-a-conjunction → fly-to + TCA-5min jump + yellow orb + both trails; RTN/miss/rel-speed/TCA detail panel; clickable list; label de-overlap; LIVE button; `N flagged · M sats` header. **Scale-up to ~11k satellites (the real new work):** Cesium **LOD/culling**, **web-worker `satellite.js` propagation** (throttled cadence + interpolation — full SGP4 on ~11k/frame is too heavy), **layer toggles** by group/type, and "animate only what's needed" (conjunction-involved sats fully tracked, the rest low-cadence). **Still open from core:** alert-table sort / TCA countdown, severity colors, "matched SOCRATES?" badge (from 8.2). *(De-dupe is server-side since 7.2.)*
- [ ] **9.3 Snapshot build pipeline** *(the deploy artifact)* — an offline script: fetch **active payloads** → run the C++ screen (Euclidean + SFS lenses) → emit a compact `snapshot.json` (elements + conjunction events + metadata above). One self-contained artifact the static site reads; reuses `tle_fetcher` + `run_screen` unchanged. Worst-case (dense Starlink) screen is ~117 s / 5.2 GB — fits a public-repo runner; Phase 10 then cuts it.
- [ ] **9.4 Static deploy** *(replaces Docker)* — point the frontend at `snapshot.json` instead of the live API; host frontend + snapshot on GitHub / Cloudflare Pages; **domain-restrict the Cesium Ion token**; verify the deployed URL loads with no network call to our former backend.
- [ ] **9.5 CI refresh job + snapshot archive** *(was 9.7 — now THE refresh mechanism)* — GitHub Actions cron (a few×/day, **public repo** for the 16 GB / unlimited-minutes runner): build the `.so`, run the 9.3 pipeline, publish the **live** `snapshot.json`, **and append a compressed `snapshots/<ISO-timestamp>.json.gz` to an archive** (a `data` branch or object storage — **never `main`**, to avoid history bloat). This is what keeps the site current *and* shields the upstream sources. Replaces the in-server scheduler / 202-refactor entirely for the deployed path (`scaling_tracker #2` closes for prod; local backend keeps manual `POST /api/refresh`). The archive backs the 9.9 prediction-evolution view + the Phase-8 "what we predicted vs. reality" story.
- [ ] **9.6 README rewrite** *(after the site is live — real link + screenshots)* — remove all ML framing; architecture diagram (CI → snapshot → static globe); screenshots of the deployed site; the live URL; run-locally instructions.
- [ ] **9.7 Demo** — record a GIF/video walkthrough of the **deployed** site for the portfolio.
- [ ] **9.8 Cleanup** — dead code removed, docstrings on public APIs, all tests green. **Remove the synthetic demo crosser seed** (`ORBITWATCH_DEMO_SEED` + `append_demo_crosser`) now that the real active catalog drives the demo — keep `build_synthetic_shell` only if still used by tests.
- [ ] **9.9 (Stretch) Snapshot-history prediction-evolution view** — read the 9.5 archive and show how a conjunction's predicted **miss distance evolves across refreshes** as its TCA approaches (shrinking vs. growing = the real "is this getting more dangerous?" signal). This is the *legitimate* version of the old CDM-evolution idea and pairs with Phase 8 ("what we predicted N days out vs. what happened"). Off the critical path; do only if the core site is solid.

**✅ Done when:** a public URL (on the resume) loads the active-satellite globe + validated conjunctions, refreshes itself a few×/day via CI, and makes **no upstream call on a page visit**.

## Phase 10: Geometric Path Filter — Full-Catalog Performance (post-launch / long-term)
**Built last, after the portfolio is shippable (Phases 8–9 done).** This is the one optimization the
Jun 24 SOCRATES research surfaced: the **smart-sieve "path filter"** (Alfano 2002 / Hoots-Crawford-
Roehrich 1984) — the orbit-geometry stage SOCRATES has and we don't. It attacks a weakness we measured
ourselves (7.1 / `scaling_tracker #3` + `#8`): `coarse_filter` is **inclination-blind**, so on a dense
constellation **49% of pairs survive** (25 M tuples, 4.5 GB) and the medium filter time-steps them all.

> **Why it's worth doing (the rationale, sharpened by the Phase-9 deploy).** Most of those survivors
> are pairs whose *orbits never actually approach* — they only share an altitude band. A geometric
> pre-cut drops them before the expensive time-stepping, cutting **both** memory **and** wall time on the
> active-payload catalog (still Starlink-dense, so the win holds). That screen is now **production**: the
> Phase-9.5 CI job runs it a few×/day to publish the snapshot, so every second/GB shaved is **real CI cost
> and faster refreshes** — *less
> compute → lower cloud cost*, the kind of measured efficiency win an employer cares about. It's deferred
> to last (not a launch blocker — the CI job tolerates the 117 s screen fine) and is the **only remaining
> C++ change**, so it forces a `.so` rebuild → must be verified in the Phase-9.5 CI build.

- [ ] **10.1 Spec the no-skip geometric bound** — derive a **conservative lower bound** on the orbit-to-orbit minimum distance from relative inclination + nodal geometry (MOID-style), so the filter can *never* drop a pair the medium filter would flag. ⚠ **The hard part:** LEO nodes precess (~5°/day for Starlink), so the bound must hold across the whole 7-day window — mirror 6.3's velocity-aware no-skip discipline. Cite Hoots-Crawford-Roehrich + Alfano smart sieve.
- [ ] **10.2 C++ `path_filter` + pybind11 binding** — new `orbitcore` function (orbital geometry → drop pairs whose orbits never approach within `threshold + precession margin`), inserted between `coarse_filter` and `medium_filter`. Keeps the cut inside C++ (no new Python materialization; complements `scaling_tracker #3`).
- [ ] **10.3 Wire into `run_screen`** — new optional stage in the cascade; preserve the index-aligned `(i,j)` contract; gate behind a flag + profile hook so it's measurable and reversible.
- [ ] **10.4 Validate + re-profile** — **no-skip equivalence:** assert the path filter drops **zero** pairs that the full medium→fine screen would have flagged (cross-check on the dense shell *and* the full 10,544-sat catalog). Then re-run `scripts/profile_screening.py` to quantify the survivor-count drop (target: well below 49%) and the wall-time / peak-RSS improvement vs the 7.1 baseline.
- [ ] **10.5 Tests** — no-skip property test (the critical lock, mutation-checked) + a survivor-reduction regression + the existing scale-regression suite still byte-identical (the filter changes *speed*, never *results*).
- [ ] **10.6 CI-build verification + close** — rebuild the `.so` in the Phase-9.5 CI job, run the active-payload snapshot screen **in CI**, confirm it completes faster/lighter with identical event counts and the published snapshot is unchanged (results, not speed). Update `scaling_tracker #3`/`#8` → resolved, roadmap → Phase 10 done.

**✅ Done when:** the CI snapshot screen is measurably faster and lighter, **no real conjunction is dropped** (no-skip proven on the full catalog), and the published snapshot is byte-identical to the pre-filter one.

## Backlog (future, unscheduled)
- **Debris + rocket-body collision screening** — the deployed site is satellites-only (active payloads) to keep the engine and the browser light. Adding the ~14k debris/R-B objects back as *secondaries* (screen active payloads **against** debris) is the natural next capability — it's the harder, more impressive SSA problem, and a clean future phase once the path filter (Phase 10) has cut the per-object cost. Object types already derived (7.4), so the data is ready.
- **Space-Track `cdm_public` cross-method check** — the Phase 8.6 stretch, if not done in-sprint.

---

## Milestones

| Date | Milestone | Demoable? |
|------|-----------|-----------|
| ✅ Apr 30 | Foundation done — C++ SGP4, FastAPI, interactive 3D globe, 279 tests | Yes |
| ✅ Jun 21 | **Phase 6:** full conjunction pipeline end-to-end on real Starlink data — 607 natural conjunctions (closest 0.34 km), visible on globe, 377 tests | Yes |
| ✅ Jun 23 | **Phase 7:** full-catalog screening with industry **ellipsoidal** RTN screening volumes + measured perf numbers — batched fine stage, type filters, scale-regression locks, 432 tests | Yes |
| Jul 4  | **Phase 8:** detections validated against CelesTrak SOCRATES | Yes |
| Jul 12 | **Phase 9:** deployed as a static website — active-satellite globe + validated conjunctions, CI-refreshed a few×/day (+ snapshot archive), zero per-visit upstream calls, resume-linkable | **Live URL** |
| post-Jul 12 | **Phase 10 (long-term):** geometric path filter — active-catalog CI screen faster & lighter (less compute → lower cost), no-skip proven, verified in CI | Yes |

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
> **The deployed site (Phase 9) goes wider, but satellites-only:** **active payloads (~11k)** — debris +
> rocket bodies dropped (a future phase) — screened **offline in CI** and baked into a static snapshot,
> so "catalog at scale" is a *deployment* concern (CI wall-time / snapshot size / in-browser rendering),
> not a per-request one.

> **Deployment = static snapshot, not a server (Phase 9, decided Jun 24).** Fetch + screen run in a
> scheduled CI job (a few×/day); the browser reads one cached `snapshot.json` and propagates positions
> client-side (`satellite.js`). This is the answer to "don't let every visit hammer CelesTrak / leak the
> Ion token": visitors touch only the CDN-served snapshot; CelesTrak sees scheduled CI hits; the Ion token
> is domain-restricted. The Python/C++ backend stays a **local-dev + CI tool**, not a hosted service.
> Full-catalog CI wall-time is the motivation for the Phase-10 path filter.

> **Industry screening model (Phase 7.2) — SFS Handbook V1.7, HAC vs HAC.** The 19 SDS screening
> volume is an **RTN ellipsoid**, not a box: `(r/R)² + (t/T)² + (n/N)² ≤ 1`, semi-axes from HAC
> Table 3 by **perigee** (with an **ecc < 0.25** gate). Our data is **LEO 1** (R/T/N = 0.4 / 44 / 51 km):
> radial tight, along/cross-track loose. The medium-filter no-skip gross threshold = the **largest
> semi-axis (51 km)**, not the box-corner. 19 SDS itself **skips Pc when relative speed is "too small"**
> (user-settable) → precedent for our co-located suppression; same-launch objects share the
> **YYYY-DDD** international-designator prefix. **RTN = RIC = UVW.** We output **geometry + regime,
> never Pc** (no covariance). Full model + page citations: `progress/week6and7_planning/sfs_handbook_summary.md`
> (re-read addendum, Jun 22).

> **Conjunction data sources (Phase 8) — updated Jun 24 after a research pass.** SOCRATES-Plus =
> open/no-auth, SGP4-based (same method), all active payloads × full catalog, 148,008 conjunctions
> (Jun 15), refreshed **3×/day**, **7-day** forward window, **5 km** screening at TCA. Query:
> `https://celestrak.org/SOCRATES-Plus/table-socrates.php?CATNR=25544,&ORDER=MINRANGE&MAX=25`
> (params: `NAME`|`CATNR`, `ORDER`=MINRANGE/MAXPROB/TCA/RELSPEED/SSC, `MAX`≤1000) — plus a raw-CSV
> full-run download.

> **Epoch-matching — the issue and the fix (Jun 24 research).** The worry: SOCRATES screens from
> *its* recent TLE epoch (~3×/day); CelesTrak `gp.php` only serves the *latest* elements, so a late
> fetch disagrees on TCA/miss from epoch drift alone (~5–10 km/day), not method. **The fix is in the
> SOCRATES data itself:** every conjunction carries **`DSE` (days-since-epoch)** = the age of the
> elements SOCRATES used to that TCA. So we fetch current CelesTrak GP, compute *our* element's age at
> the same TCA, and **compare to `DSE` → the match is verified, not assumed** — zero auth, reuses our
> fetcher. Layered solution, preference order: **(1) primary — CelesTrak `gp.php` + `DSE` verification**
> (free; filter/segment to small-`DSE` for tight agreement); **(2) backstop — Space-Track `gp_history`**
> (historical elsets queryable by epoch; Jose has the account) for the *few* top-N objects that
> drift — ⚠ **rate-limited (~30 req/min · 300/hr; verified Jun 24)**, so batch objects with
> comma-delimited `CATNR` lists and pull only the few that need it, never bulk; **(3) bonus —
> Space-Track `cdm_public`** = real operational CDMs (SP pipeline) for an optional *cross-method*
> detection check (8.6, no `PC` field).

> **How SOCRATES screens vs. how we do (Jun 24 research).** SOCRATES runs **STK/CAT** (commercial
> Satellite Tool Kit Conjunction Analysis) on STK's NORAD **SGP4**, via the **Alfano "smart sieve"**
> (2002): **perigee-apogee filter → path filter → time filter → fine TCA**, then Alfano **MaxProb**
> (fixed covariance). Our pipeline = **coarse (altitude-band) → medium (time-stepped) → fine (Newton
> TCA)** — same SGP4, same perigee-apogee idea (our `coarse_filter`), same time-step + fine-TCA stages.
> **Differences (all citable, none a correctness gap):** (a) we **lack the smart-sieve "path filter"**
> (orbit-geometry minimum-distance pre-cut that drops pairs whose *orbits* never approach regardless of
> timing) — a *perf* gap, not a miss gap: we time-step some pairs SOCRATES skips, finding the same
> events more slowly. (b) We **built our own C++/pybind11 SGP4 + screening** rather than buying STK — a
> *strength* to highlight. (c) SOCRATES screens a **simple 5 km sphere**; we have **both** a 5 km
> Euclidean mode (matches SOCRATES for validation) **and** the SFS **RTN-ellipsoid** mode (matches 19
> SDS operational volumes) — we span both criteria. (d) We deliberately **emit no Pc** (SOCRATES's
> MaxProb uses *assumed*, not measured, covariance — so this is an honest narrowing, not a deficiency).
> The smart-sieve path filter is **now scheduled as Phase 10** (post-launch, long-term) — see below.

> **Other validation sources surveyed (Jun 24) — SOCRATES still wins.** **TraCSS** (NOAA/Office of
> Space Commerce civil SSA, 52 pilot users Jun 2026): CDMs still routed through Space-Track `cdm_public`
> today, API gated to registered owner/operators — not a new open feed; one-line "where this is heading"
> mention. **ESA DISCOSweb:** object-*characteristics* DB (sizes/launches), **not** a conjunction feed,
> ESA-member-state accounts only. **CelesTrak historical archives** (`/NORAD/archives/request.php`):
> email-request form, not an API — manual last-ditch only. SOCRATES remains the one source that is
> simultaneously open, automatable, and method-aligned (SGP4). Full reference + URLs in
> `progress/notes/key_information.md`.
