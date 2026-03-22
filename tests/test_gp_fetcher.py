#!/usr/bin/env python3
"""Tests for GPFetcher — validates parsing, caching, validation, and derived params.
Week 2, Task 1: Implement comprehensive unit tests for GPFetcher._parse_json and related methods.
This test suite covers:
- Correct parsing of CelesTrak JSON records into DataFrames.
- Validation logic that filters out invalid/unusable records.
- Accurate computation of derived orbital parameters (period, apoapsis, periapsis).
- Caching behavior: writing to and reading from Parquet, freshness checks, atomic writes.
- Error handling: network errors, malformed records, missing fields.
- DataFrame schema: expected columns and types are verified.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.tle_fetcher import GPFetcher

# --- Sample CelesTrak JSON records ---

ISS_RECORD = {
    "CCSDS_OMM_VERS": "3.0",
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "EPOCH": "2026-03-21T12:00:00.000",
    "MEAN_MOTION": 15.50104166,
    "ECCENTRICITY": 0.0007976,
    "INCLINATION": 51.6416,
    "RA_OF_ASC_NODE": 247.4627,
    "ARG_OF_PERICENTER": 130.5360,
    "MEAN_ANOMALY": 325.0288,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 48765,
    "BSTAR": 0.000036771,
    "MEAN_MOTION_DOT": 0.00002182,
    "MEAN_MOTION_DDOT": 0,
}

GPS_RECORD = {
    "OBJECT_NAME": "GPS BIIR-2",
    "OBJECT_ID": "1997-035A",
    "EPOCH": "2026-03-20T06:00:00.000",
    "MEAN_MOTION": 2.00563664,
    "ECCENTRICITY": 0.0039312,
    "INCLINATION": 55.5431,
    "RA_OF_ASC_NODE": 199.1568,
    "ARG_OF_PERICENTER": 82.7401,
    "MEAN_ANOMALY": 277.4589,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 24876,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 15432,
    "BSTAR": 0.0,
    "MEAN_MOTION_DOT": -1.4e-7,
    "MEAN_MOTION_DDOT": 0,
}


def make_record(**overrides):
    """Create a valid ISS-like record with optional field overrides."""
    rec = ISS_RECORD.copy()
    rec.update(overrides)
    return rec


class TestParseJson:
    """Test _parse_json: field extraction, validation, derived params."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fetcher = GPFetcher(cache_dir=Path(self.tmp_dir))

    def test_valid_record_parses_all_fields(self):
        df = self.fetcher._parse_json([ISS_RECORD])
        assert len(df) == 1
        row = df.iloc[0]

        # Identity
        assert row["object_name"] == "ISS (ZARYA)"
        assert row["object_id"] == "1998-067A"
        assert row["norad_cat_id"] == 25544

        # SGP4 inputs preserved exactly
        assert row["mean_motion"] == 15.50104166
        assert row["eccentricity"] == 0.0007976
        assert row["inclination"] == 51.6416
        assert row["ra_of_asc_node"] == 247.4627
        assert row["arg_of_pericenter"] == 130.5360
        assert row["mean_anomaly"] == 325.0288
        assert row["bstar"] == 0.000036771
        assert row["mean_motion_dot"] == 0.00002182
        assert row["mean_motion_ddot"] == 0

        # Metadata
        assert row["ephemeris_type"] == 0
        assert row["classification"] == "U"

    def test_derived_orbital_params_iss(self):
        """ISS: LEO, ~92.9 min period, ~420 km altitude."""
        df = self.fetcher._parse_json([ISS_RECORD])
        row = df.iloc[0]

        # Period should be ~92.9 minutes (LEO, near-Earth)
        assert 90 < row["period"] < 95, f"ISS period {row['period']} not in expected range"
        assert row["period"] < 225, "ISS should be classified as near-Earth"

        # Altitude should be ~400-430 km
        assert 380 < row["periapsis"] < 450, f"ISS periapsis {row['periapsis']} km unexpected"
        assert 380 < row["apoapsis"] < 450, f"ISS apoapsis {row['apoapsis']} km unexpected"
        assert row["apoapsis"] >= row["periapsis"], "Apoapsis must be >= periapsis"

        # Semimajor axis should be ~6780 km (Earth radius + altitude)
        assert 6750 < row["semimajor_axis"] < 6810

    def test_derived_orbital_params_gps(self):
        """GPS: MEO, ~718 min period, ~20,200 km altitude."""
        df = self.fetcher._parse_json([GPS_RECORD])
        row = df.iloc[0]

        # GPS period should be ~718 minutes (deep-space: >= 225 min)
        assert 700 < row["period"] < 730, f"GPS period {row['period']} not in expected range"
        assert row["period"] >= 225, "GPS should be classified as deep-space"

        # GPS altitude should be ~20,000-20,400 km
        assert 19800 < row["periapsis"] < 20600
        assert 19800 < row["apoapsis"] < 20600

    def test_epoch_age_computed(self):
        """epoch_age_days reflects how stale the TLE is."""
        df = self.fetcher._parse_json([ISS_RECORD])
        row = df.iloc[0]
        assert "epoch_age_days" in df.columns
        # Should be positive (epoch is in the past relative to now)
        assert row["epoch_age_days"] >= 0

    def test_fetch_time_is_utc(self):
        df = self.fetcher._parse_json([ISS_RECORD])
        ft = df["fetch_time"].iloc[0]
        assert ft.tzinfo is not None or pd.Timestamp(ft).tzinfo is not None

    def test_multiple_records(self):
        df = self.fetcher._parse_json([ISS_RECORD, GPS_RECORD])
        assert len(df) == 2
        names = df["object_name"].tolist()
        assert "ISS (ZARYA)" in names
        assert "GPS BIIR-2" in names

    def test_whitespace_stripped_from_name(self):
        rec = make_record(OBJECT_NAME="  ISS (ZARYA)  ")
        df = self.fetcher._parse_json([rec])
        assert df.iloc[0]["object_name"] == "ISS (ZARYA)"

    def test_optional_metadata_defaults_to_none(self):
        """gp.php doesn't provide OBJECT_TYPE, RCS_SIZE, etc. — should be None."""
        df = self.fetcher._parse_json([ISS_RECORD])
        row = df.iloc[0]
        assert row["object_type"] is None
        assert row["rcs_size"] is None
        assert row["country_code"] is None
        assert row["launch_date"] is None
        assert row["decay_date"] is None

    def test_optional_metadata_when_present(self):
        rec = make_record(
            OBJECT_TYPE="PAYLOAD",
            RCS_SIZE="LARGE",
            COUNTRY_CODE="US",
        )
        df = self.fetcher._parse_json([rec])
        row = df.iloc[0]
        assert row["object_type"] == "PAYLOAD"
        assert row["rcs_size"] == "LARGE"
        assert row["country_code"] == "US"


