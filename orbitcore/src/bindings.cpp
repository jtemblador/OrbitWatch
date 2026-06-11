/*
 * OrbitWatch pybind11 bindings for Vallado's SGP4 implementation.
 *
 * Exposes:
 *   - GravConst enum (wgs72old, wgs72, wgs84)
 *   - Satrec class (elsetrec struct with key fields)
 *   - sgp4init() — initialize satellite record from OMM elements
 *   - sgp4() — propagate to time T, returns (pos, vel) tuples
 *   - propagate_batch() — propagate many satellites in one call (None on per-sat error)
 *   - jday() — calendar date to Julian Date
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <tuple>
#include <string>
#include <stdexcept>
#include "SGP4.h"
#include "hello.h"

namespace py = pybind11;

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
