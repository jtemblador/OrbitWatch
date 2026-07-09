# Stage 1b — Time-Filter Spec (validated prototype → C++)

**Status:** Gate PASSED on the production CI slice (Jul 8, 2026). Breadth runs
(full Starlink / full active) recorded below. The construction here is
*executable* — `time_filter_gate.py` in this directory implements it exactly and
is the independent oracle for the C++ (10.2/10.5).

## What the filter does

Hoots-Crawford-Roehrich Filter III: a conjunction can only occur while **both**
satellites are inside a small angular window around the **mutual node line**
(the intersection of their two orbit planes). Everything below exists to make
that statement *provably no-skip* on SGP4 orbits over a ≤24 h screen — the
naive version of this filter is wrong in four separately-measured ways (§ The
four failure modes).

**Exactness of the underlying predicate (verified):** at the fine-refined TCA
of 8,206 real events, the osculating perpendicular plane-distances |r₁·ĥ₂|,
|r₂·ĥ₁| never exceeded the gross threshold — 0 exceptions. `d(P₁,P₂) ≥
dist(P₁, plane₂)` is a mathematical necessity; all engineering risk lives in
*predicting* the geometry cheaply, not in the predicate.

## The validated construction

**Anchoring (3 propagations per sat — the whole trick):**
Propagate each sat at `t₀ = jd_start`, `t_mid = jd_start + T/2`, `t₁ = jd_start
+ T`; convert each state to osculating elements (rv2coe). Then:

- **Phase:** mean longitude `λ = ω + M` at t₀. (λ, not ω and M separately —
  see failure mode 3.)
- **Rates:** chord finite-differences over [t₀, t₁]: `λ̇ = (λ₁ − λ₀)/T`
  (unwrapped against the satrec's `mdot + argpdot` reference — branch-safe
  because drag shifts the true advance by only degrees over one day),
  `Ω̇ = (Ω₁ − Ω₀)/T`. Chord rates capture the TRUE local drift — J2 secular +
  drag + deep-space resonance — which at-epoch rates miss (failure mode 2).
- **Per-sat curvature margin:** `curv = 1.5 · |d₂ − d₁| / 2` where d₁, d₂ are
  the two half-chord advances of λ. For a quadratic drift this equals 1.5× the
  chord's max interior deviation, exactly; it is model-free and measures
  whatever SGP4 actually does (failure mode 4). ~0 for normal sats; ~6° for an
  actively-reentering 143 km-perigee Starlink.

**The per-time membership test (pair i, j at time t):**
1. Advance: `Ω_k(t) = Ω_k⁰ + Ω̇_k·Δt`, `λ_k(t) = λ_k⁰ + λ̇_k·Δt`,
   `u_k(t) = λ_k(t) + EoC(M_k(t), e_k)` with the equation of center computed on
   consistently wrapped M and itself re-wrapped (|EoC| ≤ 2e < π).
2. Node geometry *recomputed at t* from (i_k, Ω_k(t)): `k̂ = ĥ₁×ĥ₂`,
   `sin I_R = |k̂|`, node arguments u₁ⁿ, u₂ⁿ via atan2 in each plane.
   (Recomputing at t makes nodal precession exact — no Ω drift margin.)
3. Windows: `δu_k = arcsin(min(1, D_eff / (r_p,k · sin I_R))) + margin_base +
   curv_k`, with `D_eff = gross + 10 km` (mean-vs-osculating radial).
4. Active iff `angdist_π(u₁, u₁ⁿ) ≤ δu₁ AND angdist_π(u₂, u₂ⁿ) ≤ δu₂`
   (mod π — both antipodal crossings count), OR the pair is near-coplanar
   (`r_p·sin I_R ≤ D_eff` for either sat — no angular constraint possible),
   OR either sat failed an anchor propagation (decaying — conservative keep).

**margin_base = 0.5°** (validated at 0.25°; ship with 2× headroom — the
coverage cost of the extra 0.25° is ~zero because coverage is dominated by the
coplanar floor).

