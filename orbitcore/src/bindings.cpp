/*
 * OrbitWatch pybind11 module entry — Vallado's SGP4 + the conjunction screen.
 *
 * The bindings are organized one topic per file (see include/bindings.h):
 *   src/bind_satrec.cpp      — the satellite record: GravConst, Satrec,
 *                              sgp4init, getgravconst
 *   src/bind_propagation.cpp — propagation + time: sgp4, propagate_batch,
 *                              jday, invjday
 *   src/bind_screening.cpp   — the conjunction screen's Python boundary:
 *                              coarse_filter, medium_filter, screen_pairs
 *   src/screening.cpp        — the screening ENGINE (pure C++, no Python):
 *                              the coarse cut + the 6.3 time-major medium scan
 *
 * Registration order matters only for readability (record -> propagate ->
 * screen mirrors the pipeline); pybind11 resolves types lazily.
 */
#include <pybind11/pybind11.h>

#include "SGP4.h"      // SGP4Version
#include "bindings.h"
#include "hello.h"

namespace py = pybind11;

PYBIND11_MODULE(orbitcore, m) {
    m.doc() = "OrbitWatch C++ core — SGP4 propagation engine (Vallado 2020)";

    // Keep the hello world function for verification
    m.def("hello_world", &hello_world, "A function that returns a hello message");

    bind_satrec(m);        // GravConst, Satrec, sgp4init, getgravconst
    bind_propagation(m);   // sgp4, propagate_batch, jday, invjday
    bind_screening(m);     // coarse_filter, medium_filter, screen_pairs

    // --- Version info ---
    m.attr("SGP4_VERSION") = SGP4Version;
}
