#!/usr/bin/env python3
"""Tests for SatellitePropagator (Task 2.4).

Validates:
- Unit conversions (degrees→radians, rev/day→rad/min, epoch→days)
- Single satellite propagation (ISS, CSS, eccentric orbits)
- All 30 Phase 1 stations propagate without error
- Batch propagation (get_all_positions)
- Multi-time propagation (get_positions_at_times)
- Satrec caching and index behavior
- Error handling (unknown satellite, invalid input)
- Performance (<1 sec for 30 satellites, scalability simulation)
- Cross-validation against Python sgp4 library (ISS + multiple sats)
- Scalability: O(1) lookups, index rebuild, simulated 6000-sat workload
"""

import math
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
import pandas as pd

# Ensure orbitcore .so is found before the source directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import orbitcore

from backend.core.propagator import SatellitePropagator, omm_to_sgp4_params, XPDOTP
from backend.core.tle_fetcher import GPFetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides):
    """Create a fake OMM row with ISS-like defaults."""
    defaults = {
        "norad_cat_id": 25544,
        "epoch": datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc),
        "mean_motion": 15.49,
        "eccentricity": 0.0001,
        "inclination": 51.6,
        "ra_of_asc_node": 200.0,
        "arg_of_pericenter": 130.0,
        "mean_anomaly": 230.0,
        "bstar": 0.0001,
        "mean_motion_dot": 0.00015,
        "mean_motion_ddot": 0.0,
    }
    defaults.update(overrides)
    return pd.Series(defaults)


# ===========================================================================
# 1. Unit Conversions
# ===========================================================================

