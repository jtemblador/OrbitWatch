/*
 * Python bindings — the conjunction screen's Python boundary.
 *
 *   - coarse_filter() — altitude-band pair cut (stage 1)
 *   - medium_filter() — time-stepped pair distance screening (stage 2)
 *   - screen_pairs()  — fused coarse+medium screen (stage 1a: survivor pairs
 *                       never cross into Python)
 *
 * This file is boundary only: validation, GIL handling, Python<->C++
 * conversion. The screening engine itself (the coarse cut and the 6.3
 * time-major medium scan) is pure C++ in src/screening.cpp — one source of
 * truth shared by all three bindings. Moved verbatim out of bindings.cpp in
 * the Phase-10.2 file split.
 */
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <cmath>
#include <string>
#include <utility>
#include <vector>

#include "SGP4.h"
#include "bindings.h"
#include "screening.h"

namespace py = pybind11;

namespace {

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

}  // namespace

void bind_screening(py::module_& m) {
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

            return screening::build_coarse_pairs(
                periapsis_km, apoapsis_km, pad_km);
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

            std::vector<screening::MediumResult> results;
            {
                // Hot loop touches no Python objects -> release the GIL so a
                // multi-second screen doesn't freeze the FastAPI process.
                py::gil_scoped_release release;
                results = screening::run_medium_scan(
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

    // --- screen_pairs: fused coarse + medium screen (stage 1a + 10.2 sieve) ---
    m.def("screen_pairs",
        [](py::sequence satrecs,
           const std::vector<double>& periapsis_km,
           const std::vector<double>& apoapsis_km,
           double pad_km, double jd_start, double jd_end,
           double step_sec, double threshold_km,
           bool time_filter, double sieve_u_margin_deg,
           double sieve_osc_margin_km) -> py::tuple
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

            if (sieve_u_margin_deg < 0.0 || sieve_osc_margin_km < 0.0) {
                throw py::value_error(
                    "screen_pairs: sieve margins must be >= 0");
            }

            std::vector<elsetrec*> sats = extract_satrecs(satrecs, "screen_pairs");

            std::vector<std::pair<size_t, size_t>> P;
            std::vector<screening::MediumResult> results;
            {
                // Both the coarse cut AND the scan touch no Python objects ->
                // release the GIL for the whole thing. The survivor pair list
                // lives ONLY here in C++ (16 bytes/pair) — it never crosses
                // into Python: at the full active catalog that's ~48 M pairs,
                // ~5 GB as Python tuples vs ~0.8 GB here. (Whole-screen peak RSS
                // is dominated by the LATER fine stage, not this list — see
                // task_10_1a; this drops the pair-list share, ~2 GB at 10k.)
                py::gil_scoped_release release;

                // Same coarse cut as coarse_filter (one shared function), the
                // survivors feeding straight into the same scan medium_filter
                // runs — the fused stage is those two calls back to back.
                P = screening::build_coarse_pairs(
                    periapsis_km, apoapsis_km, pad_km);

                if (time_filter) {
                    // Phase 10.2 time sieve: precompute per-pair scan intervals
                    // from the validated 10.1b construction (3 propagations/sat
                    // -> chord-anchored elements -> node-window crossing times),
                    // then scan each pair only inside them. Same EVENTS after
                    // the fine stage + report cut (the 10.1b contract).
                    std::vector<char> used(sats.size(), 0);
                    for (const auto& pr : P) {
                        used[pr.first] = 1; used[pr.second] = 1;
                    }
                    screening::AnchorSet anchors = screening::build_anchors(
                        sats, used, jd_start, jd_end);
                    screening::SieveParams params;
                    params.u_margin_rad =
                        sieve_u_margin_deg * 3.14159265358979323846 / 180.0;
                    params.osc_margin_km = sieve_osc_margin_km;
                    screening::SieveIndex sieve = screening::build_sieve_index(
                        anchors, P, jd_start, jd_end, step_sec, threshold_km,
                        params);
                    results = screening::run_medium_scan(
                        sats, P, jd_start, jd_end, step_sec, threshold_km,
                        &sieve);
                } else {
                    results = screening::run_medium_scan(
                        sats, P, jd_start, jd_end, step_sec, threshold_km);
                }
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
        py::arg("time_filter") = false,
        py::arg("sieve_u_margin_deg") = 0.5,
        py::arg("sieve_osc_margin_km") = 10.0,
        R"doc(
Fused coarse + medium conjunction screen (stage 1a) — a drop-in replacement
for coarse_filter() followed by medium_filter(), producing byte-identical rows.

The coarse altitude-band cut is computed internally and its survivor (i, j)
pairs are fed straight into the time-stepped medium scan WITHOUT ever crossing
into Python. On the full active catalog that's ~48 M pairs: ~5 GB as Python
tuples versus ~0.8 GB as a C++ vector here. (The whole screen's peak RSS is
dominated by the later fine stage, not this list; this removes the pair-list
share — measured ~2 GB at 10k. Same screening result either way.)

time_filter=True adds the Phase-10.2 TIME SIEVE (the H-C-R Filter III
construction validated no-skip on 1.4M real events — see
progress/week10_planning/stage1b_timefilter_spec.md): per-pair scan intervals
are precomputed around the mutual-node crossings, and each pair is evaluated
only inside them (~2.5% of pair-steps at the production catalog). Rows for
evaluated windows are byte-identical; windows entirely outside every interval
(the medium bound's spurious far-distance flags) are never produced — the
EVENT list after the fine stage + report cut is identical.

satrecs:      sequence of Satrec (passed by reference; t/error mutate).
periapsis_km: per-satellite perigee altitude, km (index-aligned with satrecs).
apoapsis_km:  per-satellite apogee altitude, km, same indexing.
pad_km:       coarse altitude-band margin (>= 0); see coarse_filter.
jd_start/end: scan window as absolute Julian Dates (UTC), end > start.
step_sec:     medium time step in seconds (> 0).
threshold_km: medium flag distance in km (> 0).
time_filter:  enable the time sieve (default False = classic full scan).
sieve_u_margin_deg: sieve base angular margin, deg (validated 0.25; ship 0.5).
sieve_osc_margin_km: mean-vs-osculating pad on the sieve's D_eff (km).

Returns: (n_pairs, rows) where
  n_pairs — number of coarse survivors (for profiling; the pairs themselves
            are never returned).
  rows    — list of (i, j, jd, distance_km); see medium_filter for the no-skip
            detection criterion and row semantics. With time_filter=True the
            rows are a subset (spurious far-distance windows dropped) whose
            post-fine EVENTS are identical.
Raises: ValueError (bad lengths/pad/window/step/threshold/margins),
        TypeError (non-Satrec item, named by index).
The GIL is released during the coarse cut, sieve build, and scan.
)doc"
    );

    // --- _sieve_anchors: internal, for oracle cross-validation ---
    m.def("_sieve_anchors",
        [](py::sequence satrecs, double jd0, double jd1) -> py::dict
        {
            std::vector<elsetrec*> sats =
                extract_satrecs(satrecs, "_sieve_anchors");
            std::vector<char> used(sats.size(), 1);
            screening::AnchorSet a;
            {
                py::gil_scoped_release release;
                a = screening::build_anchors(sats, used, jd0, jd1);
            }
            py::dict out;
            out["ok"] = std::vector<int>(a.ok.begin(), a.ok.end());
            out["inc"] = a.inc;
            out["raan0"] = a.raan0;
            out["raan_dot"] = a.raan_dot;
            out["lam0"] = a.lam0;
            out["lam_rate"] = a.lam_rate;
            out["m_rate"] = a.m_rate;
            out["ecc"] = a.ecc;
            out["rp"] = a.rp;
            out["u0"] = a.u0;
            out["curv"] = a.curv;
            return out;
        },
        py::arg("satrecs"), py::arg("jd0"), py::arg("jd1"),
        R"doc(
INTERNAL — the time sieve's per-satellite anchored elements (Phase 10.2).

Exposed so tests can lock the C++ anchor construction against the Python
oracle (progress/week10_planning/time_filter_gate.py) element-by-element:
same 3 propagations, same rv2coe, same equinoctial chord rates + curvature
margins. Not part of the public screening API.

Returns: dict of per-satellite lists (index-aligned with satrecs):
  ok, inc, raan0, raan_dot, lam0, lam_rate, m_rate, ecc, rp, u0, curv
  (angles rad, rates rad/day, rp km).
)doc"
    );
}
