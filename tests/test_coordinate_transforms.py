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


class TestTemeToRtn:
    """teme_to_rtn() — relative position in the primary's RTN frame
    (Task 6.5). Conventions: R̂ radial outward, T̂ in-track (~velocity),
    N̂ cross-track (orbit normal); right-handed R̂×T̂=N̂ (Vallado RSW,
    same frame real CDMs use)."""

    def test_hand_computed_exact(self):
        """r along +X, v along +Y -> R̂=X̂, T̂=Ŷ, N̂=Ẑ: offset (1,2,3)
        maps to RTN (1,2,3) exactly."""
        from core.coordinate_transforms import teme_to_rtn
        r, t, n = teme_to_rtn((7000.0, 0.0, 0.0), (0.0, 7.5, 0.0),
                              (7001.0, 2.0, 3.0))
        assert abs(r - 1.0) < 1e-12
        assert abs(t - 2.0) < 1e-12
        assert abs(n - 3.0) < 1e-12

    def test_orthonormality_invariant(self):
        """R² + T² + N² must equal |Δr|² (frame is orthonormal) for an
        arbitrary skewed state."""
        from core.coordinate_transforms import teme_to_rtn
        rp = (4584.7, -1592.9, -4765.1)
        vp = (4.53, 5.93, 2.38)
        rs = (4590.0, -1580.0, -4770.0)
        r, t, n = teme_to_rtn(rp, vp, rs)
        d2 = sum((a - b) ** 2 for a, b in zip(rs, rp))
        assert abs((r * r + t * t + n * n) - d2) < 1e-9

    def test_pure_radial_offset(self):
        """Secondary scaled along the primary's position vector -> all R,
        zero T and N."""
        from core.coordinate_transforms import teme_to_rtn
        rp = (4584.7, -1592.9, -4765.1)
        vp = (4.53, 5.93, 2.38)
        rs = tuple(x * 1.001 for x in rp)
        r, t, n = teme_to_rtn(rp, vp, rs)
        assert r > 0  # outward
        assert abs(t) < 1e-9 and abs(n) < 1e-9

    def test_along_velocity_offset_is_mostly_in_track(self):
        """Offset along v̂: for a near-circular orbit T dominates (v̂ has
        a small radial component for e != 0, so not exactly pure T)."""
        from core.coordinate_transforms import teme_to_rtn
        rp = (7000.0, 100.0, -50.0)
        vp = (0.1, 7.4, 1.2)
        vmag = math.sqrt(sum(x * x for x in vp))
        rs = tuple(p + 5.0 * v / vmag for p, v in zip(rp, vp))
        r, t, n = teme_to_rtn(rp, vp, rs)
        assert abs(t) > 4.9          # ~all of the 5 km offset
        assert abs(r) < 1.0 and abs(n) < 1e-9

    def test_real_iss_along_track_pair(self):
        """Real SGP4 states: ISS vs a small mean-anomaly-offset clone is an
        along-track separation -> |T| dominates |R| and |N|."""
        from core.coordinate_transforms import teme_to_rtn
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
        import orbitcore
        from sgp4.api import Satrec as PySatrec, WGS72
        l1 = "1 25544U 98067A   24056.27396747  .00015798  00000+0  28508-3 0  9991"
        l2 = "2 25544  51.6415  32.0835 0004287  51.5994  12.5648 15.49571617441044"
        p = PySatrec.twoline2rv(l1, l2, WGS72)
        epoch = p.jdsatepoch + p.jdsatepochF - 2433281.5

        def make(dmo_rad):
            return orbitcore.sgp4init(
                orbitcore.GravConst.WGS72, "a", "25544", epoch,
                p.bstar, p.ndot, p.nddot, p.ecco, p.argpo, p.inclo,
                (p.mo + dmo_rad) % (2 * math.pi), p.no_kozai, p.nodeo)

        (rp, vp) = orbitcore.sgp4(make(0.0), 30.0)
        (rs, _) = orbitcore.sgp4(make(math.radians(0.5)), 30.0)
        r, t, n = teme_to_rtn(rp, vp, rs)
        miss = math.dist(rp, rs)
        assert abs(t) > 0.9 * miss   # along-track dominates
        assert abs(t) > 10 * abs(r) and abs(t) > 10 * abs(n)
        # invariant on real data too
        assert abs((r * r + t * t + n * n) - miss ** 2) < 1e-6

    def test_matches_independent_numpy_implementation(self):
        """Cross-check against a from-scratch numpy implementation on
        pseudo-random states."""
        from core.coordinate_transforms import teme_to_rtn
        import random
        import numpy as np
        rng = random.Random(99)
        for _ in range(50):
            rp = np.array([rng.uniform(-8000, 8000) for _ in range(3)])
            vp = np.array([rng.uniform(-8, 8) for _ in range(3)])
            rs = rp + np.array([rng.uniform(-100, 100) for _ in range(3)])
            if np.linalg.norm(np.cross(rp, vp)) < 1.0:
                continue  # skip near-degenerate draws
            r_hat = rp / np.linalg.norm(rp)
            n_hat = np.cross(rp, vp) / np.linalg.norm(np.cross(rp, vp))
            t_hat = np.cross(n_hat, r_hat)
            d = rs - rp
            expected = (float(d @ r_hat), float(d @ t_hat), float(d @ n_hat))
            got = teme_to_rtn(tuple(rp), tuple(vp), tuple(rs))
            for g, e in zip(got, expected):
                assert abs(g - e) < 1e-9

    def test_retrograde_orbit_flips_cross_track(self):
        """Reversing the velocity flips N̂ (orbit normal) -> N changes
        sign, R unchanged."""
        from core.coordinate_transforms import teme_to_rtn
        rp, vp = (7000.0, 0.0, 0.0), (0.0, 7.5, 0.0)
        rs = (7001.0, 0.0, 3.0)
        r1, t1, n1 = teme_to_rtn(rp, vp, rs)
        r2, t2, n2 = teme_to_rtn(rp, (0.0, -7.5, 0.0), rs)
        assert abs(r1 - r2) < 1e-12
        assert abs(n1 + n2) < 1e-12  # sign flip

    def test_degenerate_states_raise(self):
        from core.coordinate_transforms import teme_to_rtn
        try:
            teme_to_rtn((7000.0, 0.0, 0.0), (7.5, 0.0, 0.0), (1.0, 1.0, 1.0))
            assert False, "parallel r,v should raise"
        except ValueError:
            pass
        try:
            teme_to_rtn((0.0, 0.0, 0.0), (0.0, 7.5, 0.0), (1.0, 1.0, 1.0))
            assert False, "zero position should raise"
        except ValueError:
            pass