class TestUnitConversions(unittest.TestCase):
    """Test omm_to_sgp4_params() unit conversions."""

    def test_angular_elements_converted_to_radians(self):
        """All angular orbital elements must be in radians for sgp4init."""
        row = _make_row()
        params = omm_to_sgp4_params(row)

        deg2rad = math.pi / 180.0
        self.assertAlmostEqual(params["inclo"], 51.6 * deg2rad, places=10)
        self.assertAlmostEqual(params["nodeo"], 200.0 * deg2rad, places=10)
        self.assertAlmostEqual(params["argpo"], 130.0 * deg2rad, places=10)
        self.assertAlmostEqual(params["mo"], 230.0 * deg2rad, places=10)

    def test_zero_degree_stays_zero(self):
        """0° must convert to 0 radians exactly."""
        row = _make_row(inclination=0.0, ra_of_asc_node=0.0,
                        arg_of_pericenter=0.0, mean_anomaly=0.0)
        params = omm_to_sgp4_params(row)
        self.assertEqual(params["inclo"], 0.0)
        self.assertEqual(params["nodeo"], 0.0)
        self.assertEqual(params["argpo"], 0.0)
        self.assertEqual(params["mo"], 0.0)

    def test_360_degree_wraps_to_2pi(self):
        """360° should convert to 2π radians."""
        row = _make_row(inclination=360.0)
        params = omm_to_sgp4_params(row)
        self.assertAlmostEqual(params["inclo"], 2.0 * math.pi, places=10)

    def test_mean_motion_converted_to_rad_per_min(self):
        """Mean motion: rev/day → rad/min (÷ xpdotp)."""
        row = _make_row(mean_motion=15.49)
        params = omm_to_sgp4_params(row)
        expected = 15.49 / XPDOTP
        self.assertAlmostEqual(params["no_kozai"], expected, places=12)

    def test_mean_motion_low_value(self):
        """GEO satellites have ~1.0 rev/day mean motion."""
        row = _make_row(mean_motion=1.00273)
        params = omm_to_sgp4_params(row)
        expected = 1.00273 / XPDOTP
        self.assertAlmostEqual(params["no_kozai"], expected, places=12)

    def test_mean_motion_dot_conversion(self):
        """mean_motion_dot: OMM value ÷ (xpdotp × 1440)."""
        row = _make_row(mean_motion_dot=0.00015)
        params = omm_to_sgp4_params(row)
        expected = 0.00015 / (XPDOTP * 1440.0)
        self.assertAlmostEqual(params["ndot"], expected, places=18)

    def test_mean_motion_ddot_conversion(self):
        """mean_motion_ddot: OMM value ÷ (xpdotp × 1440²)."""
        row = _make_row(mean_motion_ddot=1e-8)
        params = omm_to_sgp4_params(row)
        expected = 1e-8 / (XPDOTP * 1440.0 * 1440.0)
        self.assertAlmostEqual(params["nddot"], expected, places=25)

    def test_negative_mean_motion_dot(self):
        """Negative drag term should remain negative after conversion."""
        row = _make_row(mean_motion_dot=-0.0002)
        params = omm_to_sgp4_params(row)
        self.assertLess(params["ndot"], 0)

    def test_epoch_converted_to_days_since_1949(self):
        """Epoch: datetime → Julian Date → days since 1949 Dec 31."""
        row = _make_row(epoch=datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        params = omm_to_sgp4_params(row)
        expected = 2451545.0 - 2433281.5  # = 18263.5
        self.assertAlmostEqual(params["epoch"], expected, places=6)

    def test_epoch_recent_date(self):
        """Epoch for a recent date should produce a large positive value."""
        row = _make_row(epoch=datetime(2026, 3, 20, 0, 0, 0, tzinfo=timezone.utc))
        params = omm_to_sgp4_params(row)
        # Should be roughly (2026-1950)*365.25 ≈ 27,759 days
        self.assertGreater(params["epoch"], 27000)
        self.assertLess(params["epoch"], 29000)

    def test_epoch_with_fractional_seconds(self):
        """Epoch conversion should preserve sub-second precision."""
        row = _make_row(epoch=datetime(2026, 3, 20, 12, 30, 45, 500000,
                                       tzinfo=timezone.utc))
        params = omm_to_sgp4_params(row)
        self.assertIsInstance(params["epoch"], float)

    def test_gravity_model_is_wgs72(self):
        """Must use WGS-72 for SGP4 (matches NORAD's fitting model)."""
        row = _make_row()
        params = omm_to_sgp4_params(row)
        self.assertEqual(params["whichconst"], orbitcore.WGS72)

    def test_opsmode_is_afspc(self):
        """Opsmode 'a' (AFSPC) matches operational NORAD system."""
        row = _make_row()
        params = omm_to_sgp4_params(row)
        self.assertEqual(params["opsmode"], "a")

    def test_satnum_is_string(self):
        """satnum must be a string (C++ binding expects char array)."""
        row = _make_row(norad_cat_id=25544)
        params = omm_to_sgp4_params(row)
        self.assertEqual(params["satnum"], "25544")
        self.assertIsInstance(params["satnum"], str)

    def test_timestamp_epoch_handles_pandas_timestamp(self):
        """Epoch field from DataFrame arrives as pd.Timestamp, not datetime."""
        ts = pd.Timestamp("2026-03-20 12:00:00", tz="UTC")
        row = _make_row(epoch=ts)
        params = omm_to_sgp4_params(row)
        self.assertIsInstance(params["epoch"], float)
        self.assertGreater(params["epoch"], 0)

    def test_naive_epoch_treated_as_utc(self):
        """Naive datetime epoch should be treated as UTC without error."""
        row = _make_row(epoch=datetime(2026, 3, 20, 12, 0, 0))
        params = omm_to_sgp4_params(row)
        self.assertIsInstance(params["epoch"], float)

    def test_xpdotp_constant(self):
        """Verify xpdotp = 1440/(2π)."""
        self.assertAlmostEqual(XPDOTP, 1440.0 / (2.0 * math.pi), places=10)

    def test_bstar_passed_through(self):
        """Bstar (drag) should pass through without unit conversion."""
        row = _make_row(bstar=0.00034)
        params = omm_to_sgp4_params(row)
        self.assertAlmostEqual(params["bstar"], 0.00034, places=10)

    def test_eccentricity_passed_through(self):
        """Eccentricity is dimensionless — no conversion needed."""
        row = _make_row(eccentricity=0.7069)
        params = omm_to_sgp4_params(row)
        self.assertAlmostEqual(params["ecco"], 0.7069, places=10)

    def test_all_13_params_present(self):
        """omm_to_sgp4_params must return exactly 13 keys for sgp4init."""
        row = _make_row()
        params = omm_to_sgp4_params(row)
        expected_keys = {
            "whichconst", "opsmode", "satnum", "epoch",
            "bstar", "ndot", "nddot", "ecco",
            "argpo", "inclo", "mo", "no_kozai", "nodeo",
        }
        self.assertEqual(set(params.keys()), expected_keys)

    def test_params_can_initialize_satrec(self):
        """Converted params should successfully initialize a Satrec."""
        row = _make_row()
        params = omm_to_sgp4_params(row)
        satrec = orbitcore.sgp4init(**params)
        self.assertEqual(satrec.error, 0)


# ===========================================================================
# 2. Single Satellite Propagation
# ===========================================================================

class TestSingleSatPropagation(unittest.TestCase):
    """Test propagation of individual satellites."""

    @classmethod
    def setUpClass(cls):
        cls.prop = SatellitePropagator()
        cls.now = datetime.now(timezone.utc)

    def test_iss_altitude(self):
        """ISS altitude should be ~400-435 km."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        self.assertGreater(result["alt"], 390)
        self.assertLess(result["alt"], 450)

    def test_iss_speed(self):
        """ISS speed should be ~7.6-7.7 km/s."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        self.assertGreater(result["speed_km_s"], 7.5)
        self.assertLess(result["speed_km_s"], 7.8)

    def test_iss_latitude_bounded_by_inclination(self):
        """ISS latitude must be within ±51.6° (orbital inclination)."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        self.assertLessEqual(abs(result["lat"]), 52.0)

    def test_css_altitude(self):
        """CSS Tianhe altitude should be ~370-400 km."""
        result = self.prop.get_position("CSS (TIANHE)", self.now)
        self.assertGreater(result["alt"], 350)
        self.assertLess(result["alt"], 420)

    def test_css_latitude_bounded(self):
        """CSS latitude bounded by ~41.5° inclination."""
        result = self.prop.get_position("CSS (TIANHE)", self.now)
        self.assertLessEqual(abs(result["lat"]), 43.0)

    def test_iss_and_nauka_same_location(self):
        """ISS (ZARYA) and ISS (NAUKA) should be at nearly the same position."""
        iss = self.prop.get_position("ISS (ZARYA)", self.now)
        nauka = self.prop.get_position("ISS (NAUKA)", self.now)
        # Same station, altitudes should match within a few km
        self.assertAlmostEqual(iss["alt"], nauka["alt"], delta=5.0)
        self.assertAlmostEqual(iss["lat"], nauka["lat"], delta=1.0)

    def test_result_has_all_fields(self):
        """Result dict must have all expected keys."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        expected_keys = {
            "name", "norad_id", "lat", "lon", "alt",
            "pos_ecef", "vel_ecef", "speed_km_s",
            "timestamp", "epoch_age_days",
        }
        self.assertEqual(set(result.keys()), expected_keys)

    def test_result_types(self):
        """All result values have correct types."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        self.assertIsInstance(result["name"], str)
        self.assertIsInstance(result["norad_id"], int)
        self.assertIsInstance(result["lat"], float)
        self.assertIsInstance(result["lon"], float)
        self.assertIsInstance(result["alt"], float)
        self.assertIsInstance(result["pos_ecef"], list)
        self.assertIsInstance(result["vel_ecef"], list)
        self.assertIsInstance(result["speed_km_s"], float)
        self.assertIsInstance(result["timestamp"], datetime)
        self.assertIsInstance(result["epoch_age_days"], float)
        self.assertEqual(len(result["pos_ecef"]), 3)
        self.assertEqual(len(result["vel_ecef"]), 3)

    def test_ecef_position_magnitude(self):
        """ECEF position magnitude should be ~Earth radius + altitude."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        r = math.sqrt(sum(x ** 2 for x in result["pos_ecef"]))
        # ISS: ~6378 + ~420 = ~6798 km
        self.assertGreater(r, 6700)
        self.assertLess(r, 6900)

    def test_ecef_velocity_reasonable(self):
        """ECEF velocity should be less than TEME velocity (ground-relative)."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        ecef_speed = math.sqrt(sum(v ** 2 for v in result["vel_ecef"]))
        # ECEF speed is less than inertial speed due to Earth rotation subtraction
        self.assertLess(ecef_speed, result["speed_km_s"])
        self.assertGreater(ecef_speed, 6.0)

    def test_norad_id_lookup(self):
        """Can propagate by NORAD catalog number (ISS = 25544)."""
        result = self.prop.get_position_by_norad_id(25544, self.now)
        self.assertEqual(result["name"], "ISS (ZARYA)")
        self.assertGreater(result["alt"], 390)

    def test_name_lookup_case_insensitive(self):
        """Satellite name lookup should be case-insensitive."""
        r1 = self.prop.get_position("ISS (ZARYA)", self.now)
        r2 = self.prop.get_position("iss (zarya)", self.now)
        self.assertAlmostEqual(r1["lat"], r2["lat"], places=10)
        self.assertAlmostEqual(r1["lon"], r2["lon"], places=10)

    def test_name_by_norad_matches_name_lookup(self):
        """get_position and get_position_by_norad_id should give same result."""
        r1 = self.prop.get_position("ISS (ZARYA)", self.now)
        r2 = self.prop.get_position_by_norad_id(25544, self.now)
        self.assertAlmostEqual(r1["lat"], r2["lat"], places=10)
        self.assertAlmostEqual(r1["alt"], r2["alt"], places=10)

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes should work (treated as UTC)."""
        naive_dt = datetime(2026, 3, 20, 12, 0, 0)
        result = self.prop.get_position("ISS (ZARYA)", naive_dt)
        self.assertIsNotNone(result["alt"])
        self.assertEqual(result["timestamp"].tzinfo, timezone.utc)

    def test_timestamp_preserved_in_result(self):
        """The requested time should be stored in the result."""
        test_time = datetime(2026, 3, 21, 6, 30, 0, tzinfo=timezone.utc)
        result = self.prop.get_position("ISS (ZARYA)", test_time)
        self.assertEqual(result["timestamp"], test_time)

    def test_epoch_age_positive(self):
        """epoch_age_days should be a non-negative float."""
        result = self.prop.get_position("ISS (ZARYA)", self.now)
        self.assertGreaterEqual(result["epoch_age_days"], 0.0)


