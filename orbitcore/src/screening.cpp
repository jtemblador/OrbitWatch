/*
 * OrbitWatch conjunction-screening engine — implementation. See screening.h.
 *
 * run_medium_scan is the Task-6.3 scan moved here VERBATIM in Phase 10.2's
 * mechanical split (bindings.cpp had grown past ~900 lines); its behavior is
 * locked byte-identical by TestFusedStage + the medium_filter test suite.
 */
#include "screening.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace {

const double PI = 3.14159265358979323846;

double wrap_pi(double x) {
    // Wrap into (-pi, pi] — matches the oracle's np.mod(x+pi, 2pi)-pi.
    double y = std::fmod(x + PI, 2.0 * PI);
    if (y < 0.0) y += 2.0 * PI;
    return y - PI;
}

struct Vec3 { double x, y, z; };
Vec3 cross(const Vec3& a, const Vec3& b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z,
            a.x * b.y - a.y * b.x};
}
double dot(const Vec3& a, const Vec3& b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}
double norm(const Vec3& a) { return std::sqrt(dot(a, a)); }

// Osculating classical elements from one propagated state — the C++ twin of
// the oracle's _osculating() (time_filter_gate.py), same formulas verbatim.
struct Osc {
    bool ok = false;
    double inc = 0, raan = 0, argp = 0, M = 0, ecc = 0, a = 0, u = 0;
};

Osc osculate(elsetrec* sat, double jd) {
    Osc o;
    const double epoch = sat->jdsatepoch + sat->jdsatepochF;
    double rr[3], vv[3];
    bool ok = SGP4Funcs::sgp4(*sat, (jd - epoch) * 1440.0, rr, vv);
    if (!ok || sat->error != 0) return o;

    const Vec3 r{rr[0], rr[1], rr[2]}, v{vv[0], vv[1], vv[2]};
    const double mu = sat->mus;
    const double rmag = norm(r);
    Vec3 h = cross(r, v);
    const double hmag = std::max(norm(h), 1e-12);
    const Vec3 hh{h.x / hmag, h.y / hmag, h.z / hmag};

    o.inc = std::acos(std::max(-1.0, std::min(1.0, hh.z)));
    o.raan = std::atan2(hh.x, -hh.y);           // atan2(n_y, n_x), n = z x h
    const double rv = dot(r, v);
    const double v2 = dot(v, v);
    Vec3 evec{((v2 - mu / rmag) * r.x - rv * v.x) / mu,
              ((v2 - mu / rmag) * r.y - rv * v.y) / mu,
              ((v2 - mu / rmag) * r.z - rv * v.z) / mu};
    o.ecc = norm(evec);
    Vec3 nvec{-hh.y, hh.x, 0.0};
    const double nmag = std::max(norm(nvec), 1e-12);
    const Vec3 nhat{nvec.x / nmag, nvec.y / nmag, 0.0};
    const double einv = 1.0 / std::max(o.ecc, 1e-12);
    const Vec3 ehat{evec.x * einv, evec.y * einv, evec.z * einv};
    const Vec3 te = cross(hh, ehat);
    const double nu = std::atan2(dot(r, te), dot(r, ehat));
    const Vec3 that = cross(hh, nhat);
    o.u = std::atan2(dot(r, that), dot(r, nhat));
    o.argp = o.u - nu;
    const double E = 2.0 * std::atan2(std::sqrt(1.0 - o.ecc) * std::sin(nu / 2.0),
                                      std::sqrt(1.0 + o.ecc) * std::cos(nu / 2.0));
    o.M = E - o.ecc * std::sin(E);
    o.a = 1.0 / std::max(2.0 / rmag - v2 / mu, 1e-12);
    o.ok = true;
    return o;
}

// Unit angular-momentum direction from (inc, raan) — the plane normal.
Vec3 plane_normal(double inc, double raan) {
    const double si = std::sin(inc);
    return {si * std::sin(raan), -si * std::cos(raan), std::cos(inc)};
}

// Argument of latitude of the mutual node line k in the plane (hh, raan):
// atan2(k . (hh x nhat), k . nhat) with nhat the plane's ascending node.
double node_angle(const Vec3& k, const Vec3& hh, double raan) {
    const Vec3 nhat{std::cos(raan), std::sin(raan), 0.0};
    const Vec3 that = cross(hh, nhat);
    return std::atan2(dot(k, that), dot(k, nhat));
}

}  // namespace

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

