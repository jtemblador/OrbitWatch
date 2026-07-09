/*
 * OrbitWatch conjunction-screening engine — implementation. See screening.h.
 *
 * run_medium_scan is the Task-6.3 scan moved here VERBATIM in Phase 10.2's
 * mechanical split (bindings.cpp had grown past ~900 lines); its behavior is
 * locked byte-identical by TestFusedStage + the medium_filter test suite.
 */
#include "screening.h"

#include <cmath>
#include <limits>

namespace screening {

std::vector<std::pair<size_t, size_t>> build_coarse_pairs(
    const std::vector<double>& periapsis_km,
    const std::vector<double>& apoapsis_km,
    double pad_km)
{
    const size_t n = periapsis_km.size();
    std::vector<std::pair<size_t, size_t>> pairs;
    // Naive O(N^2) interval-overlap scan: ~18M double compares at 6,000 sats
    // = tens of ms. Sort-and-sweep O(N log N + K) is the upgrade path if
    // Phase 4 (10k+ objects) ever needs it. NaN inputs compare false -> a
    // NaN-band sat pairs with nothing rather than poisoning the scan.
    for (size_t i = 0; i < n; ++i) {
        for (size_t j = i + 1; j < n; ++j) {
            if (periapsis_km[i] <= apoapsis_km[j] + pad_km &&
                periapsis_km[j] <= apoapsis_km[i] + pad_km) {
                pairs.emplace_back(i, j);
            }
        }
    }
    return pairs;
}

std::vector<MediumResult> run_medium_scan(
    const std::vector<elsetrec*>& sats,
    const std::vector<std::pair<size_t, size_t>>& P,
    double jd_start, double jd_end, double step_sec, double threshold_km)
{
    const size_t n = sats.size();
    const size_t npairs = P.size();

    // Precompute: per-sat epoch + which sats appear in a pair (propagate only
    // those). jdsatepoch/F are the absolute-JD epoch back-computed by sgp4init.
    std::vector<double> jd_epoch(n);
    for (size_t i = 0; i < n; ++i) {
        jd_epoch[i] = sats[i]->jdsatepoch + sats[i]->jdsatepochF;
    }
    std::vector<char> used(n, 0);
    for (const auto& pr : P) { used[pr.first] = 1; used[pr.second] = 1; }

    const double nan = std::numeric_limits<double>::quiet_NaN();
    const double dt_day = step_sec / 86400.0;
    const long nsteps =
        (long)std::floor((jd_end - jd_start) / dt_day + 1e-9) + 1;

    // Gravity-gradient curvature allowance for the linear motion bound:
    // relative accel <= ~2.6e-3 km/s^2, deviation over a half-step ~
    // 0.5*a*(dt/2)^2.
    const double curv_margin_km = 3.3e-4 * step_sec * step_sec;

    // Squared-distance pre-check: no two Earth-bound objects close faster than
    // ~22 km/s (two perigee-speed orbits head-on); 25 gives margin. Pairs
    // farther than this gross radius cannot flag, rejected with ZERO sqrts.
    const double VMAX_REL = 25.0;  // km/s, universal bound
    const double gross_km =
        threshold_km + VMAX_REL * (step_sec * 0.5) + curv_margin_km;
    const double gross2 = gross_km * gross_km;

    std::vector<MediumResult> results;

    // Per-step state buffers (prev / curr position + velocity)
    std::vector<double> cx(n), cy(n), cz(n), cvx(n), cvy(n), cvz(n);
    std::vector<double> pxv(n), pyv(n), pzv(n), pvx(n), pvy(n), pvz(n);
    std::vector<char> ok_curr(n, 0), ok_prev(n, 0);

    // Per-pair window state (distances kept SQUARED until needed)
    std::vector<double> prev_d2(npairs, nan);
    std::vector<double> best_d(npairs, std::numeric_limits<double>::infinity());
    std::vector<double> best_jd(npairs, 0.0);
    std::vector<char> open_win(npairs, 0);

    double jd_prev = 0.0;
    for (long kstep = 0; kstep < nsteps; ++kstep) {
        const double jd_t = jd_start + (double)kstep * dt_day;

        // Propagate every satellite that appears in a pair, once.
        for (size_t i = 0; i < n; ++i) {
            if (!used[i]) continue;
            const double tsince = (jd_t - jd_epoch[i]) * 1440.0;
            double r[3], v[3];
            bool ok = SGP4Funcs::sgp4(*sats[i], tsince, r, v);
            if (ok && sats[i]->error == 0) {
                cx[i] = r[0]; cy[i] = r[1]; cz[i] = r[2];
                cvx[i] = v[0]; cvy[i] = v[1]; cvz[i] = v[2];
                ok_curr[i] = 1;
            } else {
                cx[i] = nan; cy[i] = nan; cz[i] = nan;
                cvx[i] = nan; cvy[i] = nan; cvz[i] = nan;
                ok_curr[i] = 0;
            }
        }

        for (size_t k = 0; k < npairs; ++k) {
            const size_t i = P[k].first, j = P[k].second;
            double curr_d2 = nan;
            if (ok_curr[i] && ok_curr[j]) {
                const double dx = cx[i] - cx[j];
                const double dy = cy[i] - cy[j];
                const double dz = cz[i] - cz[j];
                curr_d2 = dx*dx + dy*dy + dz*dz;
            }

            bool flagged = false;
            if (kstep > 0 && !std::isnan(prev_d2[k])
                          && !std::isnan(curr_d2)) {
                const double dlo2 =
                    prev_d2[k] < curr_d2 ? prev_d2[k] : curr_d2;
                // Universal pre-check (squared, zero sqrts): beyond the gross
                // radius no bound pair can close to threshold within one step.
                if (dlo2 < gross2) {
                    // Precise per-pair bound. Relative speed at both endpoints;
                    // the larger bounds |d'(t)|.
                    const double rvc = std::sqrt(
                        (cvx[i]-cvx[j])*(cvx[i]-cvx[j]) +
                        (cvy[i]-cvy[j])*(cvy[i]-cvy[j]) +
                        (cvz[i]-cvz[j])*(cvz[i]-cvz[j]));
                    const double rvp = std::sqrt(
                        (pvx[i]-pvx[j])*(pvx[i]-pvx[j]) +
                        (pvy[i]-pvy[j])*(pvy[i]-pvy[j]) +
                        (pvz[i]-pvz[j])*(pvz[i]-pvz[j]));
                    const double vhat = rvc > rvp ? rvc : rvp;
                    const double d_lo = std::sqrt(dlo2);
                    // Lower bound on the true minimum inside [t_prev, t_curr]:
                    // any interior instant is within dt/2 of an endpoint, and
                    // |d'(t)| <= vhat (+ curvature allowance).
                    const double lb = d_lo
                        - vhat * (step_sec * 0.5)
                        - curv_margin_km;
                    flagged = lb < threshold_km;
                }
            }

            if (flagged) {
                // Track the better sampled endpoint of the window.
                double cand_d, cand_jd;
                if (curr_d2 < prev_d2[k]) {
                    cand_d = std::sqrt(curr_d2); cand_jd = jd_t;
                } else {
                    cand_d = std::sqrt(prev_d2[k]); cand_jd = jd_prev;
                }
                if (!open_win[k]) {
                    open_win[k] = 1;
                    best_d[k] = cand_d;
                    best_jd[k] = cand_jd;
                } else if (cand_d < best_d[k]) {
                    best_d[k] = cand_d;
                    best_jd[k] = cand_jd;
                }
            } else if (open_win[k]) {
                results.push_back({i, j, best_jd[k], best_d[k]});
                open_win[k] = 0;
                best_d[k] = std::numeric_limits<double>::infinity();
            }

            prev_d2[k] = curr_d2;
        }

        std::swap(cx, pxv); std::swap(cy, pyv); std::swap(cz, pzv);
        std::swap(cvx, pvx); std::swap(cvy, pvy); std::swap(cvz, pvz);
        std::swap(ok_curr, ok_prev);
        jd_prev = jd_t;
    }

    // Flush windows still open at the end of the scan.
    for (size_t k = 0; k < npairs; ++k) {
        if (open_win[k]) {
            results.push_back(
                {P[k].first, P[k].second, best_jd[k], best_d[k]});
        }
    }

    return results;
}

}  // namespace screening