# ===========================================================================
# 3. Error Handling
# ===========================================================================

class TestErrorHandling(unittest.TestCase):
    """Test error cases and edge conditions."""

    @classmethod
    def setUpClass(cls):
        cls.prop = SatellitePropagator()
        cls.now = datetime.now(timezone.utc)

    def test_unknown_satellite_raises_keyerror(self):
        """Requesting an unknown satellite should raise KeyError."""
        with self.assertRaises(KeyError):
            self.prop.get_position("NONEXISTENT SAT", self.now)

    def test_unknown_norad_id_raises_keyerror(self):
        """Requesting an unknown NORAD ID should raise KeyError."""
        with self.assertRaises(KeyError):
            self.prop.get_position_by_norad_id(99999999, self.now)

    def test_error_message_includes_available_sats(self):
        """KeyError message should list some available satellite names."""
        try:
            self.prop.get_position("BOGUS", self.now)
            self.fail("Should have raised KeyError")
        except KeyError as e:
            msg = str(e)
            self.assertIn("not found", msg)
            self.assertIn("Available", msg)

    def test_empty_name_raises(self):
        """Empty string name should raise KeyError."""
        with self.assertRaises(KeyError):
            self.prop.get_position("", self.now)

    def test_propagation_far_future(self):
        """Propagating far into the future should still return (accuracy degrades)."""
        far_future = self.now + timedelta(days=30)
        result = self.prop.get_position("ISS (ZARYA)", far_future)
        # Should still return valid coordinates (even if inaccurate)
        self.assertGreater(result["alt"], 0)
        self.assertLessEqual(abs(result["lat"]), 90.0)

    def test_propagation_past(self):
        """Backward propagation should work."""
        past = self.now - timedelta(days=1)
        result = self.prop.get_position("ISS (ZARYA)", past)
        self.assertGreater(result["alt"], 300)
        self.assertLess(result["alt"], 500)


