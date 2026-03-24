#!/usr/bin/env python3
"""Tests for FastAPI endpoints (Week 3, Tasks 1–3).

Validates:
- Health check endpoint
- Satellite list endpoint (count, field types, field values)
- Position endpoints (batch, single, ground track)
- CORS headers present
- Error handling (404, 422, missing cache)
"""

import os
import sys
import unittest

# Ensure orbitcore .so is found before the source directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi.testclient import TestClient

from backend.main import app


# ===========================================================================
# Helpers
# ===========================================================================

# TestClient must be used as context manager to trigger lifespan events
# (which set app.state.propagator). Use module-level enter/exit so all
# test classes share one client and one propagator load.
_client_ctx = TestClient(app)
client = _client_ctx.__enter__()

# Known Phase 1 sanity bounds
ISS_NORAD = 25544
LEO_ALT_MIN, LEO_ALT_MAX = 300, 500  # km
LEO_PERIOD_MIN, LEO_PERIOD_MAX = 88, 100  # minutes


def teardown_module():
    _client_ctx.__exit__(None, None, None)


# ===========================================================================
# 1. Health Check (Task 3.1)
# ===========================================================================

class TestHealthCheck(unittest.TestCase):
    """GET /api/health — app skeleton basics."""

    def test_health_returns_200(self):
        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)

    def test_health_body(self):
        resp = client.get("/api/health")
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_cors_headers_on_preflight(self):
        resp = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access-control-allow-origin", resp.headers)

    def test_cors_headers_on_get(self):
        resp = client.get(
            "/api/health",
            headers={"Origin": "http://localhost:3000"},
        )
        self.assertIn("access-control-allow-origin", resp.headers)


# ===========================================================================
# 2. Satellite List (Task 3.2)
# ===========================================================================

class TestSatelliteList(unittest.TestCase):
    """GET /api/satellites — metadata from cached Parquet."""

    @classmethod
    def setUpClass(cls):
        cls.resp = client.get("/api/satellites")
        cls.data = cls.resp.json()

    def test_returns_200(self):
        self.assertEqual(self.resp.status_code, 200)

    def test_response_has_required_keys(self):
        self.assertIn("count", self.data)
        self.assertIn("group", self.data)
        self.assertIn("satellites", self.data)

    def test_group_is_stations(self):
        self.assertEqual(self.data["group"], "stations")

    def test_count_matches_list_length(self):
        self.assertEqual(self.data["count"], len(self.data["satellites"]))

    def test_count_approximately_30(self):
        # Phase 1 stations group has ~15-35 objects
        self.assertGreater(self.data["count"], 10)
        self.assertLess(self.data["count"], 50)

    def test_satellite_has_required_fields(self):
        required = {
            "name", "norad_id", "object_type", "epoch",
            "epoch_age_days", "period_min", "inclination_deg",
            "apoapsis_km", "periapsis_km",
        }
        for sat in self.data["satellites"]:
            self.assertTrue(
                required.issubset(sat.keys()),
                f"Missing fields in {sat.get('name', '?')}: "
                f"{required - sat.keys()}",
            )

    def test_field_types(self):
        for sat in self.data["satellites"]:
            self.assertIsInstance(sat["name"], str)
            self.assertIsInstance(sat["norad_id"], int)
            self.assertIsInstance(sat["object_type"], str)
            self.assertIsInstance(sat["epoch"], str)
            self.assertIsInstance(sat["epoch_age_days"], (int, float))
            self.assertIsInstance(sat["period_min"], (int, float))
            self.assertIsInstance(sat["inclination_deg"], (int, float))
            self.assertIsInstance(sat["apoapsis_km"], (int, float))
            self.assertIsInstance(sat["periapsis_km"], (int, float))

    def test_norad_ids_are_positive(self):
        for sat in self.data["satellites"]:
            self.assertGreater(sat["norad_id"], 0, sat["name"])

    def test_iss_present(self):
        norad_ids = [s["norad_id"] for s in self.data["satellites"]]
        self.assertIn(ISS_NORAD, norad_ids)

    def test_iss_metadata_sanity(self):
        iss = next(s for s in self.data["satellites"] if s["norad_id"] == ISS_NORAD)
        self.assertIn("ISS", iss["name"])
        # Inclination should be ~51.6°
        self.assertAlmostEqual(iss["inclination_deg"], 51.6, delta=1.0)
        # Period should be ~92-93 min
        self.assertGreater(iss["period_min"], LEO_PERIOD_MIN)
        self.assertLess(iss["period_min"], LEO_PERIOD_MAX)

    def test_iss_altitude_bounds(self):
        """ISS should be in LEO (300–500 km). Other stations may include
        debris/rocket bodies with higher orbits (e.g. FREGAT DEB ~2200 km)."""
        iss = next(s for s in self.data["satellites"] if s["norad_id"] == ISS_NORAD)
        self.assertGreater(iss["periapsis_km"], LEO_ALT_MIN)
        self.assertLess(iss["apoapsis_km"], LEO_ALT_MAX)

    def test_all_altitudes_physically_valid(self):
        """All satellites should have positive altitudes (above Earth surface)."""
        for sat in self.data["satellites"]:
            self.assertGreater(
                sat["periapsis_km"], 0,
                f"{sat['name']} periapsis below Earth surface: {sat['periapsis_km']}",
            )
            self.assertGreater(
                sat["apoapsis_km"], sat["periapsis_km"],
                f"{sat['name']} apoapsis < periapsis",
            )

    def test_epoch_is_iso8601(self):
        from datetime import datetime
        for sat in self.data["satellites"]:
            try:
                datetime.fromisoformat(sat["epoch"])
            except ValueError:
                self.fail(f"Bad epoch format for {sat['name']}: {sat['epoch']}")

    def test_epoch_age_is_reasonable(self):
        """Epoch should be within last 30 days (not ancient stale data)."""
        for sat in self.data["satellites"]:
            self.assertLess(
                sat["epoch_age_days"], 30,
                f"{sat['name']} epoch is {sat['epoch_age_days']} days old",
            )
            self.assertGreaterEqual(sat["epoch_age_days"], 0)

    def test_object_type_not_empty(self):
        for sat in self.data["satellites"]:
            self.assertTrue(len(sat["object_type"]) > 0, sat["name"])

    def test_no_numpy_types_in_json(self):
        """Verify JSON serialization didn't leak numpy types (would crash FastAPI)."""
        import json
        # If this round-trips through json.dumps without error, no numpy types leaked
        json.dumps(self.data)