AnchorSet build_anchors(const std::vector<elsetrec*>& sats,
                        const std::vector<char>& used,
                        double jd0, double jd1)
{
    const size_t n = sats.size();
    const double T = jd1 - jd0;
    AnchorSet a;
    a.ok.assign(n, 0);
    a.inc.assign(n, 0.0);      a.raan0.assign(n, 0.0);
    a.raan_dot.assign(n, 0.0); a.lam0.assign(n, 0.0);
    a.lam_rate.assign(n, 0.0); a.m_rate.assign(n, 0.0);
    a.ecc.assign(n, 0.0);      a.rp.assign(n, 0.0);
    a.u0.assign(n, 0.0);       a.curv.assign(n, 0.0);

    for (size_t s = 0; s < n; ++s) {
        if (!used[s]) continue;
        const Osc e0 = osculate(sats[s], jd0);
        const Osc e1 = osculate(sats[s], jd1);
        const Osc em = osculate(sats[s], jd0 + T / 2.0);

        // Satrec reference rates (rad/min -> rad/day): the branch-safe unwrap
        // anchors — drag shifts the true advance by only ~degrees over a day.
        const double n_ref = sats[s]->mdot * 1440.0;
        const double argp_ref = sats[s]->argpdot * 1440.0;
        const double node_ref = sats[s]->nodedot * 1440.0;
        const double lam_ref = n_ref + argp_ref;

        const bool both = e0.ok && e1.ok;
        a.ok[s] = (e0.ok && e1.ok && em.ok) ? 1 : 0;
        if (!e0.ok) continue;   // no usable phase at all -> stays ok=false

        a.inc[s] = e0.inc;
        a.raan0[s] = e0.raan;
        a.ecc[s] = e0.ecc;
        a.rp[s] = e0.a * (1.0 - e0.ecc);
        a.u0[s] = e0.u;

        // Equinoctial mean longitude lam = argp + M: the (argp, M) SPLIT is
        // ill-conditioned for near-circular orbits (J2's forced ecc-vector
        // wobble ~1e-3 ~ e), but the SUM is well-conditioned — the 10.1b
        // failure-mode-3 cure, mirrored from the oracle.
        const double lam0 = e0.argp + e0.M;
        a.lam0[s] = lam0;
        if (both) {
            const double lam1 = e1.argp + e1.M;
            const double dlam =
                wrap_pi(lam1 - lam0 - lam_ref * T) + lam_ref * T;
            a.lam_rate[s] = dlam / T;
            const double dM = wrap_pi(e1.M - e0.M - n_ref * T) + n_ref * T;
            a.m_rate[s] = dM / T;
            a.raan_dot[s] = wrap_pi(e1.raan - e0.raan) / T;
        } else {
            a.lam_rate[s] = lam_ref;
            a.m_rate[s] = n_ref;
            a.raan_dot[s] = node_ref;
        }

        // Per-sat curvature margin from the midpoint second difference —
        // exactly half of it bounds the chord's max interior deviation for a
        // quadratic drift; x1.5 covers higher-order tails (failure mode 4:
        // a reentering 143-km-perigee sat measures ~6 deg where normal sats
        // measure ~0.1 deg).
        if (a.ok[s]) {
            const double lam_m = em.argp + em.M;
            const double ref_half = lam_ref * (T / 2.0);
            const double d1 = wrap_pi(lam_m - lam0 - ref_half) + ref_half;
            const double d2 =
                wrap_pi((e1.argp + e1.M) - lam_m - ref_half) + ref_half;
            a.curv[s] = 1.5 * std::fabs(d2 - d1) / 2.0;
        }
    }
    return a;
}