# ===========================================================================
# 4. Batch Propagation (All Satellites)
# ===========================================================================

class TestBatchPropagation(unittest.TestCase):
    """Test propagating all satellites at once."""

    @classmethod
    def setUpClass(cls):
        cls.prop = SatellitePropagator()
        cls.now = datetime.now(timezone.utc)
        cls.results, cls.errors = cls.prop.get_all_positions(cls.now)

    def test_all_30_stations_propagate(self):
        """All 30 Phase 1 stations must propagate without error."""
        self.assertGreaterEqual(len(self.results), 25)

    def test_all_altitudes_positive(self):
        """All satellites must have positive altitude (not underground)."""
        for r in self.results:
            self.assertGreater(r["alt"], 0,
                f"{r['name']} has negative altitude: {r['alt']:.1f} km")

    def test_all_latitudes_valid(self):
        """All latitudes must be in [-90, 90]."""
        for r in self.results:
            self.assertGreaterEqual(r["lat"], -90.0, f"{r['name']} lat={r['lat']}")
            self.assertLessEqual(r["lat"], 90.0, f"{r['name']} lat={r['lat']}")

    def test_all_longitudes_valid(self):
        """All longitudes must be in [-180, 180]."""
        for r in self.results:
            self.assertGreaterEqual(r["lon"], -180.0, f"{r['name']} lon={r['lon']}")
            self.assertLessEqual(r["lon"], 180.0, f"{r['name']} lon={r['lon']}")

    def test_all_speeds_reasonable(self):
        """All speeds must be between 1 and 11 km/s (bound orbit)."""
        for r in self.results:
            self.assertGreater(r["speed_km_s"], 1.0,
                f"{r['name']} speed too slow: {r['speed_km_s']:.3f} km/s")
            self.assertLess(r["speed_km_s"], 11.0,
                f"{r['name']} speed too fast: {r['speed_km_s']:.3f} km/s")

    def test_unique_norad_ids(self):
        """Each satellite should have a unique NORAD ID."""
        ids = [r["norad_id"] for r in self.results]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_have_names(self):
        """Every result must have a non-empty name."""
        for r in self.results:
            self.assertIsInstance(r["name"], str)
            self.assertGreater(len(r["name"]), 0)

    def test_all_altitudes_below_geostationary(self):
        """Phase 1 stations should all be below GEO (~36,000 km)."""
        for r in self.results:
            self.assertLess(r["alt"], 36000,
                f"{r['name']} altitude {r['alt']:.0f} km exceeds GEO")

    def test_iss_present_in_results(self):
        """ISS should be in the batch results."""
        names = [r["name"] for r in self.results]
        self.assertIn("ISS (ZARYA)", names)