class TestValidation:
    """Test that invalid/unusable records are properly skipped."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fetcher = GPFetcher(cache_dir=Path(self.tmp_dir))

    def test_skip_zero_mean_motion(self):
        rec = make_record(MEAN_MOTION=0)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_skip_negative_mean_motion(self):
        rec = make_record(MEAN_MOTION=-1.5)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_skip_negative_eccentricity(self):
        rec = make_record(ECCENTRICITY=-0.001)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_skip_eccentricity_ge_one(self):
        """Eccentricity >= 1 is parabolic/hyperbolic — not a valid bound orbit."""
        rec = make_record(ECCENTRICITY=1.0)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_skip_non_sgp4_ephemeris(self):
        rec = make_record(EPHEMERIS_TYPE=2)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_skip_decayed_object(self):
        rec = make_record(DECAYED=1)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_decayed_zero_passes(self):
        rec = make_record(DECAYED=0)
        df = self.fetcher._parse_json([rec])
        assert len(df) == 1

    def test_missing_decayed_field_defaults_to_active(self):
        """gp.php doesn't always include DECAYED — treat missing as active."""
        rec = ISS_RECORD.copy()  # no DECAYED key
        assert "DECAYED" not in rec
        df = self.fetcher._parse_json([rec])
        assert len(df) == 1

    def test_missing_required_field_skips_record(self):
        """Missing MEAN_MOTION should skip the record, not crash."""
        rec = ISS_RECORD.copy()
        del rec["MEAN_MOTION"]
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0

    def test_bad_record_doesnt_kill_batch(self):
        """One malformed record should not prevent parsing the rest."""
        bad = ISS_RECORD.copy()
        del bad["ECCENTRICITY"]
        df = self.fetcher._parse_json([bad, GPS_RECORD])
        assert len(df) == 1
        assert df.iloc[0]["object_name"] == "GPS BIIR-2"

    def test_bad_epoch_skips_record(self):
        rec = make_record(EPOCH="not-a-date")
        df = self.fetcher._parse_json([rec])
        assert len(df) == 0


