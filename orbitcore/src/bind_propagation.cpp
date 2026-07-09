/*
 * Python bindings — propagation and time conversion.
 *
 *   - sgp4()            — propagate one satellite to tsince (min from epoch)
 *   - propagate_batch() — propagate many satellites in one Python->C++
 *                         crossing (None sentinel on per-sat failure)
 *   - jday() / invjday() — calendar date <-> Julian Date
 *
 * Moved verbatim out of bindings.cpp in the Phase-10.2 file split.
 */
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include "SGP4.h"
#include "bindings.h"

namespace py = pybind11;

void bind_propagation(py::module_& m) {
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
}
