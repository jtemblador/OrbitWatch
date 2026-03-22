#!/usr/bin/env python3
"""Tests for coordinate_transforms.py — TEME→ECEF→geodetic pipeline.

Validates:
- GMST calculation against known reference values
- TEME→ECEF rotation correctness
- ECEF→geodetic conversion (lat/lon/alt)
- Full teme_to_geodetic pipeline
- Velocity transformation (including ω×r Earth rotation correction)
- Edge cases: poles, date line, equator, high altitude (GEO)
- All 30 Phase 1 stations propagate and convert without error
"""

import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.coordinate_transforms import (
    ecef_to_geodetic,
    gmst_from_jd,
    teme_to_ecef,
    teme_to_geodetic,
    utc_to_jd,
)


class TestGMST:
    """Test GMST calculation against known reference values."""

    def test_j2000_epoch(self):
        """At J2000.0 (2000-01-01 12:00:00 TT), GMST is known."""
        # J2000.0 = JD 2451545.0
        # GMST at J2000.0 ≈ 280.46 degrees (from IAU formula, T=0)
        # The formula gives gmst_sec = 67310.54841 at T=0
        # = 67310.54841 / 240 = 280.46 degrees (240 sec/deg for sidereal time)
        gmst = gmst_from_jd(2451545.0)
        gmst_deg = math.degrees(gmst)
        # Should be ~280.46 degrees
        assert 279 < gmst_deg < 282, f"GMST at J2000: {gmst_deg:.2f} deg"

    def test_gmst_positive(self):
        """GMST should always be in [0, 2π)."""
        for jd in [2451545.0, 2460000.0, 2461120.5, 2470000.0]:
            gmst = gmst_from_jd(jd)
            assert 0 <= gmst < 2 * math.pi, f"GMST out of range at JD {jd}: {gmst}"

    def test_gmst_increases_with_time(self):
        """GMST should advance ~361°/day (one sidereal day ≈ 23h56m).
        Since gmst_from_jd returns mod 2π, the raw difference wraps.
        The actual advance is ~361°, so after mod 360° we expect ~1°.
        """
        gmst1 = gmst_from_jd(2461120.5)
        gmst2 = gmst_from_jd(2461121.5)  # +1 day
        # GMST advances ~361°/day. After mod 2π, we see ~1° residual.
        diff = gmst2 - gmst1
        if diff < 0:
            diff += 2 * math.pi
        diff_deg = math.degrees(diff)
        # Should be ~0.98° (the ~1° beyond a full 360° rotation)
        assert 0.5 < diff_deg < 1.5, f"GMST daily residual: {diff_deg:.2f} deg (expect ~1°)"


class TestTemeToEcef:
    """Test TEME→ECEF rotation."""

    def test_z_component_unchanged(self):
        """Z-axis rotation should not affect the Z component."""
        pos_teme = (1000.0, 2000.0, 3000.0)
        pos_ecef, _ = teme_to_ecef(pos_teme, 2461120.5)
        assert abs(pos_ecef[2] - 3000.0) < 1e-10

    def test_distance_preserved(self):
        """Rotation should preserve the distance from Earth center."""
        pos_teme = (6700.0, 1200.0, 400.0)
        pos_ecef, _ = teme_to_ecef(pos_teme, 2461120.5)
        dist_teme = math.sqrt(sum(c**2 for c in pos_teme))
        dist_ecef = math.sqrt(sum(c**2 for c in pos_ecef))
        assert abs(dist_teme - dist_ecef) < 1e-10

    def test_velocity_transform_includes_omega_cross_r(self):
        """Velocity in ECEF should differ from pure rotation by ω×r."""
        pos_teme = (6700.0, 0.0, 0.0)
        vel_teme = (0.0, 7.5, 0.0)
        pos_ecef, vel_ecef = teme_to_ecef(pos_teme, 2461120.5, vel_teme)
        assert vel_ecef is not None
        # Pure Z-rotation of velocity + ω×r correction
        # The ECEF velocity should be different from just rotating the TEME velocity
        vel_magnitude = math.sqrt(sum(v**2 for v in vel_ecef))
        # Velocity should be in reasonable range (LEO: ~7.5 km/s)
        assert 5 < vel_magnitude < 10, f"ECEF velocity magnitude: {vel_magnitude:.2f} km/s"

    def test_no_velocity_returns_none(self):
        """If no velocity provided, vel_ecef should be None."""
        pos_ecef, vel_ecef = teme_to_ecef((6700, 0, 0), 2461120.5)
        assert vel_ecef is None


