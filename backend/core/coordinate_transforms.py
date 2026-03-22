"""
Coordinate Transforms — Converts SGP4 output (TEME) to geodetic (lat/lon/alt).

SGP4 outputs position and velocity in the TEME (True Equator, Mean Equinox) frame.
To get lat/lon/alt for map display and conjunction screening, we must convert:

    TEME → ECEF (via GMST Z-axis rotation)
    ECEF → geodetic (via SPICE recgeo)

The TEME→ECEF rotation uses the IAU 1982 GMST formula (same as Vallado's SGP4 code).
This skips polar motion corrections (~10m) and equation of equinoxes (~30m), both
well within SGP4's inherent ~1 km accuracy at epoch.

SPICE is used only for the final ECEF→geodetic conversion (recgeo), which needs
the Earth's shape model (flattening, equatorial radius).

References:
    - Vallado, "Revisiting Spacetrack Report #3" (AIAA 2006-6753), Section 3
    - SPICE recgeo documentation: https://naif.jpl.nasa.gov/naif/
    - See progress/notes/key_information.md for full context
"""

import math
from datetime import datetime
from pathlib import Path

import spiceypy as sp

# WGS-84 Earth ellipsoid constants (for geodetic conversion)
# Using WGS-84 here (not WGS-72) because geodetic conversion is about
# the physical shape of the Earth, not the gravity model used by SGP4.
EARTH_RADIUS_WGS84 = 6378.137      # km (equatorial radius)
EARTH_FLATTENING = 1.0 / 298.257223563

# SPICE kernel paths
_KERNEL_DIR = Path(__file__).parent.parent / "data" / "spice_kernels"
_KERNELS_LOADED = False


def _ensure_kernels():
    """Load SPICE kernels once. Idempotent — safe to call repeatedly."""
    global _KERNELS_LOADED
    if _KERNELS_LOADED:
        return

    kernels = [
        _KERNEL_DIR / "naif0012.tls",      # leap seconds
        _KERNEL_DIR / "pck00011.tpc",       # planetary constants
        _KERNEL_DIR / "earth_latest_high_prec.bpc",  # Earth orientation
    ]
    for k in kernels:
        if not k.exists():
            raise FileNotFoundError(
                f"SPICE kernel not found: {k}\n"
                "Run the setup script to download kernels."
            )
        sp.furnsh(str(k))

    _KERNELS_LOADED = True


def gmst_from_jd(jd: float) -> float:
    """
    Compute Greenwich Mean Sidereal Time from Julian Date.

    Uses the IAU 1982 formula (same as Vallado's SGP4 implementation).
    Returns GMST in radians, range [0, 2π).

    Args:
        jd: Julian Date (e.g., 2461120.5 for 2026-03-21 00:00:00 UTC)

    Returns:
        GMST angle in radians
    """
    # Julian centuries from J2000.0 (2000 Jan 1 12:00:00 TT)
    T = (jd - 2451545.0) / 36525.0

    # GMST in seconds of time (IAU 1982)
    gmst_sec = (
        67310.54841
        + (876600.0 * 3600.0 + 8640184.812866) * T
        + 0.093104 * T * T
        - 6.2e-6 * T * T * T
    )

    # Convert seconds of time → radians, mod 2π
    gmst_rad = math.fmod(gmst_sec * math.pi / 43200.0, 2.0 * math.pi)
    if gmst_rad < 0:
        gmst_rad += 2.0 * math.pi

    return gmst_rad


