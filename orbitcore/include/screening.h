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
#include <utility>
#include <vector>

#include "SGP4.h"

namespace screening {

// One flagged close-approach window: pair (i, j), best-sampled Julian date,
// and the distance there (km).
struct MediumResult { size_t i, j; double jd, d; };

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
// Assumes inputs already validated: nsteps >= 2 and every P index in
// [0, sats.size()). Mutates the satrecs (t, error) exactly like sgp4().
std::vector<MediumResult> run_medium_scan(
    const std::vector<elsetrec*>& sats,
    const std::vector<std::pair<size_t, size_t>>& P,
    double jd_start, double jd_end, double step_sec, double threshold_km);

}  // namespace screening

#endif  // ORBITWATCH_SCREENING_H