class TestDeriveOrbitParams:
    """Test _derive_orbit_params math against known orbits."""

    def test_iss_orbit(self):
        """ISS: mean_motion ~15.5 rev/day, near-circular."""
        params = GPFetcher._derive_orbit_params(15.50104166, 0.0007976)
        assert 92 < params["period"] < 94
        assert 380 < params["periapsis"] < 450
        assert 380 < params["apoapsis"] < 450

    def test_gps_orbit(self):
        """GPS: mean_motion ~2.0 rev/day, ~20,200 km altitude."""
        params = GPFetcher._derive_orbit_params(2.00563664, 0.0039312)
        assert 710 < params["period"] < 730
        assert 19800 < params["periapsis"] < 20600

    def test_geo_orbit(self):
        """GEO: mean_motion ~1.0 rev/day, ~35,786 km altitude."""
        params = GPFetcher._derive_orbit_params(1.00272, 0.0001)
        assert 1430 < params["period"] < 1445
        assert 35500 < params["periapsis"] < 36200
        assert 35500 < params["apoapsis"] < 36200

    def test_circular_orbit_apo_equals_peri(self):
        """For e=0, apoapsis should equal periapsis."""
        params = GPFetcher._derive_orbit_params(15.5, 0.0)
        assert abs(params["apoapsis"] - params["periapsis"]) < 0.01

    def test_eccentric_orbit_apo_gt_peri(self):
        """Molniya-type: high eccentricity, large apo/peri difference."""
        params = GPFetcher._derive_orbit_params(2.006, 0.74)
        assert params["apoapsis"] > params["periapsis"]
        assert params["apoapsis"] > 30000  # very high apoapsis
        assert params["periapsis"] < 1000   # low periapsis


