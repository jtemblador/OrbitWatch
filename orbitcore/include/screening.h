/*
 * OrbitWatch conjunction-screening engine — pure C++ (no Python/pybind11).
 *
 * The hot screening logic lives here so it has a single source of truth,
 * stays independently readable, and can later be parallelized (Phase 10.4)
 * without touching the Python boundary. bindings.cpp wraps these for Python:
 *   - build_coarse_pairs()  — altitude-band pair cut (stage 1),
 *                             shared by coarse_filter() and screen_pairs()
 *   - run_medium_scan()     — the 6.3 time-major, velocity-aware no-skip
 *                             distance scan (stage 2), shared by
 *                             medium_filter() and screen_pairs()
 *
 * All functions are GIL-free by construction (no Python objects); callers
 * hold or release the GIL as they see fit.
 */
#ifndef ORBITWATCH_SCREENING_H
#define ORBITWATCH_SCREENING_H

#include <cstddef>
#include <cstdint>
#include <utility>
#include <vector>

#include "SGP4.h"

namespace screening {

// One flagged close-approach window: pair (i, j), best-sampled Julian date,
// and the distance there (km).
struct MediumResult { size_t i, j; double jd, d; };

// ---------------------------------------------------------------------------
// Phase 10.2 time sieve — the validated 10.1b construction (see
// progress/week10_planning/stage1b_timefilter_spec.md; time_filter_gate.py is
// the independent oracle these formulas mirror).
// ---------------------------------------------------------------------------

// Per-satellite anchored elements: osculating states at the scan window's
// start / mid / end (3 SGP4 propagations per sat), converted to classical
// elements, with equinoctial CHORD rates over the window and a measured
// per-sat curvature margin. Index-aligned with the satrec list; sats that
// fail any anchor propagation get ok=false (treated always-active).
struct AnchorSet {
    std::vector<char> ok;
    std::vector<double> inc;        // inclination at t0, rad
    std::vector<double> raan0;      // RAAN at t0, rad
    std::vector<double> raan_dot;   // chord RAAN rate, rad/day
    std::vector<double> lam0;       // mean longitude argp+M at t0, rad
    std::vector<double> lam_rate;   // chord mean-longitude rate, rad/day
    std::vector<double> m_rate;     // chord mean-anomaly rate, rad/day
    std::vector<double> ecc;        // osculating eccentricity at t0
    std::vector<double> rp;         // perigee radius at t0, km
    std::vector<double> u0;         // argument of latitude at t0, rad
                                    // (incl. equation of center — exact phase)
    std::vector<double> curv;       // curvature window margin, rad (x1.5)
};

// Build the anchors: 3 propagations per USED sat (start / midpoint / end of
// [jd0, jd1]) -> rv2coe -> chord rates unwrapped against the satrec's own
// reference rates (mdot/argpdot/nodedot; branch-safe — drag shifts the true
// advance by only ~degrees over a day). Unused sats are skipped (ok=false).
AnchorSet build_anchors(const std::vector<elsetrec*>& sats,
                        const std::vector<char>& used,
                        double jd0, double jd1);

// Per-pair scan intervals as HALF-OPEN step ranges [k_lo, k_hi] (inclusive),
// CSR layout: pair p's ranges are ranges[range_offset[p] .. range_offset[p+1]).
// A pair with no ranges is never scanned (its orbits never co-occupy the node
// windows); a conservative fall-back pair gets one whole-window range.
struct SieveIndex {
    std::vector<uint32_t> range_offset;               // size npairs+1
    std::vector<std::pair<uint32_t, uint32_t>> ranges;
    // Per-step activation index (built from the ranges): at step k, activate
    // pairs in act[act_offset[k] .. act_offset[k+1]); after evaluating step k,
    // deactivate pairs in deact[...] (their range ended at k).
    std::vector<uint32_t> act_offset, act;            // size nsteps+1 / total
    std::vector<uint32_t> deact_offset, deact;
    long nsteps = 0;
    size_t n_whole_window = 0;   // pairs kept whole-window (coplanar/fallback)
    size_t n_dropped = 0;        // pairs with zero ranges (never scanned)
};

// Sieve tuning (defaults = the validated 10.1b spec values).
struct SieveParams {
    double osc_margin_km = 10.0;   // mean-vs-osculating D_eff pad
    double u_margin_rad = 0.5 * 3.14159265358979323846 / 180.0;  // base 0.5 deg
};

// Build the per-pair scan intervals for the coarse-survivor list P over
// [jd0, jd1] at step_sec, gating on D_eff = threshold_km + osc_margin_km.
// Conservative by construction: near-coplanar pairs, failed anchors, very
// eccentric phases and any window wide enough to wrap are kept whole-window.
SieveIndex build_sieve_index(const AnchorSet& a,
                             const std::vector<std::pair<size_t, size_t>>& P,
                             double jd0, double jd1, double step_sec,
                             double threshold_km, const SieveParams& params);

// Coarse altitude-band cut: every (i, j), i < j, whose bands
// [periapsis - pad, apoapsis + pad] overlap (touching counts), in row-major
// order. NaN bands pair with nothing. Inputs must be same-length; pad >= 0
// (validated by the callers at the Python boundary).
std::vector<std::pair<size_t, size_t>> build_coarse_pairs(
    const std::vector<double>& periapsis_km,
    const std::vector<double>& apoapsis_km,
    double pad_km);

// The time-major medium scan (Task 6.3): at each step every satellite
// appearing in a pair is propagated ONCE (positions cached), then all pair
// distances are read from the cache — N*steps SGP4 calls, not pairs*steps*2.
// Detection uses the velocity-aware no-skip bound
//     min(d_k, d_k+1) - v_rel_max*(dt/2) - curvature_margin < threshold_km,
// so a fast-crossing pair sampled far above threshold is still caught while
// co-orbital neighbors are not spuriously flagged. Returns one MediumResult
// per contiguous flagged window per pair.
//
// sieve == nullptr: the classic full scan (every pair at every step).
// sieve != nullptr (Phase 10.2 time filter): each pair is evaluated only
// inside its precomputed step ranges — activated at a range's first step
// (fresh window state), evaluated while active, and flushed+deactivated after
// its last step (same flush as end-of-scan). Rows for evaluated windows are
// byte-identical to the full scan; windows entirely outside every range
// (medium's spurious far-distance flags) are simply never produced — the
// EVENT list after the fine stage + report cut is identical (the 10.1b
// contract; proven on 1.4M real events).
//
// Assumes inputs already validated: nsteps >= 2 and every P index in
// [0, sats.size()). Mutates the satrecs (t, error) exactly like sgp4().
std::vector<MediumResult> run_medium_scan(
    const std::vector<elsetrec*>& sats,
    const std::vector<std::pair<size_t, size_t>>& P,
    double jd_start, double jd_end, double step_sec, double threshold_km,
    const SieveIndex* sieve = nullptr);

// ---------------------------------------------------------------------------
// Phase 10.4 fine stage — C++ TCA refinement with a SUPERSET pre-cut.
// ---------------------------------------------------------------------------
//
// refine_rows() runs the Phase-7.3 batched fine algorithm (Newton on the
// relative range-rate over the bracket jd_flag +- one step, best-sample
// tracking, one edge-widen retry) on every medium row, in parallel (OpenMP;
// each row copies its two elsetrecs — sgp4() mutates the record, so shared
// satrecs are NOT thread-safe), and returns the SURVIVOR subset of `rows`,
// order preserved, original (jd, d) values untouched.
//
// The contract is a conservative pre-cut, not the reported numbers: a row
// survives iff its converged miss can pass the report cut with margin, OR its
// miss is non-finite (a propagation failure is handed back to Python to
// adjudicate rather than dropped here). Python re-refines ONLY the survivors
// with the validated fine_filter_batch, so every reported float comes from
// the exact same code path as the unrefined screen — events are byte-identical
// by construction as long as this cut is a true superset. margin_km absorbs
// the C++-vs-NumPy floating-point disagreement (measured: |dTCA| exactly 0,
// |dmiss| <= 5.7e-14 km on 934k real rows; the 0.01 km default carries ~12
// orders of magnitude of headroom).
//
// Two cut shapes, mirroring run_screen's two report cuts:
//   axes == nullptr (Euclidean mode): miss <= pre_cut_km + margin_km.
//   axes != nullptr (SFS mode): per-satellite RTN ellipsoid semi-axes
//     {r, t, n} km, index-aligned with sats. The pair's volume is the
//     sat's whose circumscribing radius (max axis) is larger — j's only if
//     STRICTLY larger, matching Python's first-wins max(). The miss vector
//     at the converged TCA, in the PRIMARY (i)'s RTN frame (Vallado RSW,
//     identical to teme_to_rtn), must lie inside the ellipsoid with every
//     semi-axis inflated by margin_km. A global miss <= pre_cut_km ball
//     would leave ~21% of rows surviving (measured — the SFS radial axis is
//     0.4 km vs the 51 km gross); the ellipsoid collapses survivors to
//     ~the true event set, which is what keeps the full catalog in memory.
//
// n_threads > 0 pins the OpenMP team size (tests use 1 vs default to prove
// thread-count invariance); 0 = the OpenMP default. Output is deterministic
// regardless: the keep/reject flag is stored per row index and compacted
// serially.
struct RtnAxes { double r, t, n; };
std::vector<MediumResult> refine_rows(
    const std::vector<elsetrec*>& sats,
    const std::vector<MediumResult>& rows,
    double step_sec, double pre_cut_km, double margin_km,
    const std::vector<RtnAxes>* axes = nullptr,
    int n_threads = 0);

// The oracle hook (Phase-10.4 twin of _sieve_anchors): the converged
// (jd_tca, miss_km) per row, index-aligned with `rows` (NaN miss where the
// pair could not be propagated). Tests lock these against fine_filter_batch;
// the measured disagreement is what justifies refine_rows' margin_km.
struct RefineDebug { std::vector<double> jd_tca, miss_km; };
RefineDebug refine_debug(const std::vector<elsetrec*>& sats,
                         const std::vector<MediumResult>& rows,
                         double step_sec, int n_threads = 0);

}  // namespace screening

#endif  // ORBITWATCH_SCREENING_H
