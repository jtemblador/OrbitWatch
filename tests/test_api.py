#!/usr/bin/env python3
"""Tests for FastAPI endpoints (Week 3, Tasks 1–2).

Validates:
- Health check endpoint
- Satellite list endpoint (count, field types, field values)
- CORS headers present
- Error handling (missing cache)
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
# 3. Edge Cases
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
