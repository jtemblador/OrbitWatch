cmake_minimum_required(VERSION 3.12)
project(orbitcore)

# No build type -> CMake passes NO -O flag (an -O0 engine). The CI/deploy
# invocations don't set one, so default to Release here (measured: full
# catalog 24 h screen 689 s -> 242 s, events byte-identical).
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find Python and pybind11
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

# Create the pybind11 module
pybind11_add_module(orbitcore
    src/bindings.cpp
    src/bind_satrec.cpp
    src/bind_propagation.cpp
    src/bind_screening.cpp
    src/screening.cpp
    src/hello.cpp
    src/SGP4.cpp
)

target_include_directories(orbitcore PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)

# OpenMP parallelizes the Phase-10.4 fine stage (refine_rows: one row per
# task, private satrec copies). Optional: without it the refine runs
# single-threaded — same results, just slower (the pragma compiles away).
find_package(OpenMP)
if(OpenMP_CXX_FOUND)
    target_link_libraries(orbitcore PRIVATE OpenMP::OpenMP_CXX)
endif()