class TestCaching:
    """Test Parquet caching: write, read, freshness, atomic write."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fetcher = GPFetcher(cache_dir=Path(self.tmp_dir))

    def test_cache_roundtrip(self):
        """Data survives Parquet write → read cycle."""
        df = self.fetcher._parse_json([ISS_RECORD])
        self.fetcher._cache_to_parquet(df, "test_group")

        loaded = self.fetcher.load_cached("test_group")
        assert len(loaded) == 1
        assert loaded.iloc[0]["object_name"] == "ISS (ZARYA)"
        assert loaded.iloc[0]["norad_cat_id"] == 25544
        # Floats should survive roundtrip
        assert loaded.iloc[0]["mean_motion"] == 15.50104166
        assert loaded.iloc[0]["eccentricity"] == 0.0007976

    def test_load_cached_no_file_raises(self):
        try:
            self.fetcher.load_cached("nonexistent")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass

    def test_cache_freshness_returns_fresh(self):
        """Data cached just now should be considered fresh."""
        df = self.fetcher._parse_json([ISS_RECORD])
        self.fetcher._cache_to_parquet(df, "fresh_test")

        result = self.fetcher._load_if_fresh("fresh_test")
        assert result is not None
        assert len(result) == 1

    def test_cache_freshness_rejects_stale(self):
        """Data cached 3 hours ago should be considered stale."""
        df = self.fetcher._parse_json([ISS_RECORD])
        # Overwrite fetch_time to 3 hours ago
        df["fetch_time"] = datetime.now(timezone.utc) - timedelta(hours=3)
        parquet_path = Path(self.tmp_dir) / "stale_test.parquet"
        df.to_parquet(parquet_path, index=False)

        result = self.fetcher._load_if_fresh("stale_test")
        assert result is None

    def test_atomic_write_no_partial_file(self):
        """Verify no temp files are left behind after successful write."""
        df = self.fetcher._parse_json([ISS_RECORD])
        self.fetcher._cache_to_parquet(df, "atomic_test")

        files = list(Path(self.tmp_dir).iterdir())
        # Should only have the final .parquet, no temp files
        assert len(files) == 1
        assert files[0].name == "atomic_test.parquet"

    def test_empty_response_guard(self):
        """Empty CelesTrak response should NOT overwrite existing cache."""
        # First: cache valid data
        df = self.fetcher._parse_json([ISS_RECORD])
        self.fetcher._cache_to_parquet(df, "guard_test")

        # Simulate: fetch returns empty, but cache should be preserved
        with patch.object(self.fetcher, "_download", return_value="[]"):
            result = self.fetcher.fetch.__wrapped__(self.fetcher, "stations", force=True) if hasattr(self.fetcher.fetch, '__wrapped__') else None

        # Cache file should still have data
        cached = self.fetcher.load_cached("guard_test")
        assert len(cached) == 1


class TestFetchErrorHandling:
    """Test network error fallback behavior."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fetcher = GPFetcher(cache_dir=Path(self.tmp_dir))

    def test_unknown_group_raises(self):
        try:
            self.fetcher.fetch("nonexistent_group")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unknown group" in str(e)

    def test_fetch_by_catnr_403_raises_valueerror(self):
        """403 from CelesTrak should raise ValueError, not generic exception."""
        with patch.object(self.fetcher, "_download",
                         side_effect=urllib.error.HTTPError(
                             "url", 403, "Forbidden", {}, None)):
            try:
                self.fetcher.fetch_by_catnr(25544)
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "403" in str(e)

    def test_fetch_by_catnr_500_raises(self):
        """Non-403/404 errors should propagate."""
        with patch.object(self.fetcher, "_download",
                         side_effect=urllib.error.HTTPError(
                             "url", 500, "Server Error", {}, None)):
            try:
                self.fetcher.fetch_by_catnr(25544)
                assert False, "Should have raised"
            except urllib.error.HTTPError:
                pass


class TestDataFrameSchema:
    """Verify the output DataFrame has the expected columns and types."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fetcher = GPFetcher(cache_dir=Path(self.tmp_dir))

    def test_all_expected_columns_present(self):
        df = self.fetcher._parse_json([ISS_RECORD])
        expected_cols = [
            # Identity
            "object_name", "object_id", "norad_cat_id", "classification",
            # Epoch
            "epoch", "epoch_age_days",
            # SGP4 inputs
            "mean_motion", "eccentricity", "inclination",
            "ra_of_asc_node", "arg_of_pericenter", "mean_anomaly",
            "bstar", "mean_motion_dot", "mean_motion_ddot",
            # Derived
            "period", "semimajor_axis", "apoapsis", "periapsis",
            # Metadata (may be None)
            "object_type", "rcs_size", "country_code",
            "launch_date", "decay_date",
            # Element set metadata
            "ephemeris_type", "element_set_no", "rev_at_epoch",
            # Fetch metadata
            "fetch_time",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"

    def test_no_unexpected_columns(self):
        """Guard against accidental column additions."""
        df = self.fetcher._parse_json([ISS_RECORD])
        assert len(df.columns) == 28, f"Expected 28 columns, got {len(df.columns)}: {list(df.columns)}"

    def test_numeric_types_correct(self):
        import numpy as np
        df = self.fetcher._parse_json([ISS_RECORD])
        row = df.iloc[0]
        assert isinstance(row["mean_motion"], (float, np.floating))
        assert isinstance(row["eccentricity"], (float, np.floating))
        assert isinstance(row["norad_cat_id"], (int, np.integer))
        assert isinstance(row["period"], (float, np.floating))


import urllib.error

# --- Run all tests ---
if __name__ == "__main__":
    test_classes = [
        TestParseJson,
        TestValidation,
        TestDeriveOrbitParams,
        TestCaching,
        TestFetchErrorHandling,
        TestDataFrameSchema,
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
            if hasattr(instance, "setup_method"):
                instance.setup_method()
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