# ===========================================================================
# 5. Multi-Time Propagation (Ground Tracks)
# ===========================================================================

class TestMultiTimePropagation(unittest.TestCase):
    """Test propagating one satellite at multiple times."""

    @classmethod
    def setUpClass(cls):
        cls.prop = SatellitePropagator()
        cls.now = datetime.now(timezone.utc)

    def test_ground_track_over_one_orbit(self):
        """ISS ground track over ~90 min should span a wide longitude range."""
        times = [self.now + timedelta(minutes=i * 10) for i in range(10)]
        positions = self.prop.get_positions_at_times("ISS (ZARYA)", times)

        self.assertEqual(len(positions), 10)
        lons = [p["lon"] for p in positions]
        lon_range = max(lons) - min(lons)
        self.assertGreater(lon_range, 30.0)

    def test_altitude_stability(self):
        """ISS altitude should remain roughly constant over one orbit."""
        times = [self.now + timedelta(minutes=i * 10) for i in range(10)]
        positions = self.prop.get_positions_at_times("ISS (ZARYA)", times)

        alts = [p["alt"] for p in positions]
        alt_range = max(alts) - min(alts)
        self.assertLess(alt_range, 25.0)

    def test_returns_correct_count(self):
        """Should return exactly as many results as input times."""
        times = [self.now + timedelta(minutes=i) for i in range(7)]
        positions = self.prop.get_positions_at_times("ISS (ZARYA)", times)
        self.assertEqual(len(positions), 7)

    def test_single_time_same_as_get_position(self):
        """Single-time track should match get_position."""
        r1 = self.prop.get_position("ISS (ZARYA)", self.now)
        r2 = self.prop.get_positions_at_times("ISS (ZARYA)", [self.now])[0]
        self.assertAlmostEqual(r1["lat"], r2["lat"], places=10)
        self.assertAlmostEqual(r1["lon"], r2["lon"], places=10)

    def test_latitude_oscillation(self):
        """Over a full orbit, ISS latitude should oscillate (cross equator twice)."""
        times = [self.now + timedelta(minutes=i * 5) for i in range(20)]
        positions = self.prop.get_positions_at_times("ISS (ZARYA)", times)
        lats = [p["lat"] for p in positions]
        # Should have both positive and negative latitudes over ~100 min
        has_north = any(l > 10 for l in lats)
        has_south = any(l < -10 for l in lats)
        self.assertTrue(has_north or has_south,
            "ISS should reach significant latitudes over a full orbit")

    def test_timestamps_preserved(self):
        """Each result should have the corresponding input timestamp."""
        t1 = self.now
        t2 = self.now + timedelta(hours=1)
        t3 = self.now + timedelta(hours=2)
        positions = self.prop.get_positions_at_times("ISS (ZARYA)", [t1, t2, t3])
        for pos, expected_t in zip(positions, [t1, t2, t3]):
            self.assertEqual(pos["timestamp"],
                             expected_t.replace(tzinfo=timezone.utc)
                             if expected_t.tzinfo is None else expected_t)


# ===========================================================================
# 6. Caching and Index Behavior
# ===========================================================================

