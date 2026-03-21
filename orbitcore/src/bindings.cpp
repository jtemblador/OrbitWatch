#include <pybind11/pybind11.h>
#include "hello.h"

PYBIND11_MODULE(orbitcore, m) {
    m.def("hello_world", &hello_world, "A function that returns a hello message");
}