# ===========================================================================
# 3. Batch Positions (Task 3.3)
# ===========================================================================

class TestBatchPositions(unittest.TestCase):
    """GET /api/positions — all satellites at current time."""

    @classmethod
    def setUpClass(cls):
        cls.resp = client.get("/api/positions")
        cls.data = cls.resp.json()

    def test_returns_200(self):
        self.assertEqual(self.resp.status_code, 200)

    def test_response_has_required_keys(self):
        for key in ("count", "timestamp", "positions"):
            self.assertIn(key, self.data)

    def test_count_matches_list_length(self):
        self.assertEqual(self.data["count"], len(self.data["positions"]))

    def test_count_approximately_30(self):
        self.assertGreater(self.data["count"], 10)
        self.assertLess(self.data["count"], 50)

    def test_position_has_required_fields(self):
        required = {"name", "norad_id", "lat", "lon", "alt_km", "speed_km_s", "epoch_age_days"}
        for pos in self.data["positions"]:
            self.assertTrue(
                required.issubset(pos.keys()),
                f"Missing fields in {pos.get('name', '?')}: {required - pos.keys()}",
            )

    def test_all_latitudes_valid(self):
        for pos in self.data["positions"]:
            self.assertGreaterEqual(pos["lat"], -90.0, pos["name"])
            self.assertLessEqual(pos["lat"], 90.0, pos["name"])

    def test_all_longitudes_valid(self):
        for pos in self.data["positions"]:
            self.assertGreaterEqual(pos["lon"], -180.0, pos["name"])
            self.assertLessEqual(pos["lon"], 180.0, pos["name"])

    def test_all_altitudes_positive(self):
        for pos in self.data["positions"]:
            self.assertGreater(pos["alt_km"], 0, pos["name"])

    def test_all_speeds_reasonable(self):
        for pos in self.data["positions"]:
            self.assertGreater(pos["speed_km_s"], 1.0, pos["name"])
            self.assertLess(pos["speed_km_s"], 11.0, pos["name"])

    def test_custom_time_param(self):
        resp = client.get("/api/positions?time=2026-03-24T12:00:00Z")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("2026-03-24T12:00:00", data["timestamp"])
        self.assertGreater(data["count"], 0)

    def test_malformed_time_returns_422(self):
        resp = client.get("/api/positions?time=not-a-date")
        self.assertEqual(resp.status_code, 422)

    def test_timestamp_is_iso8601(self):
        from datetime import datetime
        try:
            datetime.fromisoformat(self.data["timestamp"])
        except ValueError:
            self.fail(f"Bad timestamp format: {self.data['timestamp']}")

    def test_no_numpy_types_in_json(self):
        import json
        json.dumps(self.data)