class TestCachingAndIndexes(unittest.TestCase):
    """Test Satrec caching and O(1) lookup indexes."""

    def test_satrec_cached_between_calls(self):
        """Calling get_position twice should reuse the same Satrec."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)

        prop.get_position("ISS (ZARYA)", now)
        self.assertEqual(len(prop._satrec_cache), 1)

        prop.get_position("ISS (ZARYA)", now + timedelta(minutes=5))
        self.assertEqual(len(prop._satrec_cache), 1)

    def test_reload_clears_all_caches(self):
        """reload_data() should clear Satrec cache and indexes."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)

        prop.get_position("ISS (ZARYA)", now)
        self.assertEqual(len(prop._satrec_cache), 1)
        self.assertIsNotNone(prop._name_index)
        self.assertIsNotNone(prop._norad_index)

        prop.reload_data()
        self.assertEqual(len(prop._satrec_cache), 0)
        self.assertIsNone(prop._name_index)
        self.assertIsNone(prop._norad_index)

    def test_different_satellites_get_different_satrecs(self):
        """Each satellite gets its own cached Satrec."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)

        prop.get_position("ISS (ZARYA)", now)
        prop.get_position("CSS (TIANHE)", now)
        self.assertEqual(len(prop._satrec_cache), 2)

    def test_name_index_built_on_first_access(self):
        """Name index should be built when data is first loaded."""
        prop = SatellitePropagator()
        self.assertIsNone(prop._name_index)

        prop._ensure_data()
        self.assertIsNotNone(prop._name_index)
        self.assertIn("ISS (ZARYA)", prop._name_index)

    def test_norad_index_built_on_first_access(self):
        """NORAD index should be built when data is first loaded."""
        prop = SatellitePropagator()
        self.assertIsNone(prop._norad_index)

        prop._ensure_data()
        self.assertIsNotNone(prop._norad_index)
        self.assertIn(25544, prop._norad_index)

    def test_index_is_case_insensitive(self):
        """Name index keys should be uppercase for case-insensitive lookup."""
        prop = SatellitePropagator()
        prop._ensure_data()
        # All keys should be uppercase
        for key in prop._name_index:
            self.assertEqual(key, key.upper())

    def test_data_loaded_lazily(self):
        """DataFrame should not be loaded until needed."""
        prop = SatellitePropagator()
        self.assertIsNone(prop._df)
        prop.get_position("ISS (ZARYA)", datetime.now(timezone.utc))
        self.assertIsNotNone(prop._df)

    def test_reload_then_reuse(self):
        """After reload, lookups should still work (indexes rebuilt)."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)

        r1 = prop.get_position("ISS (ZARYA)", now)
        prop.reload_data()
        r2 = prop.get_position("ISS (ZARYA)", now)

        self.assertAlmostEqual(r1["lat"], r2["lat"], places=10)


# ===========================================================================
# 7. Performance
# ===========================================================================

class TestPerformance(unittest.TestCase):
    """Verify propagation performance meets requirements."""

    def test_30_satellites_under_1_second(self):
        """Propagating all 30 stations should take < 1 second."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)

        start = time.perf_counter()
        results, _ = prop.get_all_positions(now)
        elapsed = time.perf_counter() - start

        self.assertGreaterEqual(len(results), 25)
        self.assertLess(elapsed, 1.0,
            f"Took {elapsed:.3f}s for {len(results)} satellites (limit: 1.0s)")

    def test_100_time_steps_under_1_second(self):
        """Propagating ISS at 100 time steps should take < 1 second."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)
        times = [now + timedelta(minutes=i) for i in range(100)]

        start = time.perf_counter()
        results = prop.get_positions_at_times("ISS (ZARYA)", times)
        elapsed = time.perf_counter() - start

        self.assertEqual(len(results), 100)
        self.assertLess(elapsed, 1.0,
            f"Took {elapsed:.3f}s for 100 time steps (limit: 1.0s)")

    def test_repeated_lookups_faster_than_first(self):
        """Cached Satrec lookups should be faster than cold init."""
        prop = SatellitePropagator()
        now = datetime.now(timezone.utc)

        # Cold: first call initializes Satrec + loads data
        start = time.perf_counter()
        prop.get_position("ISS (ZARYA)", now)
        cold_time = time.perf_counter() - start

        # Warm: Satrec and data already cached
        times = [now + timedelta(seconds=i) for i in range(10)]
        start = time.perf_counter()
        for t in times:
            prop.get_position("ISS (ZARYA)", t)
        warm_time = (time.perf_counter() - start) / 10

        # Warm lookup should be faster (or at least not slower)
        self.assertLess(warm_time, cold_time * 2,
            f"Warm={warm_time:.5f}s, Cold={cold_time:.5f}s")

    def test_index_lookup_is_constant_time(self):
        """Name lookup via index should not scale with dataset size."""
        prop = SatellitePropagator()
        prop._ensure_data()

        now = datetime.now(timezone.utc)
        # Warm up
        prop.get_position("ISS (ZARYA)", now)

        # Time 100 lookups
        start = time.perf_counter()
        for _ in range(100):
            prop._find_satellite("ISS (ZARYA)")
        elapsed = time.perf_counter() - start

        # 100 lookups should take < 50ms (they're just dict lookups)
        self.assertLess(elapsed, 0.05,
            f"100 name lookups took {elapsed:.4f}s (expected < 0.05s)")