namespace {

// Per-sat node-crossing windows for one pair, appended as [t_lo, t_hi]
// (sorted, non-overlapping by construction). Returns false if this sat's
// windows cannot be bounded usefully (keep the pair whole-window instead).
bool sat_windows(const AnchorSet& a, size_t s,
                 double un0, double un1,      // node angle at jd0 / jd1
                 double du,                   // angular half-window, rad
                 double jd0, double jd1, double step_day,
                 std::vector<std::pair<double, double>>& out)
{
    const double T = jd1 - jd0;
    const double node_rate = wrap_pi(un1 - un0) / T;
    const double g = a.lam_rate[s] - node_rate;    // phase rate vs the node
    if (g <= 1.0) return false;                    // < ~0.16 rev/day: degenerate

    const double e = a.ecc[s];
    if (e >= 0.9) return false;
    // Slowest angular rate along the orbit (apogee) — conservative du -> dt.
    const double udot_min =
        a.m_rate[s] * (1.0 - e) * (1.0 - e) / std::pow(1.0 - e * e, 1.5);
    if (udot_min <= 1.0) return false;
    // Crossing times are enumerated from a LINEAR phase model anchored on the
    // exact osculating u at jd0; the equation of center drifts the true phase
    // by at most ~2*EoC_max either way, absorbed as extra time width (cheap —
    // no per-crossing Newton; blows up only for very eccentric sats, which
    // fall back to whole-window via the wrap check below).
    const double eoc_max = 2.0 * e / (1.0 - e);
    const double dt_half = du / udot_min + 2.0 * eoc_max / g + step_day;

    const double spacing = PI / g;                 // node-to-node, days
    if (2.0 * dt_half >= spacing) return false;    // windows merge: keep whole

    // Crossings at phase = 0 (mod pi); phase(t) = phi0 + g*(t - jd0).
    double phi0 = std::fmod(a.u0[s] - un0, PI);
    if (phi0 < 0.0) phi0 += PI;                    // into [0, pi)
    const double t_first = jd0 + (PI - phi0) / g - spacing;  // include overhang
    for (double tc = t_first; tc <= jd1 + dt_half; tc += spacing) {
        const double lo = tc - dt_half, hi = tc + dt_half;
        if (hi < jd0 || lo > jd1) continue;
        out.emplace_back(lo, hi);
    }
    return true;
}

}  // namespace

