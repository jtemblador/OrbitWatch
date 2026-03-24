cmake_minimum_required(VERSION 3.12)
project(orbitcore)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find Python and pybind11
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

# Create the pybind11 module
pybind11_add_module(orbitcore
    src/bindings.cpp
    src/hello.cpp
    src/SGP4.cpp
)

target_include_directories(orbitcore PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/include)