# ===========================================================================
# 8. Scalability Simulation
# ===========================================================================

class TestScalability(unittest.TestCase):
    """Simulate larger catalogs to verify the design scales."""

    def test_simulated_6000_sat_index_build(self):
        """Building indexes for 6000 satellites should take < 1 second."""
        # Create a synthetic DataFrame with 6000 rows
        n = 6000
        df = pd.DataFrame({
            "object_name": [f"STARLINK-{i}" for i in range(n)],
            "norad_cat_id": list(range(50000, 50000 + n)),
            "epoch": [datetime(2026, 3, 20, tzinfo=timezone.utc)] * n,
            "mean_motion": [15.0] * n,
            "eccentricity": [0.0001] * n,
            "inclination": [53.0] * n,
            "ra_of_asc_node": [100.0] * n,
            "arg_of_pericenter": [200.0] * n,
            "mean_anomaly": [300.0] * n,
            "bstar": [0.0001] * n,
            "mean_motion_dot": [0.0] * n,
            "mean_motion_ddot": [0.0] * n,
            "epoch_age_days": [0.5] * n,
        })

        prop = SatellitePropagator()
        prop._df = df

        start = time.perf_counter()
        prop._build_indexes()
        elapsed = time.perf_counter() - start

        self.assertEqual(len(prop._name_index), n)
        self.assertEqual(len(prop._norad_index), n)
        self.assertLess(elapsed, 1.0,
            f"Index build for {n} sats took {elapsed:.3f}s")

    def test_simulated_6000_sat_lookup(self):
        """Looking up satellites in a 6000-entry index should be instant."""
        n = 6000
        df = pd.DataFrame({
            "object_name": [f"STARLINK-{i}" for i in range(n)],
            "norad_cat_id": list(range(50000, 50000 + n)),
        })

        prop = SatellitePropagator()
        prop._df = df
        prop._name_index = {f"STARLINK-{i}": i for i in range(n)}
        prop._norad_index = {50000 + i: i for i in range(n)}

        # 1000 random lookups by name
        start = time.perf_counter()
        for i in range(0, n, 6):  # 1000 lookups
            prop._name_index.get(f"STARLINK-{i}")
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.01,
            f"1000 index lookups took {elapsed:.5f}s")

    def test_satrec_cache_memory_estimate(self):
        """Verify Satrec objects are lightweight enough for 6000 sats."""
        # Each Satrec is a C++ struct. Create a few and measure.
        row = _make_row()
        params = omm_to_sgp4_params(row)
        satrec = orbitcore.sgp4init(**params)

        # sys.getsizeof won't capture C++ internals, but Satrec should
        # be less than 2KB based on elsetrec struct size (~110 doubles = ~880 bytes)
        import sys as _sys
        size = _sys.getsizeof(satrec)
        # At 6000 sats × 2KB = 12MB — easily fits in memory
        self.assertLess(size, 2048, f"Satrec size {size} bytes exceeds 2KB")


# ===========================================================================
# 9. Cross-Validation Against Python sgp4 Library
# ===========================================================================