SieveIndex build_sieve_index(const AnchorSet& a,
                             const std::vector<std::pair<size_t, size_t>>& P,
                             double jd0, double jd1, double step_sec,
                             double threshold_km, const SieveParams& params)
{
    const size_t npairs = P.size();
    const double dt_day = step_sec / 86400.0;
    const double step_day = dt_day;
    const long nsteps =
        (long)std::floor((jd1 - jd0) / dt_day + 1e-9) + 1;
    const double D_eff = threshold_km + params.osc_margin_km;

    SieveIndex idx;
    idx.nsteps = nsteps;
    idx.range_offset.assign(npairs + 1, 0);
    idx.ranges.reserve(npairs + npairs / 2);

    std::vector<std::pair<double, double>> wi, wj, inter;
    for (size_t p = 0; p < npairs; ++p) {
        const size_t i = P[p].first, j = P[p].second;
        bool whole = false;

        if (!a.ok[i] || !a.ok[j]) {
            whole = true;   // failed anchor -> conservative keep (oracle rule)
        } else {
            // Mutual-node geometry at window start / mid / end; take the
            // SMALLEST sin(I_R) so the angular window is the WIDEST the pair
            // sees anywhere in the window (conservative; also catches planes
            // precessing through near-coplanarity mid-window).
            double sinIR_min = 2.0;
            double un_i0 = 0, un_im = 0, un_i1 = 0;
            double un_j0 = 0, un_jm = 0, un_j1 = 0;
            for (int q = 0; q < 3; ++q) {
                const double t = (q == 0) ? jd0 : (q == 1 ? jd0 + (jd1 - jd0) / 2.0 : jd1);
                const double dt = t - jd0;
                const double o1 = a.raan0[i] + a.raan_dot[i] * dt;
                const double o2 = a.raan0[j] + a.raan_dot[j] * dt;
                const Vec3 h1 = plane_normal(a.inc[i], o1);
                const Vec3 h2 = plane_normal(a.inc[j], o2);
                const Vec3 k = cross(h1, h2);
                sinIR_min = std::min(sinIR_min, std::max(norm(k), 1e-12));
                if (q == 0) { un_i0 = node_angle(k, h1, o1); un_j0 = node_angle(k, h2, o2); }
                if (q == 1) { un_im = node_angle(k, h1, o1); un_jm = node_angle(k, h2, o2); }
                if (q == 2) { un_i1 = node_angle(k, h1, o1); un_j1 = node_angle(k, h2, o2); }
            }

            const double xi = D_eff / (a.rp[i] * sinIR_min);
            const double xj = D_eff / (a.rp[j] * sinIR_min);
            if (xi >= 1.0 || xj >= 1.0) {
                whole = true;   // near-coplanar: no angular constraint (oracle rule)
            } else {
                // Node-path NONLINEARITY margin (review catch): the crossing
                // enumeration advances the node angle linearly between its
                // endpoint evaluations, but u_node is a nonlinear function of
                // the two precessing planes (worst where sin I_R is small —
                // the 1/sinIR amplification). The already-computed midpoint
                // evaluation measures the actual deviation: for a smooth path,
                // max interior deviation from the chord = |half-chord
                // mismatch| / 2; x1.5 for higher-order tails — the same
                // measured-curvature discipline as the anchor's lam margin.
                const double ncurv_i = 1.5 * std::fabs(
                    wrap_pi(un_i1 - un_im) - wrap_pi(un_im - un_i0)) / 2.0;
                const double ncurv_j = 1.5 * std::fabs(
                    wrap_pi(un_j1 - un_jm) - wrap_pi(un_jm - un_j0)) / 2.0;

                const double du_i =
                    std::asin(xi) + params.u_margin_rad + a.curv[i] + ncurv_i;
                const double du_j =
                    std::asin(xj) + params.u_margin_rad + a.curv[j] + ncurv_j;
                wi.clear(); wj.clear(); inter.clear();
                if (!sat_windows(a, i, un_i0, un_i1, du_i, jd0, jd1, step_day, wi) ||
                    !sat_windows(a, j, un_j0, un_j1, du_j, jd0, jd1, step_day, wj)) {
                    whole = true;
                } else {
                    // Intersect the two sorted window lists (two pointers).
                    size_t x = 0, y = 0;
                    while (x < wi.size() && y < wj.size()) {
                        const double lo = std::max(wi[x].first, wj[y].first);
                        const double hi = std::min(wi[x].second, wj[y].second);
                        if (lo <= hi) inter.emplace_back(lo, hi);
                        if (wi[x].second < wj[y].second) ++x; else ++y;
                    }
                    if (inter.size() > 64) whole = true;  // runaway guard
                }
            }
        }

        if (whole) {
            idx.ranges.emplace_back(0u, (uint32_t)(nsteps - 1));
            ++idx.n_whole_window;
        } else {
            // Time intervals -> inclusive step ranges, merging any that touch.
            uint32_t prev_lo = 0, prev_hi = 0;
            bool have = false;
            for (const auto& w : inter) {
                long k_lo = (long)std::floor((w.first - jd0) / dt_day);
                long k_hi = (long)std::ceil((w.second - jd0) / dt_day);
                if (k_hi < 0 || k_lo > nsteps - 1) continue;
                k_lo = std::max(0L, k_lo);
                k_hi = std::min(nsteps - 1, k_hi);
                if (have && (uint32_t)k_lo <= prev_hi + 1) {
                    prev_hi = std::max(prev_hi, (uint32_t)k_hi);
                } else {
                    if (have) idx.ranges.emplace_back(prev_lo, prev_hi);
                    prev_lo = (uint32_t)k_lo;
                    prev_hi = (uint32_t)k_hi;
                    have = true;
                }
            }
            if (have) idx.ranges.emplace_back(prev_lo, prev_hi);
            else ++idx.n_dropped;
        }
        idx.range_offset[p + 1] = (uint32_t)idx.ranges.size();
    }

    // Per-step activation index (counting sort over the ranges).
    idx.act_offset.assign((size_t)nsteps + 1, 0);
    idx.deact_offset.assign((size_t)nsteps + 1, 0);
    for (const auto& r : idx.ranges) {
        ++idx.act_offset[r.first + 1];
        ++idx.deact_offset[r.second + 1];
    }
    for (long k = 0; k < nsteps; ++k) {
        idx.act_offset[k + 1] += idx.act_offset[k];
        idx.deact_offset[k + 1] += idx.deact_offset[k];
    }
    idx.act.resize(idx.ranges.size());
    idx.deact.resize(idx.ranges.size());
    std::vector<uint32_t> ac(idx.act_offset.begin(), idx.act_offset.end() - 1);
    std::vector<uint32_t> dc(idx.deact_offset.begin(), idx.deact_offset.end() - 1);
    for (size_t p = 0; p < npairs; ++p) {
        for (uint32_t r = idx.range_offset[p]; r < idx.range_offset[p + 1]; ++r) {
            idx.act[ac[idx.ranges[r].first]++] = (uint32_t)p;
            idx.deact[dc[idx.ranges[r].second]++] = (uint32_t)p;
        }
    }
    return idx;
}

