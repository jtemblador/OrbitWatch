/*
 * OrbitWatch pybind11 bindings for Vallado's SGP4 implementation.
 *
 * Exposes:
 *   - GravConst enum (wgs72old, wgs72, wgs84)
 *   - Satrec class (elsetrec struct with key fields)
 *   - sgp4init() — initialize satellite record from OMM elements
 *   - sgp4() — propagate to time T, returns (pos, vel) tuples
 *   - propagate_batch() — propagate many satellites in one call (None on per-sat error)
 *   - medium_filter() — time-stepped pair distance screening (stage 2)
 *   - screen_pairs()  — fused coarse+medium screen (stage 1a: survivor pairs
 *                       never cross into Python; see run_medium_scan below)
 *   - jday() — calendar date to Julian Date
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <tuple>
#include <string>
#include <stdexcept>
#include <cmath>
#include <limits>
#include <vector>
#include <utility>
#include "SGP4.h"
#include "hello.h"

namespace py = pybind11;

namespace {

// One flagged close-approach window: pair (i, j), best-sampled Julian date, and
// the distance there (km). Shared result type for the medium scan.
struct MediumResult { size_t i, j; double jd, d; };

// Cast a Python sequence of Satrec objects to raw elsetrec pointers (by
// reference — mutates the caller's Satrec, like the sgp4() binding). None or a
// non-Satrec item raises TypeError naming the index. Must be called with the
// GIL held (touches Python objects). Shared by medium_filter + screen_pairs.
std::vector<elsetrec*> extract_satrecs(py::sequence satrecs, const char* who) {
    const size_t n = py::len(satrecs);
    std::vector<elsetrec*> sats(n);
    for (size_t i = 0; i < n; ++i) {
        py::object item = satrecs[i];
        elsetrec* p = nullptr;
        // Pointer cast: pybind11 converts None to nullptr (a reference cast
        // would throw) — so check both paths.
        try { p = item.cast<elsetrec*>(); }
        catch (const py::cast_error&) { p = nullptr; }
        if (p == nullptr) {
            throw py::type_error(
                std::string(who) + ": satrecs item " + std::to_string(i)
                + " is not a Satrec");
        }
        sats[i] = p;
    }
    return sats;
}

// The time-major medium scan (Task 6.3), extracted so BOTH medium_filter
// (candidate pairs handed in from Python) and screen_pairs (candidate pairs
// built internally from the coarse cut) run the identical, validated no-skip
// logic — a single source of truth for the screening result.
//
// Pure C++ (no Python objects), so the caller wraps it in a
// py::gil_scoped_release. Assumes inputs are already validated: nsteps >= 2 and
// every P index in [0, sats.size()). Returns one MediumResult per contiguous
// flagged window per pair.
//
// Time-major: at each step every satellite appearing in a pair is propagated
// ONCE (positions cached), then all pair distances are read from the cache —
// N*steps SGP4 calls, not pairs*steps*2. Detection uses the velocity-aware
// bound min(d_k, d_k+1) - v_rel_max*(dt/2) - curvature < threshold, so a
// fast-crossing pair sampled far above threshold is still caught while
// co-orbital neighbors are not spuriously flagged.
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

}  // namespace

PYBIND11_MODULE(orbitcore, m) {
    m.doc() = "OrbitWatch C++ core — SGP4 propagation engine (Vallado 2020)";

    // Keep the hello world function for verification
    m.def("hello_world", &hello_world, "A function that returns a hello message");

    // --- Gravity constant enum ---
    py::enum_<gravconsttype>(m, "GravConst")
        .value("WGS72OLD", wgs72old, "Original STR#3 constants")
        .value("WGS72", wgs72, "WGS-72 (NORAD standard — use this)")
        .value("WGS84", wgs84, "WGS-84 (modern, not for TLE propagation)")
        .export_values();

    // --- Satellite record (elsetrec) ---
    py::class_<elsetrec>(m, "Satrec")
        .def(py::init<>())

        // Error state
        .def_readwrite("error", &elsetrec::error)

        // Identification
        .def_property("satnum",
            [](const elsetrec& s) { return std::string(s.satnum); },
            [](elsetrec& s, const std::string& val) {
                strncpy(s.satnum, val.c_str(), 5);
                s.satnum[5] = '\0';
            })
        .def_readwrite("classification", &elsetrec::classification)
        .def_readwrite("ephtype", &elsetrec::ephtype)
        .def_readwrite("elnum", &elsetrec::elnum)
        .def_readwrite("revnum", &elsetrec::revnum)

        // Epoch
        .def_readwrite("epochyr", &elsetrec::epochyr)
        .def_readwrite("epochdays", &elsetrec::epochdays)
        .def_readwrite("jdsatepoch", &elsetrec::jdsatepoch)
        .def_readwrite("jdsatepochF", &elsetrec::jdsatepochF)

        // Orbital elements (mean, as initialized)
        .def_readwrite("bstar", &elsetrec::bstar)
        .def_readwrite("ndot", &elsetrec::ndot)
        .def_readwrite("nddot", &elsetrec::nddot)
        .def_readwrite("ecco", &elsetrec::ecco)
        .def_readwrite("argpo", &elsetrec::argpo)
        .def_readwrite("inclo", &elsetrec::inclo)
        .def_readwrite("mo", &elsetrec::mo)
        .def_readwrite("no_kozai", &elsetrec::no_kozai)
        .def_readwrite("nodeo", &elsetrec::nodeo)
        .def_readwrite("no_unkozai", &elsetrec::no_unkozai)

        // Derived / computed
        .def_readwrite("a", &elsetrec::a)
        .def_readwrite("alta", &elsetrec::alta)
        .def_readwrite("altp", &elsetrec::altp)
        .def_readwrite("t", &elsetrec::t)

        // Gravity model constants (populated by sgp4init)
        .def_readonly("radiusearthkm", &elsetrec::radiusearthkm)
        .def_readonly("mus", &elsetrec::mus)
        .def_readonly("xke", &elsetrec::xke)
        .def_readonly("j2", &elsetrec::j2)
        .def_readonly("tumin", &elsetrec::tumin)

        // Operation mode
        .def_readwrite("operationmode", &elsetrec::operationmode)
        .def_readwrite("init", &elsetrec::init)
        .def_readwrite("method", &elsetrec::method)

        // Additional metadata
        .def_readwrite("dia_mm", &elsetrec::dia_mm)
        .def_readwrite("period_sec", &elsetrec::period_sec)
        .def_readwrite("active", &elsetrec::active)
        .def_readwrite("rcs_m2", &elsetrec::rcs_m2)
    ;

    // --- sgp4init: initialize satellite from orbital elements ---
    m.def("sgp4init",
        [](gravconsttype whichconst, char opsmode, const std::string& satnum,
           double epoch, double bstar, double ndot, double nddot,
           double ecco, double argpo, double inclo, double mo,
           double no_kozai, double nodeo) -> elsetrec
        {
            elsetrec satrec;
            memset(&satrec, 0, sizeof(elsetrec));

            // satnum needs to be a char array
            char satn[9];
            strncpy(satn, satnum.c_str(), 8);
            satn[8] = '\0';

            bool ok = SGP4Funcs::sgp4init(
                whichconst, opsmode, satn, epoch,
                bstar, ndot, nddot,
                ecco, argpo, inclo, mo, no_kozai, nodeo,
                satrec
            );

            if (!ok) {
                throw std::runtime_error(
                    "sgp4init failed with error code: " + std::to_string(satrec.error)
                );
            }

            // sgp4init doesn't set jdsatepoch (only twoline2rv does).
            // Back-compute it from the epoch parameter so Python can access it.
            double jd_epoch = epoch + 2433281.5;
            satrec.jdsatepoch = floor(jd_epoch) + 0.5;
            satrec.jdsatepochF = jd_epoch - satrec.jdsatepoch;

            return satrec;
        },
        py::arg("whichconst"),
        py::arg("opsmode"),
        py::arg("satnum"),
        py::arg("epoch"),
        py::arg("bstar"),
        py::arg("ndot"),
        py::arg("nddot"),
        py::arg("ecco"),
        py::arg("argpo"),
        py::arg("inclo"),
        py::arg("mo"),
        py::arg("no_kozai"),
        py::arg("nodeo"),
        R"doc(
Initialize SGP4 satellite record from orbital elements.

All angular elements must be in RADIANS.
Mean motion (no_kozai) must be in RADIANS/MINUTE.
Epoch is days since 1949 Dec 31 00:00 UTC (jdsatepoch - 2433281.5).

Returns: Satrec object ready for propagation.
Raises: RuntimeError if initialization fails.
)doc"
    );

    // --- sgp4: propagate and return ((x,y,z), (vx,vy,vz)) ---
    m.def("sgp4",
        [](elsetrec& satrec, double tsince)
            -> std::tuple<std::tuple<double,double,double>, std::tuple<double,double,double>>
        {
            double r[3], v[3];

            bool ok = SGP4Funcs::sgp4(satrec, tsince, r, v);

            if (!ok || satrec.error != 0) {
                throw std::runtime_error(
                    "sgp4 propagation failed with error code: " + std::to_string(satrec.error)
                );
            }

            return std::make_tuple(
                std::make_tuple(r[0], r[1], r[2]),
                std::make_tuple(v[0], v[1], v[2])
            );
        },
        py::arg("satrec"),
        py::arg("tsince"),
        R"doc(
Propagate satellite to time tsince (minutes from epoch).

Returns: ((x, y, z), (vx, vy, vz)) in TEME frame.
         Position in km, velocity in km/s.
Raises: RuntimeError if propagation fails (e.g., decayed orbit).
)doc"
    );

    // --- propagate_batch: propagate many satellites in one call ---
    m.def("propagate_batch",
        [](py::sequence satrecs, const std::vector<double>& tsince_list) -> py::list
        {
            if (py::len(satrecs) != tsince_list.size()) {
                throw py::value_error(
                    "propagate_batch: satrecs and tsince_list lengths differ ("
                    + std::to_string(py::len(satrecs)) + " vs "
                    + std::to_string(tsince_list.size()) + ")"
                );
            }

            py::list results;
            for (size_t i = 0; i < tsince_list.size(); ++i) {
                // Cast to reference, not copy — mutates the caller's Satrec
                // (t, error) exactly like the single-satellite sgp4() binding.
                py::object item = satrecs[i];
                elsetrec* satrec_ptr = nullptr;
                try {
                    // Pointer cast: pybind11 converts None to nullptr (a
                    // reference cast would throw) — so check both paths.
                    satrec_ptr = item.cast<elsetrec*>();
                } catch (const py::cast_error&) {
                    satrec_ptr = nullptr;
                }
                if (satrec_ptr == nullptr) {
                    throw py::type_error(
                        "propagate_batch: item " + std::to_string(i)
                        + " is not a Satrec"
                    );
                }
                elsetrec& satrec = *satrec_ptr;

                double r[3], v[3];
                bool ok = SGP4Funcs::sgp4(satrec, tsince_list[i], r, v);

                if (!ok || satrec.error != 0) {
                    // Sentinel instead of throwing: one decayed/bad satellite
                    // must not kill the batch. sgp4() clears satrec.error at
                    // the start of every call, so a failed satellite remains
                    // usable at other propagation times.
                    results.append(py::none());
                } else {
                    results.append(py::make_tuple(
                        py::make_tuple(r[0], r[1], r[2]),
                        py::make_tuple(v[0], v[1], v[2])
                    ));
                }
            }
            return results;
        },
        py::arg("satrecs"),
        py::arg("tsince_list"),
        R"doc(
Propagate many satellites in a single call (one Python->C++ crossing).

satrecs:     sequence of Satrec objects (each initialized via sgp4init).
tsince_list: minutes from EACH satellite's own epoch, one entry per
             satellite. Epochs differ per satellite — to evaluate all at
             the same UTC instant, compute per-satellite
             tsince = (jd_target - (jdsatepoch + jdsatepochF)) * 1440.

Returns: list with one entry per satellite:
         ((x, y, z), (vx, vy, vz)) in TEME (km, km/s), or
         None if propagation failed for that satellite (e.g. decayed
         orbit). A failure does not affect other entries.
Raises: ValueError if the input lengths differ.
)doc"
    );

    // --- coarse_filter: altitude-band pair screening (conjunction stage 1) ---
    m.def("coarse_filter",
        [](const std::vector<double>& periapsis_km,
           const std::vector<double>& apoapsis_km,
           double pad_km) -> std::vector<std::pair<size_t, size_t>>
        {
            if (periapsis_km.size() != apoapsis_km.size()) {
                throw py::value_error(
                    "coarse_filter: periapsis_km and apoapsis_km lengths differ ("
                    + std::to_string(periapsis_km.size()) + " vs "
                    + std::to_string(apoapsis_km.size()) + ")"
                );
            }
            if (pad_km < 0.0) {
                throw py::value_error(
                    "coarse_filter: pad_km must be >= 0, got "
                    + std::to_string(pad_km)
                );
            }

            const size_t n = periapsis_km.size();
            std::vector<std::pair<size_t, size_t>> pairs;
            // Naive O(N^2) interval-overlap scan: ~18M double compares at
            // 6,000 sats = tens of ms. Sort-and-sweep O(N log N + K) is the
            // upgrade path if Phase 4 (10k+ objects) ever needs it.
            for (size_t i = 0; i < n; ++i) {
                for (size_t j = i + 1; j < n; ++j) {
                    // Bands overlap (touching counts) once padded by pad_km.
                    // NaN inputs compare false -> a NaN-band sat pairs with
                    // nothing rather than poisoning the scan.
                    if (periapsis_km[i] <= apoapsis_km[j] + pad_km &&
                        periapsis_km[j] <= apoapsis_km[i] + pad_km) {
                        pairs.emplace_back(i, j);
                    }
                }
            }
            return pairs;
        },
        py::arg("periapsis_km"),
        py::arg("apoapsis_km"),
        py::arg("pad_km"),
        R"doc(
Coarse conjunction filter: keep satellite pairs whose orbital altitude
bands [periapsis - pad, apoapsis + pad] overlap. Two objects whose bands
never intersect can never come close — rejected without any propagation.

periapsis_km: per-satellite perigee altitude in km (e.g. Satrec.altp *
              Satrec.radiusearthkm, or the GP-derived 'periapsis' column).
apoapsis_km:  per-satellite apogee altitude in km, same indexing.
pad_km:       safety margin >= 0. Covers SGP4 element drift and the gap
              up to the medium-filter distance threshold. Two bands
              separated by a gap <= pad_km still count as overlapping.

Returns: list of (i, j) index pairs with i < j, in row-major order.
         NaN bands match nothing.
Raises: ValueError on length mismatch or negative pad_km.
)doc"
    );

    // --- medium_filter: time-stepped pair distance screening (stage 2) ---
    m.def("medium_filter",
        [](py::sequence satrecs, py::sequence pairs,
           double jd_start, double jd_end,
           double step_sec, double threshold_km) -> py::list
        {
            // ---- boundary validation ----
            if (!(jd_end > jd_start)) {
                throw py::value_error(
                    "medium_filter: jd_end must be > jd_start");
            }
            if (!(step_sec > 0.0)) {
                throw py::value_error(
                    "medium_filter: step_sec must be > 0, got "
                    + std::to_string(step_sec));
            }
            if (!(threshold_km > 0.0)) {
                throw py::value_error(
                    "medium_filter: threshold_km must be > 0, got "
                    + std::to_string(threshold_km));
            }

            // ---- extract satrec pointers once (None -> nullptr: check!) ----
            std::vector<elsetrec*> sats = extract_satrecs(satrecs, "medium_filter");
            const size_t n = sats.size();

            // ---- extract + validate pairs ----
            const size_t npairs = py::len(pairs);
            std::vector<std::pair<size_t, size_t>> P(npairs);
            for (size_t k = 0; k < npairs; ++k) {
                py::object item = pairs[k];
                std::pair<long long, long long> pr;
                try { pr = item.cast<std::pair<long long, long long>>(); }
                catch (const py::cast_error&) {
                    throw py::type_error(
                        "medium_filter: pairs item " + std::to_string(k)
                        + " is not an (i, j) index pair");
                }
                if (pr.first < 0 || pr.second < 0 ||
                    (size_t)pr.first >= n || (size_t)pr.second >= n ||
                    pr.first == pr.second) {
                    throw py::value_error(
                        "medium_filter: pairs item " + std::to_string(k)
                        + " (" + std::to_string(pr.first) + ", "
                        + std::to_string(pr.second)
                        + ") out of range for " + std::to_string(n)
                        + " satellites or i == j");
                }
                P[k] = {(size_t)pr.first, (size_t)pr.second};
            }

            // One sample = zero intervals = nothing can ever flag. Fail loudly
            // instead of silently returning [].
            const double dt_day = step_sec / 86400.0;
            const long nsteps =
                (long)std::floor((jd_end - jd_start) / dt_day + 1e-9) + 1;
            if (nsteps < 2) {
                throw py::value_error(
                    "medium_filter: scan window ("
                    + std::to_string((jd_end - jd_start) * 86400.0)
                    + " s) is shorter than step_sec ("
                    + std::to_string(step_sec) + " s)");
            }

            std::vector<MediumResult> results;
            {
                // Hot loop touches no Python objects -> release the GIL so a
                // multi-second screen doesn't freeze the FastAPI process.
                py::gil_scoped_release release;
                results = run_medium_scan(
                    sats, P, jd_start, jd_end, step_sec, threshold_km);
            } // GIL reacquired here

            py::list out;
            for (const auto& r : results) {
                out.append(py::make_tuple(r.i, r.j, r.jd, r.d));
            }
            return out;
        },
        py::arg("satrecs"),
        py::arg("pairs"),
        py::arg("jd_start"),
        py::arg("jd_end"),
        py::arg("step_sec"),
        py::arg("threshold_km"),
        R"doc(
Medium conjunction filter: time-stepped TEME distance screening of
candidate pairs (stage 2 of the cascade, after coarse_filter).

Time-major scan: at each step every satellite appearing in a pair is
propagated ONCE (positions cached), then all pair distances are evaluated
from the cache — N*steps SGP4 calls instead of pairs*steps*2.

Detection criterion (no skipped approaches): an interval [t_k, t_k+1] is
flagged when  min(d_k, d_k+1) - v_rel_max*(dt/2) - curvature_margin <
threshold_km, where v_rel_max is the pair's larger endpoint relative
speed. A fast-crossing pair (~15 km/s) whose sampled distances sit far
above the threshold is still caught; co-orbital neighbors (v_rel ~ 0)
are not spuriously flagged.

satrecs:      sequence of Satrec (passed by reference; t/error mutate).
pairs:        sequence of (i, j) index pairs into satrecs (e.g. from
              coarse_filter). Indices validated; i != j required.
jd_start/end: scan window as absolute Julian Dates (UTC), end > start.
step_sec:     time step in seconds (> 0).
threshold_km: flag distance in km (> 0). The fine filter refines.

Returns: list of (i, j, jd, distance_km) — one row per contiguous
         flagged window per pair (consecutive flagged intervals merge).
         jd/distance are the window's best SAMPLED point; the true
         minimum lies within +-1 step (fine-filter bracket). A pair can
         appear multiple times (repeating encounter geometry). A
         satellite that fails to propagate (decayed) contributes no
         flags and closes any open window; other pairs are unaffected.
Raises: ValueError (bad window/step/threshold/pair indices),
        TypeError (non-Satrec or non-pair items, named by index).
The GIL is released during the scan.
)doc"
    );

    // --- screen_pairs: fused coarse + medium screen (stage 1a) ---
    m.def("screen_pairs",
        [](py::sequence satrecs,
           const std::vector<double>& periapsis_km,
           const std::vector<double>& apoapsis_km,
           double pad_km, double jd_start, double jd_end,
           double step_sec, double threshold_km) -> py::tuple
        {
            const size_t n = py::len(satrecs);
            if (periapsis_km.size() != n || apoapsis_km.size() != n) {
                throw py::value_error(
                    "screen_pairs: periapsis_km (" + std::to_string(periapsis_km.size())
                    + ") and apoapsis_km (" + std::to_string(apoapsis_km.size())
                    + ") must match satrecs (" + std::to_string(n) + ")");
            }
            if (pad_km < 0.0) {
                throw py::value_error(
                    "screen_pairs: pad_km must be >= 0, got "
                    + std::to_string(pad_km));
            }
            if (!(jd_end > jd_start)) {
                throw py::value_error("screen_pairs: jd_end must be > jd_start");
            }
            if (!(step_sec > 0.0)) {
                throw py::value_error(
                    "screen_pairs: step_sec must be > 0, got "
                    + std::to_string(step_sec));
            }
            if (!(threshold_km > 0.0)) {
                throw py::value_error(
                    "screen_pairs: threshold_km must be > 0, got "
                    + std::to_string(threshold_km));
            }
            const double dt_day = step_sec / 86400.0;
            const long nsteps =
                (long)std::floor((jd_end - jd_start) / dt_day + 1e-9) + 1;
            if (nsteps < 2) {
                throw py::value_error(
                    "screen_pairs: scan window ("
                    + std::to_string((jd_end - jd_start) * 86400.0)
                    + " s) is shorter than step_sec ("
                    + std::to_string(step_sec) + " s)");
            }

            std::vector<elsetrec*> sats = extract_satrecs(satrecs, "screen_pairs");

            std::vector<std::pair<size_t, size_t>> P;
            std::vector<MediumResult> results;
            {
                // Both the coarse cut AND the scan touch no Python objects ->
                // release the GIL for the whole thing. The survivor pair list
                // lives ONLY here in C++ (~8 bytes/pair) — it never crosses
                // into Python, which is the point of the fused stage: at the
                // full active catalog the equivalent Python list is ~8.7 GB of
                // tuples (would not fit CI), versus a few hundred MB here.
                py::gil_scoped_release release;

                // Coarse altitude-band cut — identical logic to coarse_filter
                // (i < j, row-major, NaN bands pair with nothing), inlined so
                // survivors go straight into the scan.
                for (size_t i = 0; i < n; ++i) {
                    for (size_t j = i + 1; j < n; ++j) {
                        if (periapsis_km[i] <= apoapsis_km[j] + pad_km &&
                            periapsis_km[j] <= apoapsis_km[i] + pad_km) {
                            P.emplace_back(i, j);
                        }
                    }
                }
                results = run_medium_scan(
                    sats, P, jd_start, jd_end, step_sec, threshold_km);
            } // GIL reacquired here

            py::list out;
            for (const auto& r : results) {
                out.append(py::make_tuple(r.i, r.j, r.jd, r.d));
            }
            // (n_pairs, rows): the survivor count is returned for profiling
            // (survivor reduction) WITHOUT materializing the pairs in Python.
            return py::make_tuple(P.size(), out);
        },
        py::arg("satrecs"),
        py::arg("periapsis_km"),
        py::arg("apoapsis_km"),
        py::arg("pad_km"),
        py::arg("jd_start"),
        py::arg("jd_end"),
        py::arg("step_sec"),
        py::arg("threshold_km"),
        R"doc(
Fused coarse + medium conjunction screen (stage 1a) — a drop-in replacement
for coarse_filter() followed by medium_filter(), producing byte-identical rows.

The coarse altitude-band cut is computed internally and its survivor (i, j)
pairs are fed straight into the time-stepped medium scan WITHOUT ever crossing
into Python. On the full active catalog that pair list is ~48 M entries: as
Python tuples it is ~8.7 GB (does not fit a 16 GB CI runner), versus a few
hundred MB as a C++ vector here. Same screening result, far less memory.

satrecs:      sequence of Satrec (passed by reference; t/error mutate).
periapsis_km: per-satellite perigee altitude, km (index-aligned with satrecs).
apoapsis_km:  per-satellite apogee altitude, km, same indexing.
pad_km:       coarse altitude-band margin (>= 0); see coarse_filter.
jd_start/end: scan window as absolute Julian Dates (UTC), end > start.
step_sec:     medium time step in seconds (> 0).
threshold_km: medium flag distance in km (> 0).

Returns: (n_pairs, rows) where
  n_pairs — number of coarse survivors (for profiling; the pairs themselves
            are never returned).
  rows    — list of (i, j, jd, distance_km), identical to medium_filter's
            output for the same coarse survivors (see medium_filter for the
            no-skip detection criterion and row semantics).
Raises: ValueError (bad lengths/pad/window/step/threshold),
        TypeError (non-Satrec item, named by index).
The GIL is released during both the coarse cut and the scan.
)doc"
    );

    // --- jday: calendar date to Julian Date ---
    m.def("jday",
        [](int year, int mon, int day, int hr, int minute, double sec)
            -> std::tuple<double, double>
        {
            double jd, jdFrac;
            SGP4Funcs::jday_SGP4(year, mon, day, hr, minute, sec, jd, jdFrac);
            return std::make_tuple(jd, jdFrac);
        },
        py::arg("year"), py::arg("mon"), py::arg("day"),
        py::arg("hr"), py::arg("minute"), py::arg("sec"),
        "Convert calendar date to Julian Date. Returns (jd, jdFrac)."
    );

    // --- invjday: Julian Date to calendar date ---
    m.def("invjday",
        [](double jd, double jdFrac)
            -> std::tuple<int, int, int, int, int, double>
        {
            int year, mon, day, hr, minute;
            double sec;
            SGP4Funcs::invjday_SGP4(jd, jdFrac, year, mon, day, hr, minute, sec);
            return std::make_tuple(year, mon, day, hr, minute, sec);
        },
        py::arg("jd"), py::arg("jdFrac"),
        "Convert Julian Date to calendar date. Returns (year, mon, day, hr, min, sec)."
    );

    // --- getgravconst: retrieve gravity model constants ---
    m.def("getgravconst",
        [](gravconsttype whichconst)
            -> py::dict
        {
            double tumin, mus, radiusearthkm, xke, j2, j3, j4, j3oj2;
            SGP4Funcs::getgravconst(whichconst, tumin, mus, radiusearthkm, xke, j2, j3, j4, j3oj2);

            py::dict result;
            result["tumin"] = tumin;
            result["mus"] = mus;
            result["radiusearthkm"] = radiusearthkm;
            result["xke"] = xke;
            result["j2"] = j2;
            result["j3"] = j3;
            result["j4"] = j4;
            result["j3oj2"] = j3oj2;
            return result;
        },
        py::arg("whichconst"),
        "Get gravity constants for a given model. Returns dict with tumin, mus, radiusearthkm, xke, j2, j3, j4, j3oj2."
    );

    // --- Version info ---
    m.attr("SGP4_VERSION") = SGP4Version;
}