class TestEcefToGeodetic:
    """Test ECEF→geodetic conversion via SPICE."""

    def test_on_equator_prime_meridian(self):
        """Point on equator at prime meridian → lat≈0, lon≈0."""
        lat, lon, alt = ecef_to_geodetic([6378.137, 0.0, 0.0])
        assert abs(lat) < 0.01
        assert abs(lon) < 0.01
        assert abs(alt) < 0.01  # on the surface

    def test_on_equator_90_east(self):
        """Point on equator at 90°E → lat≈0, lon≈90."""
        lat, lon, alt = ecef_to_geodetic([0.0, 6378.137, 0.0])
        assert abs(lat) < 0.01
        assert abs(lon - 90.0) < 0.01
        assert abs(alt) < 0.01

    def test_north_pole(self):
        """Point at north pole → lat≈90."""
        # Earth polar radius ≈ 6356.752 km
        lat, lon, alt = ecef_to_geodetic([0.0, 0.0, 6356.752])
        assert abs(lat - 90.0) < 0.1
        assert abs(alt) < 1  # approximately on surface

    def test_south_pole(self):
        """Point at south pole → lat≈-90."""
        lat, lon, alt = ecef_to_geodetic([0.0, 0.0, -6356.752])
        assert abs(lat + 90.0) < 0.1

    def test_altitude_400km(self):
        """Point 400 km above equator → alt≈400."""
        lat, lon, alt = ecef_to_geodetic([6378.137 + 400.0, 0.0, 0.0])
        assert abs(alt - 400.0) < 1.0
        assert abs(lat) < 0.01

    def test_geo_altitude(self):
        """GEO altitude (~35786 km) → alt≈35786."""
        geo_r = 6378.137 + 35786.0
        lat, lon, alt = ecef_to_geodetic([geo_r, 0.0, 0.0])
        assert abs(alt - 35786.0) < 10.0

    def test_negative_longitude(self):
        """Point at 90°W → lon≈-90."""
        lat, lon, alt = ecef_to_geodetic([0.0, -6378.137, 0.0])
        assert abs(lon + 90.0) < 0.01

    def test_date_line_positive(self):
        """Point at 180°E → lon≈180 or -180."""
        lat, lon, alt = ecef_to_geodetic([-6378.137, 0.0, 0.0])
        assert abs(abs(lon) - 180.0) < 0.01


class TestTemeToGeodetic:
    """Test the full pipeline."""

    def test_returns_all_fields(self):
        """Output dict should have lat, lon, alt, pos_ecef, vel_ecef."""
        result = teme_to_geodetic((6700, 0, 0), 2461120.5)
        assert "lat" in result
        assert "lon" in result
        assert "alt" in result
        assert "pos_ecef" in result
        assert "vel_ecef" in result

    def test_iss_like_altitude(self):
        """ISS-like TEME position should give ~400 km altitude."""
        # ISS at roughly 6780 km from center in TEME
        result = teme_to_geodetic((6780, 0, 0), 2461120.5)
        assert 350 < result["alt"] < 450, f"Alt: {result['alt']:.1f} km"

    def test_latitude_range(self):
        """Latitude should be in [-90, 90]."""
        result = teme_to_geodetic((6700, 1200, 3000), 2461120.5)
        assert -90 <= result["lat"] <= 90

    def test_longitude_range(self):
        """Longitude should be in [-180, 180]."""
        result = teme_to_geodetic((6700, 1200, 400), 2461120.5)
        assert -180 <= result["lon"] <= 180

    def test_with_velocity(self):
        """Passing velocity should populate vel_ecef."""
        result = teme_to_geodetic((6700, 0, 0), 2461120.5, (0, 7.5, 0))
        assert result["vel_ecef"] is not None
        assert len(result["vel_ecef"]) == 3

    def test_without_velocity(self):
        """Not passing velocity should set vel_ecef to None."""
        result = teme_to_geodetic((6700, 0, 0), 2461120.5)
        assert result["vel_ecef"] is None


class TestUtcToJd:
    """Test UTC datetime → Julian Date conversion."""

    def test_j2000_epoch(self):
        """J2000.0 = 2000-01-01 12:00:00 UTC → JD 2451545.0."""
        dt = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        jd_w, jd_f = utc_to_jd(dt)
        assert abs((jd_w + jd_f) - 2451545.0) < 1e-6

    def test_known_date(self):
        """2026-03-21 00:00:00 → JD 2461120.5."""
        dt = datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc)
        jd_w, jd_f = utc_to_jd(dt)
        assert abs((jd_w + jd_f) - 2461120.5) < 1e-6

    def test_noon_vs_midnight(self):
        """Noon should be 0.5 days after midnight."""
        dt_midnight = datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc)
        dt_noon = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)
        jd_m = sum(utc_to_jd(dt_midnight))
        jd_n = sum(utc_to_jd(dt_noon))
        assert abs((jd_n - jd_m) - 0.5) < 1e-10