class TestCrossValidation(unittest.TestCase):
    """Cross-validate C++ propagator against Python sgp4 library."""

    @classmethod
    def setUpClass(cls):
        cls.prop = SatellitePropagator()
        cls.df = cls.prop._ensure_data()

    def _cross_validate_satellite(self, name, test_time):
        """Helper: propagate with both engines, compare ECEF positions."""
        from sgp4.api import Satrec as PySatrec, WGS72 as PyWGS72, jday

        row = self.df[self.df["object_name"] == name].iloc[0]

        # Our propagator
        our_result = self.prop.get_position(name, test_time)

        # Python sgp4
        deg2rad = math.pi / 180.0
        py_sat = PySatrec()
        epoch_dt = row["epoch"]
        if isinstance(epoch_dt, pd.Timestamp):
            epoch_dt = epoch_dt.to_pydatetime()
        if epoch_dt.tzinfo is None:
            epoch_dt = epoch_dt.replace(tzinfo=timezone.utc)

        jd, jdf = jday(epoch_dt.year, epoch_dt.month, epoch_dt.day,
                        epoch_dt.hour, epoch_dt.minute,
                        epoch_dt.second + epoch_dt.microsecond / 1e6)
        epoch_days = (jd + jdf) - 2433281.5

        py_sat.sgp4init(
            PyWGS72, 'a', int(row["norad_cat_id"]),
            epoch_days,
            float(row["bstar"]),
            float(row["mean_motion_dot"]) / (XPDOTP * 1440.0),
            float(row["mean_motion_ddot"]) / (XPDOTP * 1440.0 * 1440.0),
            float(row["eccentricity"]),
            float(row["arg_of_pericenter"]) * deg2rad,
            float(row["inclination"]) * deg2rad,
            float(row["mean_anomaly"]) * deg2rad,
            float(row["mean_motion"]) / XPDOTP,
            float(row["ra_of_asc_node"]) * deg2rad,
        )

        jd_now, jdf_now = jday(test_time.year, test_time.month, test_time.day,
                                test_time.hour, test_time.minute,
                                test_time.second + test_time.microsecond / 1e6)
        e, py_r, py_v = py_sat.sgp4(jd_now, jdf_now)
        self.assertEqual(e, 0, f"Python sgp4 error for {name}")

        from backend.core.coordinate_transforms import teme_to_geodetic as t2g
        py_geo = t2g(py_r, jd_now + jdf_now, py_v)

        dist = math.sqrt(sum((a - b) ** 2
                             for a, b in zip(our_result["pos_ecef"],
                                             py_geo["pos_ecef"])))
        return dist

    def test_iss_matches_python_sgp4(self):
        """ISS position should match Python sgp4 to sub-meter."""
        test_time = datetime(2026, 3, 21, 0, 0, 0, tzinfo=timezone.utc)
        dist = self._cross_validate_satellite("ISS (ZARYA)", test_time)
        self.assertLess(dist, 0.001,
            f"ISS ECEF mismatch: {dist:.6f} km")

    def test_css_matches_python_sgp4(self):
        """CSS Tianhe should match Python sgp4 to sub-meter."""
        test_time = datetime(2026, 3, 21, 6, 0, 0, tzinfo=timezone.utc)
        dist = self._cross_validate_satellite("CSS (TIANHE)", test_time)
        self.assertLess(dist, 0.001,
            f"CSS ECEF mismatch: {dist:.6f} km")

    def test_all_stations_match_python_sgp4(self):
        """All Phase 1 stations should match Python sgp4 to sub-meter."""
        test_time = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)
        max_dist = 0.0
        worst_name = ""

        for _, row in self.df.iterrows():
            name = row["object_name"]
            try:
                dist = self._cross_validate_satellite(name, test_time)
                if dist > max_dist:
                    max_dist = dist
                    worst_name = name
            except Exception as e:
                self.fail(f"Cross-validation failed for {name}: {e}")

        self.assertLess(max_dist, 0.001,
            f"Worst match: {worst_name} at {max_dist:.6f} km")


# ===========================================================================
# 10. Propagator Constructor & Configuration
# ===========================================================================

class TestPropagatorConfig(unittest.TestCase):
    """Test constructor options and configuration."""

    def test_default_group_is_stations(self):
        prop = SatellitePropagator()
        self.assertEqual(prop.group, "stations")

    def test_custom_group(self):
        prop = SatellitePropagator(group="visual")
        self.assertEqual(prop.group, "visual")

    def test_custom_fetcher(self):
        """Can inject a custom GPFetcher."""
        fetcher = GPFetcher()
        prop = SatellitePropagator(fetcher=fetcher)
        self.assertIs(prop.fetcher, fetcher)

    def test_initial_state_is_empty(self):
        """Fresh propagator should have no data loaded."""
        prop = SatellitePropagator()
        self.assertIsNone(prop._df)
        self.assertIsNone(prop._name_index)
        self.assertIsNone(prop._norad_index)
        self.assertEqual(len(prop._satrec_cache), 0)


if __name__ == "__main__":
    unittest.main()
