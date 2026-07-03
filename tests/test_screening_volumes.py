#!/usr/bin/env python3
"""Tests for backend/core/screening_volumes.py — the SFS Handbook (Table 3)
RTN screening ellipsoids and regime classification (Task 7.2).

Pure / deterministic (no propagation): regime boundaries, the ellipsoid
membership test, the circumscribing radius (the medium-filter no-skip gross
threshold), and the catalog-wide gross threshold.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.screening_volumes import (  # noqa: E402
    DEEP_SPACE,
    LEO_1,
    LEO_2,
    LEO_3,
    LEO_4,
    ScreeningVolume,
    gross_threshold_km,
    is_screenable,
    regime_for,
)


class TestRegimeFor:
    """regime_for(): SFS Table 3 keyed by perigee, gated on ecc < 0.25."""

    def test_leo_perigee_boundaries(self):
        # boundaries are inclusive on the upper edge (<=)
        assert regime_for(200.0, 0.001, 91.0) is LEO_1
        assert regime_for(500.0, 0.001, 95.0) is LEO_1     # <= 500
        assert regime_for(500.1, 0.001, 95.0) is LEO_2
        assert regime_for(750.0, 0.001, 100.0) is LEO_2    # <= 750
        assert regime_for(750.1, 0.001, 100.0) is LEO_3
        assert regime_for(1200.0, 0.001, 109.0) is LEO_3   # <= 1200
        assert regime_for(1200.1, 0.001, 110.0) is LEO_4
        assert regime_for(2000.0, 0.001, 127.0) is LEO_4   # <= 2000

    def test_starlink_is_leo_1(self):
        # ~550 km circular Starlink shell
        assert regime_for(545.0, 0.0001, 95.6) is LEO_2  # 545 > 500 -> LEO 2
        assert regime_for(480.0, 0.0001, 94.6) is LEO_1  # the dense 43° shell

    def test_deep_space_by_period(self):
        # GEO: perigee ~35786 km, period ~1436 min, in the 1300-1800 band
        assert regime_for(35786.0, 0.0002, 1436.0) is DEEP_SPACE

    def test_eccentricity_gate_falls_back(self):
        # ecc >= 0.25 is outside the SFS regimes -> conservative fallback (LEO 1)
        assert regime_for(450.0, 0.3, 95.0) is LEO_1
        assert regime_for(35786.0, 0.7, 1436.0) is LEO_1  # HEO

    def test_unclassified_high_perigee_falls_back(self):
        # perigee > 2000 but not in the deep-space period band (e.g. MEO/GPS,
        # ~720 min): not in the table -> conservative fallback, never under-screen
        assert regime_for(20000.0, 0.01, 718.0) is LEO_1


class TestIsScreenable:
    """is_screenable(): True iff the orbit gets a GENUINE SFS volume (screen it),
    False if it would hit the LEO-1 fallback (display only). This is the 9.2
    line — screen only where the industry-standard handbook actually applies."""

    def test_leo_is_screenable(self):
        assert is_screenable(200.0, 0.001, 91.0) is True       # LEO 1
        assert is_screenable(2000.0, 0.001, 127.0) is True     # LEO 4 upper edge

    def test_geo_band_is_screenable(self):
        # GEO gets the real deep-space volume, so it IS screened
        assert is_screenable(35786.0, 0.0002, 1436.0) is True

    def test_meo_gnss_not_screenable(self):
        # GPS/GNSS: perigee > 2000 km but ~718 min period (outside the band) ->
        # no genuine volume -> display, don't screen
        assert is_screenable(20000.0, 0.01, 718.0) is False

    def test_heo_not_screenable(self):
        # ecc >= 0.25 -> outside the SFS regimes -> not screened
        assert is_screenable(450.0, 0.3, 95.0) is False
        assert is_screenable(35786.0, 0.7, 1436.0) is False


class TestEllipsoidMembership:
    """ScreeningVolume.contains(): (r/R)^2 + (t/T)^2 + (n/N)^2 <= 1."""

    def test_on_axis_boundaries(self):
        # LEO_1 = (0.4, 44, 51)
        assert LEO_1.contains(0.4, 0.0, 0.0)      # on the radial boundary
        assert LEO_1.contains(0.0, 44.0, 0.0)     # on the in-track boundary
        assert LEO_1.contains(0.0, 0.0, 51.0)     # on the cross-track boundary
        assert not LEO_1.contains(0.41, 0.0, 0.0)
        assert not LEO_1.contains(0.0, 44.1, 0.0)
        assert not LEO_1.contains(0.0, 0.0, 51.1)

    def test_asymmetry_radial_is_tight(self):
        """The whole point of 7.2: a miss dominated by *radial* separation is
        excluded though a Euclidean threshold would accept it; the same
        magnitude as *in-track* is well inside."""
        # 5 km radial -> way outside (5/0.4 = 12.5); 5 km in-track -> well inside
        assert not LEO_1.contains(5.0, 0.0, 0.0)
        assert LEO_1.contains(0.0, 5.0, 0.0)

    def test_combined_interior(self):
        # half each semi-axis: 0.25 + 0.25 + 0.25 = 0.75 < 1
        assert LEO_1.contains(0.2, 22.0, 25.5)


class TestCircumscribingRadius:
    """circumscribing_radius() = largest semi-axis (the no-skip gross threshold,
    NOT the box corner)."""

    def test_largest_semi_axis(self):
        assert LEO_1.circumscribing_radius() == 51.0
        assert LEO_2.circumscribing_radius() == 25.0
        assert LEO_3.circumscribing_radius() == 12.0
        assert LEO_4.circumscribing_radius() == 2.0
        assert DEEP_SPACE.circumscribing_radius() == 10.0

    def test_is_not_box_corner(self):
        # sqrt(0.4^2 + 44^2 + 51^2) ~ 67.4 km would be the box corner; the
        # ellipsoid's bounding sphere is the max semi-axis (51), which is what
        # keeps the medium gross threshold tight while staying no-skip.
        import math
        corner = math.sqrt(0.4 ** 2 + 44 ** 2 + 51 ** 2)
        assert LEO_1.circumscribing_radius() < corner
        assert LEO_1.circumscribing_radius() == max(0.4, 44.0, 51.0)

    def test_gross_threshold_is_max_over_catalog(self):
        assert gross_threshold_km([LEO_4, LEO_1, LEO_3]) == 51.0
        assert gross_threshold_km([LEO_4, LEO_4]) == 2.0
        assert gross_threshold_km([]) == 0.0

    def test_every_interior_point_within_circumscribing_radius(self):
        """The no-skip guarantee, numerically: sample the ellipsoid surface and
        confirm no point exceeds the circumscribing radius (so a Euclidean
        medium pre-screen at that radius can't miss an in-volume event)."""
        import math
        v = ScreeningVolume("t", 0.4, 44.0, 51.0)
        R = v.circumscribing_radius()
        for k in range(64):
            th = math.pi * k / 32.0
            for m in range(64):
                ph = 2 * math.pi * m / 64.0
                r = v.r_km * math.sin(th) * math.cos(ph)
                t = v.t_km * math.sin(th) * math.sin(ph)
                n = v.n_km * math.cos(th)
                assert math.sqrt(r * r + t * t + n * n) <= R + 1e-9
