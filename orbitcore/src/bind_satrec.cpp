/*
 * Python bindings — the satellite record and its initialization.
 *
 *   - GravConst enum (wgs72old, wgs72, wgs84)
 *   - Satrec class (elsetrec struct with key fields)
 *   - sgp4init()     — initialize a satellite record from OMM elements
 *   - getgravconst() — gravity-model constants
 *
 * Moved verbatim out of bindings.cpp in the Phase-10.2 file split.
 */
#include <pybind11/pybind11.h>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <string>

#include "SGP4.h"
#include "bindings.h"

namespace py = pybind11;

void bind_satrec(py::module_& m) {
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
                // Bound to the field width (9 digits + NUL) — never overflow.
                strncpy(s.satnum, val.c_str(), sizeof(s.satnum) - 1);
                s.satnum[sizeof(s.satnum) - 1] = '\0';
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

        // Secular rates (rad/min), set by sgp4init — the linear terms SGP4 uses
        // to advance the mean elements: mp = mo + mdot*t, argpp = argpo +
        // argpdot*t, nodep = nodeo + nodedot*t. Exposed for the Phase-10 time
        // filter's per-step node-window geometry (see progress/week10_planning).
        .def_readonly("mdot", &elsetrec::mdot)
        .def_readonly("argpdot", &elsetrec::argpdot)
        .def_readonly("nodedot", &elsetrec::nodedot)

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

            // satnum needs to be a char array. Bound the copy to the elsetrec
            // field width so the strcpy sgp4init does into satrec.satnum (a
            // char[10] = 9 digits + NUL) can never overflow, whatever comes in.
            char satn[sizeof(satrec.satnum)];
            strncpy(satn, satnum.c_str(), sizeof(satn) - 1);
            satn[sizeof(satn) - 1] = '\0';

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
}