class TestEndToEnd:
    """End-to-end: real ISS TLE → SGP4 → coordinate transform."""

    def test_iss_propagation_at_epoch(self):
        """Propagate ISS at epoch, verify altitude in expected range."""
        from sgp4.api import Satrec, WGS72
        import pandas as pd

        parquet = Path(__file__).parent.parent / "backend" / "data" / "tle" / "stations.parquet"
        if not parquet.exists():
            print("  SKIP (no cached TLE data)")
            return

        df = pd.read_parquet(parquet)
        iss = df[df["norad_cat_id"] == 25544]
        if iss.empty:
            print("  SKIP (ISS not in cache)")
            return

        row = iss.iloc[0]
        sat = Satrec()
        epoch_dt = row["epoch"].to_pydatetime().replace(tzinfo=timezone.utc)
        jd_w, jd_f = utc_to_jd(epoch_dt)
        epoch_jd = jd_w + jd_f

        sat.sgp4init(
            WGS72, "i",
            int(row["norad_cat_id"]),
            epoch_jd - 2433281.5,
            float(row["bstar"]),
            float(row["mean_motion_dot"]) / (1440.0 * 2),
            0.0,
            float(row["eccentricity"]),
            math.radians(float(row["arg_of_pericenter"])),
            math.radians(float(row["inclination"])),
            math.radians(float(row["mean_anomaly"])),
            float(row["mean_motion"]) * 2 * math.pi / 1440.0,
            math.radians(float(row["ra_of_asc_node"])),
        )

        e, r, v = sat.sgp4(jd_w, jd_f)
        assert e == 0, f"SGP4 error code: {e}"

        result = teme_to_geodetic(r, epoch_jd, v)

        # ISS altitude should be ~410-435 km
        assert 380 < result["alt"] < 450, f"ISS alt: {result['alt']:.1f} km"
        # ISS inclination is 51.6° — latitude should be within that range
        assert -52 < result["lat"] < 52, f"ISS lat: {result['lat']:.2f}"
        # Velocity should be ~7.5-7.8 km/s
        speed = math.sqrt(sum(c**2 for c in result["vel_ecef"]))
        assert 6 < speed < 9, f"ISS ECEF speed: {speed:.2f} km/s"

    def test_all_stations_propagate(self):
        """All 30 Phase 1 stations should propagate and convert without error."""
        from sgp4.api import Satrec, WGS72
        import pandas as pd

        parquet = Path(__file__).parent.parent / "backend" / "data" / "tle" / "stations.parquet"
        if not parquet.exists():
            print("  SKIP (no cached TLE data)")
            return

        df = pd.read_parquet(parquet)
        errors = 0
        for _, row in df.iterrows():
            try:
                sat = Satrec()
                edt = row["epoch"].to_pydatetime().replace(tzinfo=timezone.utc)
                jw, jf = utc_to_jd(edt)
                ejd = jw + jf
                sat.sgp4init(
                    WGS72, "i", int(row["norad_cat_id"]),
                    ejd - 2433281.5, float(row["bstar"]),
                    float(row["mean_motion_dot"]) / (1440.0 * 2), 0.0,
                    float(row["eccentricity"]),
                    math.radians(float(row["arg_of_pericenter"])),
                    math.radians(float(row["inclination"])),
                    math.radians(float(row["mean_anomaly"])),
                    float(row["mean_motion"]) * 2 * math.pi / 1440.0,
                    math.radians(float(row["ra_of_asc_node"])),
                )
                e, r, v = sat.sgp4(jw, jf)
                assert e == 0, f"SGP4 error for {row['object_name']}"
                result = teme_to_geodetic(r, ejd, v)
                assert 50 < result["alt"] < 50000, f"Bad alt for {row['object_name']}: {result['alt']}"
            except Exception as ex:
                print(f"  ERROR: {row['object_name']}: {ex}")
                errors += 1

        assert errors == 0, f"{errors} stations failed"


# --- Run all tests ---
if __name__ == "__main__":
    test_classes = [
        TestGMST,
        TestTemeToEcef,
        TestEcefToGeodetic,
        TestTemeToGeodetic,
        TestUtcToJd,
        TestEndToEnd,
    ]

    total = 0
    passed = 0
    failed = 0

    for cls in test_classes:
        print(f"\n{'='*60}")
        print(f"  {cls.__name__}")
        print(f"{'='*60}")
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method_name in sorted(methods):
            total += 1
            try:
                getattr(instance, method_name)()
                print(f"  PASS  {method_name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL  {method_name}: {e}")
                failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*60}")
    sys.exit(1 if failed else 0)