std::vector<MediumResult> run_medium_scan(
    const std::vector<elsetrec*>& sats,
    const std::vector<std::pair<size_t, size_t>>& P,
    double jd_start, double jd_end, double step_sec, double threshold_km,
    const SieveIndex* sieve)
{
    const size_t n = sats.size();
    const size_t npairs = P.size();

    // Precompute: per-sat epoch + which sats appear in a pair (propagate only
    // those). jdsatepoch/F are the absolute-JD epoch back-computed by sgp4init.
    // With a sieve, sats appearing only in fully-dropped pairs are skipped too.
    std::vector<double> jd_epoch(n);
    for (size_t i = 0; i < n; ++i) {
        jd_epoch[i] = sats[i]->jdsatepoch + sats[i]->jdsatepochF;
    }
    std::vector<char> used(n, 0);
    for (size_t p = 0; p < npairs; ++p) {
        if (sieve && sieve->range_offset[p + 1] == sieve->range_offset[p]) {
            continue;   // no scan ranges: this pair is never evaluated
        }
        used[P[p].first] = 1;
        used[P[p].second] = 1;
    }

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

    // Sieve active-pair tracking: an unordered list with swap-removal, plus
    // each pair's position in it (so deactivation is O(1)).
    std::vector<uint32_t> active;
    std::vector<uint32_t> pos_in_active;
    if (sieve != nullptr) {
        active.reserve(4096);
        pos_in_active.assign(npairs, UINT32_MAX);
    }

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

        // The per-pair evaluation body — identical for the full and sieved
        // paths (one lambda = one source of truth for the no-skip flag logic).
        auto eval_pair = [&](size_t k) {
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
        };

        if (sieve == nullptr) {
            for (size_t k = 0; k < npairs; ++k) eval_pair(k);
        } else {
            // Activate pairs whose range starts at this step (window state is
            // fresh: prev_d2 NaN, so flagging begins at the range's 2nd step —
            // the +-1-step interval padding exists exactly for this).
            for (uint32_t x = sieve->act_offset[kstep];
                 x < sieve->act_offset[kstep + 1]; ++x) {
                const uint32_t p = sieve->act[x];
                pos_in_active[p] = (uint32_t)active.size();
                active.push_back(p);
            }
            for (size_t x = 0; x < active.size(); ++x) eval_pair(active[x]);
            // Deactivate pairs whose range ends at this step: flush any open
            // window (same as end-of-scan) and reset state so a later range
            // of the same pair starts fresh.
            for (uint32_t x = sieve->deact_offset[kstep];
                 x < sieve->deact_offset[kstep + 1]; ++x) {
                const uint32_t p = sieve->deact[x];
                if (open_win[p]) {
                    results.push_back(
                        {P[p].first, P[p].second, best_jd[p], best_d[p]});
                    open_win[p] = 0;
                    best_d[p] = std::numeric_limits<double>::infinity();
                }
                prev_d2[p] = nan;
                const uint32_t at = pos_in_active[p];
                const uint32_t last = active.back();
                active[at] = last;
                pos_in_active[last] = at;
                active.pop_back();
                pos_in_active[p] = UINT32_MAX;
            }
        }

        std::swap(cx, pxv); std::swap(cy, pyv); std::swap(cz, pzv);
        std::swap(cvx, pvx); std::swap(cvy, pvy); std::swap(cvz, pvz);
        std::swap(ok_curr, ok_prev);
        jd_prev = jd_t;
    }

    // Flush windows still open at the end of the scan. (Sieved ranges end at
    // nsteps-1 at the latest, so their deactivation already flushed them —
    // this loop is then a no-op, kept for the full-scan path.)
    for (size_t k = 0; k < npairs; ++k) {
        if (open_win[k]) {
            results.push_back(
                {P[k].first, P[k].second, best_jd[k], best_d[k]});
        }
    }

    return results;
}

}  // namespace screening