def teme_to_ecef(
    pos_teme: tuple[float, float, float],
    jd: float,
    vel_teme: tuple[float, float, float] | None = None,
) -> tuple[list[float], list[float] | None]:
    """
    Rotate position (and optionally velocity) from TEME to ECEF frame.

    The TEME and ECEF Z-axes are nearly aligned (both point toward the
    celestial/geographic pole). The only difference is Earth's rotation
    angle (GMST) around Z. So this is a single rotation matrix.

    Args:
        pos_teme: (x, y, z) position in km, TEME frame
        jd: Julian Date at the propagation time
        vel_teme: (vx, vy, vz) velocity in km/s, TEME frame (optional)

    Returns:
        (pos_ecef, vel_ecef) — position in km, velocity in km/s (or None)
    """
    gmst = gmst_from_jd(jd)
    cos_g = math.cos(gmst)
    sin_g = math.sin(gmst)

    # Rotation matrix R3(-GMST): rotate from TEME to PEF/ECEF
    x, y, z = pos_teme
    pos_ecef = [
         cos_g * x + sin_g * y,
        -sin_g * x + cos_g * y,
        z,
    ]

    vel_ecef = None
    if vel_teme is not None:
        # Velocity transform includes ω×r correction (Earth's angular velocity)
        # ω_earth = 7.292115e-5 rad/s (IAU value)
        omega_earth = 7.292115e-5  # rad/s
        vx, vy, vz = vel_teme

        # First rotate velocity to ECEF frame
        vel_rot = [
             cos_g * vx + sin_g * vy,
            -sin_g * vx + cos_g * vy,
            vz,
        ]

        # Then subtract ω×r (cross product of Earth's rotation with ECEF position)
        # ω = [0, 0, ω_earth], r = pos_ecef
        # ω×r = [-ω*y, ω*x, 0]
        vel_ecef = [
            vel_rot[0] + omega_earth * pos_ecef[1],
            vel_rot[1] - omega_earth * pos_ecef[0],
            vel_rot[2],
        ]

    return pos_ecef, vel_ecef


def ecef_to_geodetic(pos_ecef: list[float]) -> tuple[float, float, float]:
    """
    Convert ECEF (x, y, z) in km to geodetic (lat, lon, alt).

    Uses SPICE's recgeo() with the WGS-84 Earth ellipsoid.

    Args:
        pos_ecef: (x, y, z) position in km, ECEF frame

    Returns:
        (lat_deg, lon_deg, alt_km) — latitude/longitude in degrees, altitude in km
    """
    _ensure_kernels()
    lon_rad, lat_rad, alt_km = sp.recgeo(pos_ecef, EARTH_RADIUS_WGS84, EARTH_FLATTENING)
    return math.degrees(lat_rad), math.degrees(lon_rad), alt_km


def teme_to_geodetic(
    pos_teme: tuple[float, float, float],
    jd: float,
    vel_teme: tuple[float, float, float] | None = None,
) -> dict:
    """
    Full pipeline: TEME position → geodetic coordinates.

    This is the main function downstream code should call.

    Args:
        pos_teme: (x, y, z) position in km from SGP4
        jd: Julian Date at the propagation time
        vel_teme: (vx, vy, vz) velocity in km/s from SGP4 (optional)

    Returns:
        dict with keys:
            lat: latitude in degrees (-90 to 90)
            lon: longitude in degrees (-180 to 180)
            alt: altitude in km above WGS-84 ellipsoid
            pos_ecef: [x, y, z] in km (for conjunction distance calculations)
            vel_ecef: [vx, vy, vz] in km/s (or None)
    """
    pos_ecef, vel_ecef = teme_to_ecef(pos_teme, jd, vel_teme)
    lat, lon, alt = ecef_to_geodetic(pos_ecef)

    return {
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "pos_ecef": pos_ecef,
        "vel_ecef": vel_ecef,
    }


def utc_to_jd(utc_dt: datetime) -> tuple[float, float]:
    """
    Convert a UTC datetime to Julian Date components.

    Returns (jd_whole, jd_fraction) for use with sgp4's Satrec.sgp4().

    Args:
        utc_dt: datetime object (must be UTC)

    Returns:
        (jd_whole, jd_fraction) — Julian Date split for precision
    """
    from sgp4.api import jday
    jd_whole, jd_frac = jday(
        utc_dt.year, utc_dt.month, utc_dt.day,
        utc_dt.hour, utc_dt.minute,
        utc_dt.second + utc_dt.microsecond / 1e6,
    )
    return jd_whole, jd_frac