**From membership to time windows (the C++ realization, 10.2):** evaluating
the membership per pair-step costs as much as the distance check it replaces —
the win requires *precomputed per-pair scan intervals*. From the anchored
elements: each sat crosses its node argument twice per rev at times solvable
from `u_k(t) ≡ u_k ⁿ(t) (mod π)` (u̇ ≈ λ̇ dominant, Newton 1–2 iterations);
half-width `δt_k = δu_k / u̇_k,min` with `u̇_min = λ̇·(1−e)²/(1−e²)^{3/2}`
(apogee rate — conservative for eccentric orbits). Intersect the two sats'
periodic interval sets; **pad every resulting interval by ±1 medium step** (so
the medium filter's interval bound and the fine bracket survive — this is
where the sampled-flag-vs-TCA offset is absorbed, in time, exactly).

## The four failure modes (each measured, each with its cure in the design)

| # | Failure | Measured symptom | Cure |
|---|---------|------------------|------|
| 1 | Validating at `jd_flag` (medium's sampled step) instead of TCA | 85 k false "violations" — even the *exact* osculating predicate fails at jd_flag (sats are ~1 step from the crossing) | Contract is TCA-coverage; C++ pads windows ±1 step in time |
| 2 | Advancing mean elements from **epoch** with linear rates | 35° along-track error at a 21.8-day-old epoch (drag t² compounds) | Anchor at window start; chord rates over the window |
| 3 | Osculating (ω, M) split for **near-circular** orbits | One ordinary e=0.0011 sat produced margin-independent misses — J2's forced ecc-vector wobble (~1e-3) ≈ e, so the split is ill-conditioned and the M-unwrap can pick the wrong branch | Equinoctial: chord λ=ω+M (well-conditioned sum); keep the split only inside EoC where error ≤ 2e |
| 4 | Flat margin vs **extreme-drag** objects | Reentering sats (143–217 km perigee, ndot up to 0.127 rev/day²; one bogus bstar=−0.19) need up to ~6°, normal sats ~0.1° | Per-sat measured curvature margin (midpoint second difference), ×1.5 |

## Validation results (every real event checked at its TCA)

| Catalog | Events checked | Uncovered @ base 0.25° | @ 0.5° | Mutation (0 margin) | Coverage @0.5° → medium reduction |
|---|---|---|---|---|---|
| Active head-800 (low-inc-biased worst case) | 1,401 | 0 | 0 | 1 | 17% → ~6× (15% coplanar slice) |
| **Active head-5000 (THE CI slice, 24 h/30 s)** | **197,523** | **0** | **0** | **59** | **2.47% → ~41×** |
| Starlink 10,544 (3 h) | 253,392 | 0 | 0 | 256 | 0.77% → ~130× |
| Active full 15,708 (6 h) | 951,683 | 0 | 0 | 351 | 0.60% → ~168× |

**Total: 1,403,999 real events validated at their TCAs — zero uncovered at the
0.25° base margin, on every catalog; the mutation check bites on every catalog.**

**5 km Euclidean mode (the SOCRATES-validation screen) also holds** (review
round): the CI slice's 1,681 sub-5 km events against the ~3× narrower
`D_eff = 15 km` windows — 0 uncovered at 0.25° (mutation: 56 at 0°). The
margins are absolute angular adds, so the argument is threshold-independent —
now confirmed empirically, not just argued.

Coverage is dominated by the near-coplanar floor (2.0% of CI-slice pairs;
0.35% of full-active pairs → up to ~2× more reduction at full catalog). The
margins barely move it — window width enters coverage linearly for the
non-coplanar 98%, and those windows are ~1°/180° of the orbit.

## What this buys (and what it doesn't)

- Medium pair-step work ×0.025 at the CI point (~41×). Medium becomes
  propagation-bound; expected CI-point total ≈ fine-stage-dominated.
- **It does NOT reduce the fine stage** (same events by construction), which is
  ~50% of the cascade today and ~90% post-sieve. The full cap-lift still runs
  through Stage 2 (10.4), exactly as scoped by the 10.0 gate.

## C++ integration notes (for 10.2)

- Lives inside `screen_pairs` behind a flag; emits the same `(i,j,jd,d)` rows;
  **byte-identical events** is the acceptance gate, with this prototype as the
  independent oracle.
- 3 extra `propagate_batch`-equivalent calls per sat (3N vs the scan's N×2880
  — noise). rv2coe in C++ ~30 lines; all reference rates (`mdot`, `argpdot`,
  `nodedot`) already on the satrec.
- Interval sets: ~2 crossings/rev/sat → ~30/sat/day at LEO; pair intersection
  of two sorted small lists. Estimated index memory ≪ the old pair list.
- Decayed-at-anchor sats: keep whole-window (rare; they're also the sats the
  medium scan NaNs out).