# ===========================================================================
# 4. Single Position (Task 3.3)
# ===========================================================================

class TestSinglePosition(unittest.TestCase):
    """GET /api/positions/{norad_id} — single satellite."""

    def test_iss_returns_200(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}")
        self.assertEqual(resp.status_code, 200)

    def test_iss_has_required_fields(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}")
        data = resp.json()
        for key in ("name", "norad_id", "lat", "lon", "alt_km", "speed_km_s"):
            self.assertIn(key, data)

    def test_iss_altitude_bounds(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}")
        data = resp.json()
        self.assertGreater(data["alt_km"], LEO_ALT_MIN)
        self.assertLess(data["alt_km"], LEO_ALT_MAX)

    def test_iss_norad_id_matches(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}")
        self.assertEqual(resp.json()["norad_id"], ISS_NORAD)

    def test_iss_speed_bounds(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}")
        data = resp.json()
        self.assertGreater(data["speed_km_s"], 7.0)
        self.assertLess(data["speed_km_s"], 8.0)

    def test_unknown_norad_id_returns_404(self):
        resp = client.get("/api/positions/9999999")
        self.assertEqual(resp.status_code, 404)

    def test_custom_time_param(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}?time=2026-03-24T12:00:00Z")
        self.assertEqual(resp.status_code, 200)

    def test_malformed_time_returns_422(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}?time=not-a-date")
        self.assertEqual(resp.status_code, 422)


# ===========================================================================
# 5. Ground Track (Task 3.3)
# ===========================================================================

class TestGroundTrack(unittest.TestCase):
    """GET /api/positions/{norad_id}/track — orbit trail points."""

    @classmethod
    def setUpClass(cls):
        cls.resp = client.get(f"/api/positions/{ISS_NORAD}/track")
        cls.data = cls.resp.json()

    def test_returns_200(self):
        self.assertEqual(self.resp.status_code, 200)

    def test_response_has_required_keys(self):
        for key in ("norad_id", "name", "duration_min", "steps", "track"):
            self.assertIn(key, self.data)

    def test_default_60_points(self):
        self.assertEqual(len(self.data["track"]), 60)

    def test_track_point_has_required_fields(self):
        for pt in self.data["track"]:
            for key in ("lat", "lon", "alt_km", "timestamp"):
                self.assertIn(key, pt, f"Missing {key} in track point")

    def test_track_latitudes_valid(self):
        for pt in self.data["track"]:
            self.assertGreaterEqual(pt["lat"], -90.0)
            self.assertLessEqual(pt["lat"], 90.0)

    def test_track_altitudes_positive(self):
        for pt in self.data["track"]:
            self.assertGreater(pt["alt_km"], 0)

    def test_custom_steps(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}/track?steps=10&duration_min=45")
        data = resp.json()
        self.assertEqual(len(data["track"]), 10)
        self.assertEqual(data["duration_min"], 45)

    def test_timestamps_span_duration(self):
        from datetime import datetime
        track = self.data["track"]
        t_first = datetime.fromisoformat(track[0]["timestamp"])
        t_last = datetime.fromisoformat(track[-1]["timestamp"])
        span_min = (t_last - t_first).total_seconds() / 60
        # 60 steps over 90 min → last point at step 59 → ~88.5 min from first
        self.assertGreater(span_min, 80)
        self.assertLess(span_min, 95)

    def test_unknown_norad_id_returns_404(self):
        resp = client.get("/api/positions/9999999/track")
        self.assertEqual(resp.status_code, 404)

    def test_malformed_time_returns_422(self):
        resp = client.get(f"/api/positions/{ISS_NORAD}/track?time=garbage")
        self.assertEqual(resp.status_code, 422)


# ===========================================================================
# 6. Edge Cases
# ===========================================================================

class TestApiEdgeCases(unittest.TestCase):
    """Edge cases and invalid inputs for existing endpoints."""

    def test_unknown_route_returns_404(self):
        resp = client.get("/api/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_health_post_returns_405(self):
        resp = client.post("/api/health")
        self.assertEqual(resp.status_code, 405)


if __name__ == "__main__":
    unittest.main()
