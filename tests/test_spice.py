#!/usr/bin/env python3
"""Test SPICE kernel loading and coordinate transformation."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import spiceypy as sp
from pathlib import Path

# Paths to SPICE kernels
kernel_dir = Path(__file__).parent.parent / "backend" / "data" / "spice_kernels"
lsk_kernel = kernel_dir / "naif0012.tls"
pck_kernel = kernel_dir / "pck00011.tpc"
bpc_kernel = kernel_dir / "earth_latest_high_prec.bpc"

print("Testing SPICE setup...")
print(f"Kernel directory: {kernel_dir}")

# Check kernels exist
for kernel in [lsk_kernel, pck_kernel, bpc_kernel]:
    if kernel.exists():
        print(f"  ✓ {kernel.name}")
    else:
        print(f"  ✗ {kernel.name} NOT FOUND")
        sys.exit(1)

# Load kernels
print("\nLoading kernels...")
sp.furnsh(str(lsk_kernel))
sp.furnsh(str(pck_kernel))
sp.furnsh(str(bpc_kernel))
print("  ✓ All kernels loaded")

# Test: Convert a known ECI position to lat/lon/alt
# ISS approximate position at 2024-03-21 00:00:00 UTC (example)
# ECI: roughly (6700 km, 1200 km, 400 km) — these are made-up but reasonable numbers
et = sp.str2et("2024-03-21 00:00:00")  # UTC time to SPICE ET (Ephemeris Time)
eci_position = [6700.0, 1200.0, 400.0]  # km, in ECI frame

print(f"\nTest coordinate transformation:")
print(f"  Input (ECI): {eci_position} km")

# Transform from ECI to ECEF using Earth rotation matrix
# Get the Earth rotation from J2000 to ITRF93 (Earth-fixed frame)
rotation_matrix = sp.pxform("J2000", "ITRF93", et)
ecef_position = sp.mxv(rotation_matrix, eci_position)
print(f"  ECEF: {ecef_position} km")

# Convert ECEF (x, y, z) to geodetic (lon, lat, alt)
# Using Earth as the reference body
lon, lat, alt = sp.recgeo(ecef_position, 6378.137, 0.0033528)  # WGS84 params

# Convert from radians to degrees
lon_deg = lon * 57.29577951308232  # rad to deg
lat_deg = lat * 57.29577951308232

print(f"  Geodetic: lon={lon_deg:.2f}°, lat={lat_deg:.2f}°, alt={alt:.1f} km")
print("  ✓ Coordinate transformation works!")

print("\n✓ SPICE setup verified successfully!")
