/*
 * OrbitWatch Python-binding registration — one function per topic file.
 *
 * The pybind11 module is assembled in src/bindings.cpp (the ~40-line entry
 * point), which calls these in order:
 *   bind_satrec()      — src/bind_satrec.cpp:      GravConst, Satrec,
 *                        sgp4init, getgravconst (the satellite record)
 *   bind_propagation() — src/bind_propagation.cpp: sgp4, propagate_batch,
 *                        jday, invjday (propagation + time conversion)
 *   bind_screening()   — src/bind_screening.cpp:   coarse_filter,
 *                        medium_filter, screen_pairs (the conjunction
 *                        screen's Python boundary; the engine itself is
 *                        pure C++ in src/screening.cpp)
 */
#ifndef ORBITWATCH_BINDINGS_H
#define ORBITWATCH_BINDINGS_H

#include <pybind11/pybind11.h>

void bind_satrec(pybind11::module_& m);
void bind_propagation(pybind11::module_& m);
void bind_screening(pybind11::module_& m);

#endif  // ORBITWATCH_BINDINGS_H
