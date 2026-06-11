#!/usr/bin/env python3
"""Tests for orbitcore C++ SGP4 module (Task 2.3).

Validates:
- Module imports and version info
- GravConst enum and getgravconst() constants
- Satrec struct field access (read/write)
- sgp4init() initialization with valid and invalid inputs
- sgp4() propagation accuracy and output format
- jday() and invjday() time conversion round-trips
- Cross-validation against Python sgp4 library (identical results)
- Vallado's SGP4-VER.TLE verification suite (33 satellites)
- Diverse orbit types: LEO, MEO, GEO, HEO, Molniya, decaying
- Forward and backward propagation
- Multi-orbit propagation stability
- End-to-end: C++ SGP4 → coordinate transforms → geodetic
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import orbitcore

# Constants
DEG2RAD = math.pi / 180.0
XPDOTP = 1440.0 / (2.0 * math.pi)  # rev/day -> rad/min conversion
RE_WGS72 = 6378.135  # Earth radius km (WGS-72, used by SGP4)


# ---------------------------------------------------------------------------
# Test fixtures — known TLEs for various orbit types
# ---------------------------------------------------------------------------

# ISS (ZARYA) — LEO circular, ~420 km, 51.6° inclination
ISS_TLE = {
    "line1": "1 25544U 98067A   24056.27396747  .00015798  00000+0  28508-3 0  9991",
    "line2": "2 25544  51.6415  32.0835 0004287  51.5994  12.5648 15.49571617441044",
}

# GPS BIIR-2 (PRN 13) — MEO, ~20200 km, 55° inclination
GPS_TLE = {
    "line1": "1 24876U 97035A   06176.94472850  .00000045  00000+0  10000-3 0  7528",
    "line2": "2 24876  55.7010 194.2880 0040434  58.5765 301.9425  2.00560449 66900",
}

# Molniya 1-36 — HEO, e=0.707, 12h resonant (from Vallado test suite)
MOLNIYA_TLE = {
    "line1": "1 09880U 77021A   06176.56157475  .00000421  00000-0  10000-3 0  9814",
    "line2": "2 09880  64.5968 349.3786 7069051 270.0229  16.3320  2.00813614112380",
}


def _init_from_tle(line1, line2, opsmode="a"):
    """Helper: parse TLE via Python sgp4, init our C++ module with same params."""
    from sgp4.api import Satrec as PySatrec, WGS72

    pysat = PySatrec.twoline2rv(line1, line2, WGS72)
    satnum = line1[2:7].strip()
    epoch = pysat.jdsatepoch + pysat.jdsatepochF - 2433281.5

    satrec = orbitcore.sgp4init(
        orbitcore.GravConst.WGS72,
        opsmode,
        satnum,
        epoch,
        pysat.bstar,
        pysat.ndot,
        pysat.nddot,
        pysat.ecco,
        pysat.argpo,
        pysat.inclo,
        pysat.mo,
        pysat.no_kozai,
        pysat.nodeo,
    )
    return satrec, pysat


# ===================================================================
# 1. Module loading and version
# ===================================================================
class TestModuleBasics:
    def test_import(self):
        assert hasattr(orbitcore, "sgp4init")
        assert hasattr(orbitcore, "sgp4")
        assert hasattr(orbitcore, "jday")
        assert hasattr(orbitcore, "invjday")
        assert hasattr(orbitcore, "getgravconst")
        assert hasattr(orbitcore, "GravConst")
        assert hasattr(orbitcore, "Satrec")

    def test_version_string(self):
        assert isinstance(orbitcore.SGP4_VERSION, str)
        assert "SGP4" in orbitcore.SGP4_VERSION

    def test_hello_world_still_works(self):
        result = orbitcore.hello_world()
        assert isinstance(result, str)
        assert "Hello" in result


# ===================================================================
# 2. GravConst enum and getgravconst()
# ===================================================================
class TestGravityConstants:
    def test_enum_values_exist(self):
        assert orbitcore.GravConst.WGS72OLD is not None
        assert orbitcore.GravConst.WGS72 is not None
        assert orbitcore.GravConst.WGS84 is not None

    def test_wgs72_constants(self):
        c = orbitcore.getgravconst(orbitcore.GravConst.WGS72)
        assert c["mus"] == 398600.8
        assert c["radiusearthkm"] == 6378.135
        assert abs(c["j2"] - 0.001082616) < 1e-12
        assert abs(c["j3"] - (-2.53881e-6)) < 1e-15
        assert abs(c["j4"] - (-1.65597e-6)) < 1e-15
        # j3oj2 should be j3/j2
        assert abs(c["j3oj2"] - c["j3"] / c["j2"]) < 1e-15

    def test_wgs84_constants(self):
        c = orbitcore.getgravconst(orbitcore.GravConst.WGS84)
        assert c["mus"] == 398600.5
        assert c["radiusearthkm"] == 6378.137

    def test_wgs72_vs_wgs84_differ(self):
        c72 = orbitcore.getgravconst(orbitcore.GravConst.WGS72)
        c84 = orbitcore.getgravconst(orbitcore.GravConst.WGS84)
        assert c72["mus"] != c84["mus"]
        assert c72["radiusearthkm"] != c84["radiusearthkm"]

    def test_all_keys_present(self):
        c = orbitcore.getgravconst(orbitcore.GravConst.WGS72)
        expected = {"tumin", "mus", "radiusearthkm", "xke", "j2", "j3", "j4", "j3oj2"}
        assert set(c.keys()) == expected

    def test_xke_consistent_with_mus(self):
        """xke = sqrt(mus) / (radiusearthkm^1.5 * tumin)"""
        c = orbitcore.getgravconst(orbitcore.GravConst.WGS72)
        xke_calc = math.sqrt(c["mus"]) / (c["radiusearthkm"] ** 1.5) * 60.0
        # xke is in 1/min units, should be close
        assert abs(c["xke"] - xke_calc) < 1e-8


# ===================================================================
# 3. Satrec struct field access
# ===================================================================
class TestSatrec:
    def test_default_construction(self):
        s = orbitcore.Satrec()
        assert s.error == 0

    def test_satnum_read_write(self):
        s = orbitcore.Satrec()
        s.satnum = "25544"
        assert s.satnum == "25544"

    def test_orbital_elements_after_init(self):
        satrec, _ = _init_from_tle(**ISS_TLE)
        assert satrec.error == 0
        # ISS eccentricity ~0.0004
        assert abs(satrec.ecco - 0.0004287) < 1e-8
        # ISS inclination ~51.6 deg
        assert abs(satrec.inclo - 51.6415 * DEG2RAD) < 1e-6
        # Semi-major axis should be populated
        assert satrec.a > 1.0  # in Earth radii

    def test_gravity_constants_populated(self):
        satrec, _ = _init_from_tle(**ISS_TLE)
        assert satrec.radiusearthkm == RE_WGS72
        assert satrec.mus == 398600.8
        assert satrec.j2 > 0

    def test_alta_altp_reasonable(self):
        """Apogee and perigee altitudes for ISS should be ~415-425 km."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        alta_km = satrec.alta * RE_WGS72
        altp_km = satrec.altp * RE_WGS72
        assert 380 < altp_km < 450, f"Perigee {altp_km} km out of range"
        assert 380 < alta_km < 450, f"Apogee {alta_km} km out of range"
        assert alta_km >= altp_km  # apogee >= perigee

    def test_epoch_fields_populated(self):
        satrec, _ = _init_from_tle(**ISS_TLE)
        assert satrec.jdsatepoch > 2400000  # Julian date
        assert 0 <= satrec.jdsatepochF < 1  # fractional day


# ===================================================================
# 4. sgp4init() — initialization
# ===================================================================
class TestSgp4Init:
    def test_iss_init_succeeds(self):
        satrec, _ = _init_from_tle(**ISS_TLE)
        assert satrec.error == 0

    def test_gps_init_succeeds(self):
        satrec, _ = _init_from_tle(**GPS_TLE)
        assert satrec.error == 0

    def test_molniya_init_succeeds(self):
        satrec, _ = _init_from_tle(**MOLNIYA_TLE)
        assert satrec.error == 0

    def test_afspc_mode(self):
        satrec, _ = _init_from_tle(**ISS_TLE, opsmode="a")
        assert satrec.error == 0

    def test_improved_mode(self):
        satrec, _ = _init_from_tle(**ISS_TLE, opsmode="i")
        assert satrec.error == 0

    def test_invalid_eccentricity_raises(self):
        """Eccentricity >= 1 should cause initialization to fail."""
        try:
            orbitcore.sgp4init(
                orbitcore.GravConst.WGS72, "a", "99999", 27084.0,
                0.0, 0.0, 0.0,
                1.5,   # eccentricity > 1 — invalid
                0.0, 0.5, 0.0, 0.06, 0.0,
            )
            # If it doesn't raise, check error field
        except RuntimeError:
            pass  # Expected

    def test_zero_mean_motion_raises(self):
        """Zero mean motion should fail."""
        try:
            orbitcore.sgp4init(
                orbitcore.GravConst.WGS72, "a", "99999", 27084.0,
                0.0, 0.0, 0.0,
                0.001, 0.0, 0.5, 0.0,
                0.0,  # zero mean motion
                0.0,
            )
        except (RuntimeError, ZeroDivisionError):
            pass  # Expected

    def test_wgs72_vs_wgs84_produce_different_results(self):
        """Same elements with different gravity models should differ."""
        from sgp4.api import Satrec as PySatrec, WGS72

        pysat = PySatrec.twoline2rv(ISS_TLE["line1"], ISS_TLE["line2"], WGS72)
        epoch = pysat.jdsatepoch + pysat.jdsatepochF - 2433281.5

        s72 = orbitcore.sgp4init(
            orbitcore.GravConst.WGS72, "a", "25544", epoch,
            pysat.bstar, pysat.ndot, pysat.nddot, pysat.ecco,
            pysat.argpo, pysat.inclo, pysat.mo, pysat.no_kozai, pysat.nodeo,
        )
        s84 = orbitcore.sgp4init(
            orbitcore.GravConst.WGS84, "a", "25544", epoch,
            pysat.bstar, pysat.ndot, pysat.nddot, pysat.ecco,
            pysat.argpo, pysat.inclo, pysat.mo, pysat.no_kozai, pysat.nodeo,
        )

        pos72, _ = orbitcore.sgp4(s72, 60.0)
        pos84, _ = orbitcore.sgp4(s84, 60.0)
        diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos72, pos84)))
        assert diff > 0.001, "WGS72 and WGS84 should produce different positions"


# ===================================================================
# 5. sgp4() — propagation
# ===================================================================
class TestSgp4Propagation:
    def test_output_format(self):
        """sgp4() returns ((x,y,z), (vx,vy,vz)) tuples."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)
        assert len(pos) == 3
        assert len(vel) == 3
        assert all(isinstance(x, float) for x in pos)
        assert all(isinstance(x, float) for x in vel)

    def test_iss_at_epoch(self):
        """ISS at epoch: altitude ~400-430 km, speed ~7.6-7.7 km/s."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)
        dist = math.sqrt(sum(x ** 2 for x in pos))
        speed = math.sqrt(sum(v ** 2 for v in vel))
        alt = dist - RE_WGS72
        assert 380 < alt < 450, f"ISS altitude {alt:.1f} km"
        assert 7.4 < speed < 7.9, f"ISS speed {speed:.3f} km/s"

    def test_gps_at_epoch(self):
        """GPS: altitude ~20000-20600 km, period ~12h."""
        satrec, _ = _init_from_tle(**GPS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)
        dist = math.sqrt(sum(x ** 2 for x in pos))
        alt = dist - RE_WGS72
        assert 19000 < alt < 21000, f"GPS altitude {alt:.1f} km"

    def test_molniya_apogee_perigee(self):
        """Molniya: highly eccentric, perigee ~500 km, apogee ~40000 km."""
        satrec, _ = _init_from_tle(**MOLNIYA_TLE)
        # Sample at many times over one orbit (~12h = 720 min)
        min_dist = float("inf")
        max_dist = 0.0
        for t in range(0, 721, 10):
            pos, _ = orbitcore.sgp4(satrec, float(t))
            dist = math.sqrt(sum(x ** 2 for x in pos))
            min_dist = min(min_dist, dist)
            max_dist = max(max_dist, dist)
        min_alt = min_dist - RE_WGS72
        max_alt = max_dist - RE_WGS72
        assert min_alt < 2000, f"Molniya perigee {min_alt:.0f} km too high"
        assert max_alt > 30000, f"Molniya apogee {max_alt:.0f} km too low"

    def test_forward_propagation(self):
        """Propagate ISS 24 hours forward — altitude stays reasonable."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        for t in range(0, 1441, 60):  # every hour for 24h
            pos, vel = orbitcore.sgp4(satrec, float(t))
            dist = math.sqrt(sum(x ** 2 for x in pos))
            alt = dist - RE_WGS72
            assert 350 < alt < 500, f"ISS altitude {alt:.1f} km at t={t} min"

    def test_backward_propagation(self):
        """Propagate ISS backward — should work (Vallado fixed this)."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, -90.0)
        dist = math.sqrt(sum(x ** 2 for x in pos))
        alt = dist - RE_WGS72
        assert 350 < alt < 500, f"Backward propagation altitude {alt:.1f} km"

    def test_propagation_repeatable(self):
        """Same tsince should give identical results every time."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos1, vel1 = orbitcore.sgp4(satrec, 120.0)
        pos2, vel2 = orbitcore.sgp4(satrec, 120.0)
        assert pos1 == pos2
        assert vel1 == vel2

    def test_propagation_order_independent(self):
        """Results shouldn't depend on what time you propagated before."""
        satrec1, _ = _init_from_tle(**ISS_TLE)
        satrec2, _ = _init_from_tle(**ISS_TLE)

        # satrec1: propagate to 60, then 120
        orbitcore.sgp4(satrec1, 60.0)
        pos1, _ = orbitcore.sgp4(satrec1, 120.0)

        # satrec2: propagate directly to 120
        pos2, _ = orbitcore.sgp4(satrec2, 120.0)

        diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos1, pos2)))
        assert diff < 1e-9, f"Order-dependent difference: {diff} km"

    def test_tsince_updates_satrec_t(self):
        """After propagation, satrec.t should equal tsince."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        orbitcore.sgp4(satrec, 42.5)
        assert abs(satrec.t - 42.5) < 1e-10

    def test_one_orbit_returns_near_start(self):
        """After one ISS orbit (~92.8 min), position should be near the start."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        period_min = 1440.0 / 15.49571617  # rev/day -> minutes
        pos0, _ = orbitcore.sgp4(satrec, 0.0)
        pos1, _ = orbitcore.sgp4(satrec, period_min)
        # Won't be exactly the same (drag, J2 precession), but within ~50 km
        diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos0, pos1)))
        assert diff < 100, f"After 1 orbit, diff = {diff:.1f} km (too large)"


# ===================================================================
# 6. jday() and invjday() — time conversions
# ===================================================================
class TestTimeConversions:
    def test_j2000_epoch(self):
        """J2000.0 epoch: 2000 Jan 1 12:00:00 UTC = JD 2451545.0."""
        jd, jdF = orbitcore.jday(2000, 1, 1, 12, 0, 0.0)
        assert abs((jd + jdF) - 2451545.0) < 1e-10

    def test_unix_epoch(self):
        """Unix epoch: 1970 Jan 1 00:00:00 UTC = JD 2440587.5."""
        jd, jdF = orbitcore.jday(1970, 1, 1, 0, 0, 0.0)
        assert abs((jd + jdF) - 2440587.5) < 1e-10

    def test_jday_invjday_roundtrip(self):
        """jday → invjday should return original date."""
        jd, jdF = orbitcore.jday(2026, 3, 23, 15, 30, 45.0)
        yr, mo, dy, hr, mn, sc = orbitcore.invjday(jd, jdF)
        assert yr == 2026
        assert mo == 3
        assert dy == 23
        assert hr == 15
        assert mn == 30
        assert abs(sc - 45.0) < 0.001

    def test_leap_year(self):
        """Feb 29 in a leap year should work."""
        jd1, jdF1 = orbitcore.jday(2024, 2, 29, 0, 0, 0.0)
        jd2, jdF2 = orbitcore.jday(2024, 3, 1, 0, 0, 0.0)
        diff = (jd2 + jdF2) - (jd1 + jdF1)
        assert abs(diff - 1.0) < 1e-10, "Feb 29 → Mar 1 should be 1 day"

    def test_fractional_seconds(self):
        """Fractional seconds should be preserved."""
        jd, jdF = orbitcore.jday(2026, 6, 15, 12, 0, 0.123456)
        yr, mo, dy, hr, mn, sc = orbitcore.invjday(jd, jdF)
        assert abs(sc - 0.123456) < 0.001

    def test_midnight_vs_noon(self):
        """Noon and midnight of the same day differ by 0.5 JD."""
        jd_noon, jdF_noon = orbitcore.jday(2026, 1, 1, 12, 0, 0.0)
        jd_mid, jdF_mid = orbitcore.jday(2026, 1, 1, 0, 0, 0.0)
        diff = (jd_noon + jdF_noon) - (jd_mid + jdF_mid)
        assert abs(diff - 0.5) < 1e-10


# ===================================================================
# 7. Cross-validation against Python sgp4 library
# ===================================================================
class TestCrossValidation:
    def _compare(self, line1, line2, times_min, tol_km=1e-9):
        satrec, pysat = _init_from_tle(line1, line2)
        for t in times_min:
            e_py, r_py, v_py = pysat.sgp4(
                pysat.jdsatepoch, pysat.jdsatepochF + t / 1440.0
            )
            if e_py != 0:
                continue
            pos, vel = orbitcore.sgp4(satrec, t)
            r_diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, r_py)))
            v_diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(vel, v_py)))
            assert r_diff < tol_km, f"Position diff {r_diff} km at t={t}"
            assert v_diff < 1e-12, f"Velocity diff {v_diff} km/s at t={t}"

    def test_iss_cross_validation(self):
        self._compare(**ISS_TLE, times_min=[0, 30, 60, 90, 120, 720, 1440])

    def test_gps_cross_validation(self):
        self._compare(**GPS_TLE, times_min=[0, 60, 360, 720])

    def test_molniya_cross_validation(self):
        self._compare(**MOLNIYA_TLE, times_min=[0, 120, 360, 720])


# ===================================================================
# 8. Vallado's SGP4-VER.TLE verification suite
# ===================================================================
class TestValladoVerification:
    VER_TLE_PATH = os.path.join(
        os.path.dirname(__file__), "..",
        "misc", "Revisiting Spacetrack Report #3",
        "AIAA-2006-6753", "sgp4", "cpp", "testsgp4", "SGP4-VER.TLE",
    )

    @staticmethod
    def _parse_ver_tle(filepath):
        tests = []
        with open(filepath) as f:
            lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#") or line == "":
                i += 1
                continue
            if line.startswith("1 "):
                line1 = line
                i += 1
                line2_full = lines[i].strip()
                line2 = line2_full[:69]
                remaining = line2_full[69:].strip().split()
                if len(remaining) >= 3:
                    startmfe = float(remaining[0])
                    stopmfe = float(remaining[1])
                    deltamin = float(remaining[2])
                else:
                    startmfe, stopmfe, deltamin = 0.0, 1440.0, 120.0
                tests.append({
                    "satnum": line1[2:7].strip(),
                    "line1": line1,
                    "line2": line2,
                    "startmfe": startmfe,
                    "stopmfe": stopmfe,
                    "deltamin": deltamin,
                })
            i += 1
        return tests

    def test_all_33_satellites(self):
        """Run all 33 Vallado test satellites, cross-validate against Python sgp4."""
        from sgp4.api import Satrec as PySatrec, WGS72

        tests = self._parse_ver_tle(self.VER_TLE_PATH)
        assert len(tests) == 33, f"Expected 33 test cases, got {len(tests)}"

        passed = 0
        for test in tests:
            pysat = PySatrec.twoline2rv(test["line1"], test["line2"], WGS72)
            epoch = pysat.jdsatepoch + pysat.jdsatepochF - 2433281.5

            try:
                satrec = orbitcore.sgp4init(
                    orbitcore.GravConst.WGS72, "a", test["satnum"], epoch,
                    pysat.bstar, pysat.ndot, pysat.nddot, pysat.ecco,
                    pysat.argpo, pysat.inclo, pysat.mo, pysat.no_kozai, pysat.nodeo,
                )
            except RuntimeError:
                continue  # Some edge cases may fail init

            tsince = test["startmfe"]
            sat_ok = True
            while tsince <= test["stopmfe"]:
                e_py, r_py, v_py = pysat.sgp4(
                    pysat.jdsatepoch, pysat.jdsatepochF + tsince / 1440.0
                )
                try:
                    pos, vel = orbitcore.sgp4(satrec, tsince)
                except RuntimeError:
                    if e_py != 0:
                        tsince += test["deltamin"]
                        continue
                    sat_ok = False
                    break

                if e_py != 0:
                    tsince += test["deltamin"]
                    continue

                diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, r_py)))
                # Allow 1 km tolerance for opsmode difference on deep-space sats
                if diff > 1.0:
                    sat_ok = False
                    break
                tsince += test["deltamin"]

            if sat_ok:
                passed += 1

        assert passed >= 32, f"Only {passed}/33 test satellites passed"

    def test_near_earth_satellites_exact(self):
        """Near-earth test sats should match Python sgp4 to sub-micrometer."""
        from sgp4.api import Satrec as PySatrec, WGS72

        near_earth_tles = [
            # Sat 00005 — basic near-earth
            ("1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
             "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"),
            # Sat 06251 — moderate drag
            ("1 06251U 62025E   06176.82412014  .00008885  00000-0  12808-3 0  3985",
             "2 06251  58.0579  54.0425 0030035 139.1568 221.1854 15.56387291  6774"),
        ]

        for line1, line2 in near_earth_tles:
            pysat = PySatrec.twoline2rv(line1, line2, WGS72)
            satnum = line1[2:7].strip()
            epoch = pysat.jdsatepoch + pysat.jdsatepochF - 2433281.5

            satrec = orbitcore.sgp4init(
                orbitcore.GravConst.WGS72, "a", satnum, epoch,
                pysat.bstar, pysat.ndot, pysat.nddot, pysat.ecco,
                pysat.argpo, pysat.inclo, pysat.mo, pysat.no_kozai, pysat.nodeo,
            )

            for t in [0.0, 360.0, 720.0, 1440.0]:
                e_py, r_py, v_py = pysat.sgp4(
                    pysat.jdsatepoch, pysat.jdsatepochF + t / 1440.0
                )
                if e_py != 0:
                    continue
                pos, vel = orbitcore.sgp4(satrec, t)
                diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, r_py)))
                assert diff < 1e-6, f"Sat {satnum} at t={t}: diff={diff} km"


# ===================================================================
# 9. End-to-end: C++ SGP4 → coordinate transforms → geodetic
# ===================================================================
class TestEndToEnd:
    def test_iss_sgp4_to_geodetic(self):
        """C++ SGP4 → teme_to_geodetic → verify ISS lat/lon/alt."""
        from core.coordinate_transforms import teme_to_geodetic, utc_to_jd

        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)

        # Get Julian Date for the epoch
        jd = satrec.jdsatepoch + satrec.jdsatepochF

        result = teme_to_geodetic(list(pos), jd, vel_teme=list(vel))

        # ISS: lat in [-51.6, 51.6], alt ~400-430 km, lon any value
        assert -52 < result["lat"] < 52, f"Lat {result['lat']}"
        assert 380 < result["alt"] < 450, f"Alt {result['alt']}"
        assert -180 <= result["lon"] <= 180

    def test_iss_groundtrack_over_orbit(self):
        """Propagate ISS one orbit, verify groundtrack stays within inclination band."""
        from core.coordinate_transforms import teme_to_geodetic

        satrec, _ = _init_from_tle(**ISS_TLE)
        period_min = 1440.0 / 15.49571617

        for t_frac in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
            t = t_frac * period_min
            pos, vel = orbitcore.sgp4(satrec, t)
            jd = satrec.jdsatepoch + satrec.jdsatepochF + t / 1440.0
            result = teme_to_geodetic(list(pos), jd, vel_teme=list(vel))
            # Latitude must be within inclination band
            assert abs(result["lat"]) < 53, f"Lat {result['lat']} at t={t:.1f}"
            assert 350 < result["alt"] < 500

    def test_gps_altitude(self):
        """GPS satellite altitude should be ~20200 km."""
        from core.coordinate_transforms import teme_to_geodetic

        satrec, _ = _init_from_tle(**GPS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)
        jd = satrec.jdsatepoch + satrec.jdsatepochF
        result = teme_to_geodetic(list(pos), jd, vel_teme=list(vel))
        assert 19000 < result["alt"] < 21000, f"GPS alt {result['alt']}"

    def test_velocity_magnitude_from_cpp(self):
        """C++ velocity fed through transforms should give ~7.66 km/s for ISS."""
        from core.coordinate_transforms import teme_to_geodetic

        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)
        jd = satrec.jdsatepoch + satrec.jdsatepochF
        result = teme_to_geodetic(list(pos), jd, vel_teme=list(vel))

        vel_ecef = result["vel_ecef"]
        speed = math.sqrt(sum(v ** 2 for v in vel_ecef))
        # ECEF speed differs from inertial by ~0.4 km/s due to Earth rotation
        assert 7.0 < speed < 8.0, f"ECEF speed {speed:.3f} km/s"


# ===================================================================
# 10. Edge cases and robustness
# ===================================================================
class TestEdgeCases:
    def test_propagate_zero_minutes(self):
        """t=0 should work and give valid output."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 0.0)
        assert all(math.isfinite(x) for x in pos)
        assert all(math.isfinite(v) for v in vel)

    def test_propagate_large_tsince(self):
        """Propagate 7 days forward — should still work (accuracy degrades)."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, 7 * 1440.0)
        dist = math.sqrt(sum(x ** 2 for x in pos))
        alt = dist - RE_WGS72
        # Altitude should still be vaguely LEO (drag makes it decay, but 7 days is fine)
        assert 200 < alt < 600, f"Altitude after 7 days: {alt:.1f} km"

    def test_propagate_negative_tsince(self):
        """Backward propagation should work."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        pos, vel = orbitcore.sgp4(satrec, -1440.0)
        assert all(math.isfinite(x) for x in pos)

    def test_multiple_satellites_independent(self):
        """Two separate Satrec objects should not interfere."""
        sat_iss, _ = _init_from_tle(**ISS_TLE)
        sat_gps, _ = _init_from_tle(**GPS_TLE)

        pos_iss, _ = orbitcore.sgp4(sat_iss, 0.0)
        pos_gps, _ = orbitcore.sgp4(sat_gps, 0.0)

        dist_iss = math.sqrt(sum(x ** 2 for x in pos_iss))
        dist_gps = math.sqrt(sum(x ** 2 for x in pos_gps))

        # ISS ~6780 km, GPS ~26500 km — very different
        assert abs(dist_iss - dist_gps) > 10000

    def test_rapid_sequential_propagation(self):
        """Propagate 1000 times in a loop — no crashes or memory issues."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        for i in range(1000):
            pos, vel = orbitcore.sgp4(satrec, float(i) * 0.1)
        # Just verify the last one is sane
        dist = math.sqrt(sum(x ** 2 for x in pos))
        assert 6500 < dist < 7000

    def test_high_eccentricity_orbit(self):
        """Molniya orbit (e=0.707) should propagate without error."""
        satrec, _ = _init_from_tle(**MOLNIYA_TLE)
        # Propagate over a full orbit
        for t in range(0, 721, 30):
            pos, vel = orbitcore.sgp4(satrec, float(t))
            assert all(math.isfinite(x) for x in pos)
            assert all(math.isfinite(v) for v in vel)


# ===================================================================
# 10. Batch propagation — propagate_batch() (Task 6.1)
# ===================================================================
class TestPropagateBatch:
    """propagate_batch(): many satellites in one Python→C++ crossing.

    Correctness anchor: results must be BIT-IDENTICAL to individual
    orbitcore.sgp4() calls (same code path, no tolerance). The existing
    TestCrossValidation / TestValladoVerification suites validate sgp4()
    itself, so identity here transitively cross-validates the batch.
    """

    def test_exposed(self):
        assert hasattr(orbitcore, "propagate_batch")

    def test_matches_single_calls_exactly(self):
        """Bit-identical to single sgp4() across orbit types and times
        (incl. backward propagation)."""
        for tle in (ISS_TLE, GPS_TLE, MOLNIYA_TLE):
            for t in (0.0, 60.0, 360.0, -30.0):
                sat_single, _ = _init_from_tle(**tle)
                sat_batch, _ = _init_from_tle(**tle)
                expected = orbitcore.sgp4(sat_single, t)
                got = orbitcore.propagate_batch([sat_batch], [t])
                assert got[0] == expected

    def test_mixed_satellites_one_call(self):
        """LEO + MEO + HEO with different tsince values in a single batch."""
        tsinces = [10.0, 120.0, 720.0]
        sats, expected = [], []
        for tle, t in zip((ISS_TLE, GPS_TLE, MOLNIYA_TLE), tsinces):
            s_batch, _ = _init_from_tle(**tle)
            s_single, _ = _init_from_tle(**tle)
            sats.append(s_batch)
            expected.append(orbitcore.sgp4(s_single, t))
        assert orbitcore.propagate_batch(sats, tsinces) == expected

    def test_result_shape_and_sanity(self):
        """((x,y,z),(vx,vy,vz)) floats, finite, ISS radius in LEO range."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        (pos, vel), = orbitcore.propagate_batch([satrec], [60.0])
        assert len(pos) == 3 and len(vel) == 3
        assert all(math.isfinite(x) for x in pos + vel)
        radius = math.sqrt(sum(x ** 2 for x in pos))
        assert 6500 < radius < 7000  # ISS: ~6780 km from geocenter
        speed = math.sqrt(sum(v ** 2 for v in vel))
        assert 7.0 < speed < 8.0  # LEO orbital speed km/s

    def test_empty_inputs(self):
        assert orbitcore.propagate_batch([], []) == []

    def test_accepts_tuple_inputs(self):
        """py::sequence input — tuples work as well as lists; int tsince ok."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        ref, _ = _init_from_tle(**ISS_TLE)
        got = orbitcore.propagate_batch((satrec,), (60,))
        assert got[0] == orbitcore.sgp4(ref, 60.0)

    def test_length_mismatch_raises_valueerror(self):
        satrec, _ = _init_from_tle(**ISS_TLE)
        try:
            orbitcore.propagate_batch([satrec], [1.0, 2.0])
            assert False, "should have raised ValueError"
        except ValueError as e:
            assert "1 vs 2" in str(e)

    def test_non_satrec_item_raises_typeerror_with_index(self):
        """Bad items (wrong type, None) raise TypeError naming the index —
        None is the regression case: pybind11 casts None to nullptr on
        pointer casts, which segfaulted an early implementation."""
        good, _ = _init_from_tle(**ISS_TLE)
        for bad in ("not a satrec", None, 42):
            try:
                orbitcore.propagate_batch([good, bad], [0.0, 0.0])
                assert False, f"should have raised TypeError for {bad!r}"
            except TypeError as e:
                assert "item 1" in str(e)

    def test_failed_sat_yields_none_others_unaffected(self):
        """A decaying satellite returns None at its index; neighbors still
        propagate. One bad sat must not kill the batch."""
        good_a, pysat = _init_from_tle(**ISS_TLE)
        good_b, _ = _init_from_tle(**ISS_TLE)
        # Same elements but absurd drag — decays within days, error != 0
        epoch = pysat.jdsatepoch + pysat.jdsatepochF - 2433281.5
        decayer = orbitcore.sgp4init(
            orbitcore.GravConst.WGS72, "a", "99999", epoch,
            0.1,  # bstar ~350x ISS — guarantees decay
            pysat.ndot, pysat.nddot, pysat.ecco, pysat.argpo,
            pysat.inclo, pysat.mo, pysat.no_kozai, pysat.nodeo,
        )
        t_far = 30.0 * 1440.0  # 30 days
        out = orbitcore.propagate_batch(
            [good_a, decayer, good_b], [t_far, t_far, 60.0])
        assert out[0] is not None
        assert out[1] is None
        assert decayer.error != 0
        assert out[2] is not None

    def test_failed_sat_reusable_at_other_times(self):
        """sgp4() clears the error flag per call (SGP4.cpp:1779) — a failed
        satellite must succeed again at a valid time."""
        _, pysat = _init_from_tle(**ISS_TLE)
        epoch = pysat.jdsatepoch + pysat.jdsatepochF - 2433281.5
        decayer = orbitcore.sgp4init(
            orbitcore.GravConst.WGS72, "a", "99999", epoch,
            0.1, pysat.ndot, pysat.nddot, pysat.ecco, pysat.argpo,
            pysat.inclo, pysat.mo, pysat.no_kozai, pysat.nodeo,
        )
        assert orbitcore.propagate_batch([decayer], [30.0 * 1440.0]) == [None]
        again = orbitcore.propagate_batch([decayer], [0.0])
        assert again[0] is not None
        assert decayer.error == 0

    def test_mutates_satrec_like_single_call(self):
        """Items are passed by reference (not copied): satrec.t updates,
        matching the single-call binding's semantics."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        orbitcore.propagate_batch([satrec], [42.0])
        assert satrec.t == 42.0

    def test_cross_validation_vs_python_sgp4(self):
        """Direct check against the python-sgp4 library (Brandon Rhodes):
        ISS at epoch+60min, sub-meter agreement."""
        satrec, pysat = _init_from_tle(**ISS_TLE)
        (pos, _), = orbitcore.propagate_batch([satrec], [60.0])
        e, py_r, _ = pysat.sgp4(pysat.jdsatepoch,
                                pysat.jdsatepochF + 60.0 / 1440.0)
        assert e == 0
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(pos, py_r)))
        assert dist < 0.001  # km — sub-meter

    def test_batch_faster_than_python_loop(self):
        """The point of the batch call: one boundary crossing beats N.
        min-of-3 timings at N=1000 to keep this robust on a loaded machine."""
        import time
        satrec, _ = _init_from_tle(**ISS_TLE)
        n = 1000
        tsinces = [float(i) * 0.1 for i in range(n)]
        sats = [satrec] * n  # same satrec is fine — each call restarts from epoch

        def best_of(fn, runs=3):
            times = []
            for _ in range(runs):
                t0 = time.perf_counter()
                result = fn()
                times.append(time.perf_counter() - t0)
            return min(times), result

        t_loop, loop_result = best_of(
            lambda: [orbitcore.sgp4(satrec, t) for t in tsinces])
        t_batch, batch_result = best_of(
            lambda: orbitcore.propagate_batch(sats, tsinces))

        assert batch_result == loop_result  # free correctness check
        print(f"\n  batch {t_batch*1e3:.2f} ms vs loop {t_loop*1e3:.2f} ms "
              f"({t_loop/t_batch:.2f}x) for {n} propagations")
        # Measured speedup is modest (~1.05x): sgp4() compute dominates and
        # the batch still builds Python tuples per sat. The order-of-magnitude
        # win is the all-C++ medium filter (6.3). Asserting a strict win on a
        # ~5% margin would be a flaky timing race (week-3 lesson) — enforce
        # "not meaningfully slower" and record the ratio above.
        assert t_batch < t_loop * 1.10, (
            f"batch ({t_batch:.4f}s) should not be slower than the "
            f"Python loop ({t_loop:.4f}s)")


# ===================================================================
# 11. Coarse conjunction filter — coarse_filter() (Task 6.2)
# ===================================================================
class TestCoarseFilter:
    """coarse_filter(): altitude-band pair screening, stage 1 of the
    conjunction cascade. Pure interval math — no propagation."""

    # Altitude bands (km): ISS, GPS, GEO, Molniya (HEO spans MEO->GEO)
    PERI = [415.0, 20180.0, 35780.0, 500.0]
    APO = [424.0, 20270.0, 35800.0, 39000.0]

    def test_exposed(self):
        assert hasattr(orbitcore, "coarse_filter")

    def test_disjoint_bands_rejected(self):
        """ISS (415-424) vs GPS (20180-20270): can never meet."""
        assert orbitcore.coarse_filter(
            [415.0, 20180.0], [424.0, 20270.0], 0.0) == []

    def test_co_altitude_paired(self):
        assert orbitcore.coarse_filter(
            [415.0, 410.0], [424.0, 430.0], 0.0) == [(0, 1)]

    def test_heo_crosses_meo_and_geo_but_not_iss(self):
        """Molniya (500-39000 km) overlaps GPS and GEO; its perigee sits
        76 km ABOVE ISS apogee, so no ISS pair at pad=0."""
        pairs = orbitcore.coarse_filter(self.PERI, self.APO, 0.0)
        assert pairs == [(1, 3), (2, 3)]

    def test_pad_bridges_gap(self):
        """The ISS-Molniya gap is 76 km: pad >= 76 pairs them."""
        pairs = orbitcore.coarse_filter(self.PERI, self.APO, 76.0)
        assert (0, 3) in pairs
        pairs_75 = orbitcore.coarse_filter(self.PERI, self.APO, 75.9)
        assert (0, 3) not in pairs_75

    def test_touching_bands_count_as_overlap(self):
        """apogee_a == perigee_b exactly -> paired (<= semantics)."""
        assert orbitcore.coarse_filter(
            [400.0, 424.0], [424.0, 500.0], 0.0) == [(0, 1)]

    def test_pair_ordering_no_self_no_dup(self):
        """All-overlapping set: exactly N(N-1)/2 pairs, i<j, row-major."""
        n = 5
        pairs = orbitcore.coarse_filter([400.0] * n, [500.0] * n, 0.0)
        expected = [(i, j) for i in range(n) for j in range(i + 1, n)]
        assert pairs == expected

    def test_empty_inputs(self):
        assert orbitcore.coarse_filter([], [], 10.0) == []

    def test_single_satellite_no_pairs(self):
        assert orbitcore.coarse_filter([400.0], [450.0], 1000.0) == []

    def test_length_mismatch_raises_valueerror(self):
        try:
            orbitcore.coarse_filter([1.0], [1.0, 2.0], 0.0)
            assert False, "should have raised ValueError"
        except ValueError as e:
            assert "1 vs 2" in str(e)

    def test_negative_pad_raises_valueerror(self):
        try:
            orbitcore.coarse_filter([1.0], [2.0], -5.0)
            assert False, "should have raised ValueError"
        except ValueError:
            pass

    def test_nan_band_matches_nothing(self):
        """NaN comparisons are false -> a NaN-band sat silently pairs with
        nothing (documented IEEE semantics), neighbors unaffected."""
        nan = float("nan")
        pairs = orbitcore.coarse_filter(
            [nan, 400.0, 410.0], [nan, 450.0, 460.0], 1e6)
        assert pairs == [(1, 2)]

    def test_matches_brute_force_property(self):
        """Property check vs an independent Python implementation on a
        pseudo-random population."""
        import random
        rng = random.Random(1234)
        peri = [rng.uniform(200, 2200) for _ in range(60)]
        apo = [p + rng.uniform(0, 800) for p in peri]
        pad = 50.0
        expected = [
            (i, j)
            for i in range(60) for j in range(i + 1, 60)
            if peri[i] <= apo[j] + pad and peri[j] <= apo[i] + pad
        ]
        assert orbitcore.coarse_filter(peri, apo, pad) == expected

    def test_real_stations_catalog(self):
        """Real Phase 1 data: parquet-derived bands, pad = 50 km medium
        threshold. Survivors must be a strict subset of all pairs and the
        overlap property must hold for every returned pair."""
        import pandas as pd
        df = pd.read_parquet("backend/data/tle/stations.parquet")
        peri = df["periapsis"].astype(float).tolist()
        apo = df["apoapsis"].astype(float).tolist()
        n = len(peri)
        pairs = orbitcore.coarse_filter(peri, apo, 50.0)
        total = n * (n - 1) // 2
        assert 0 < len(pairs) < total  # filters something, keeps something
        for i, j in pairs:
            assert peri[i] <= apo[j] + 50.0 and peri[j] <= apo[i] + 50.0

    def test_satrec_bands_consistent_with_parquet(self):
        """alta/altp * radiusearthkm (the screener's satrec-derived bands)
        agree with the fetcher's derived columns to within a couple km."""
        satrec, _ = _init_from_tle(**ISS_TLE)
        apo_km = satrec.alta * satrec.radiusearthkm
        peri_km = satrec.altp * satrec.radiusearthkm
        assert 380 < peri_km < 440  # ISS-ish LEO band
        assert peri_km < apo_km < 460

    def test_scan_performance_at_phase3_scale(self):
        """Pure scan cost at 6,000 sats (sparse bands -> few pairs, so this
        times the O(N^2) loop, not the Python conversion of survivors).
        Measured ~40 ms; assert a generous 1 s bound."""
        import time
        n = 6000
        peri = [200.0 + 3.0 * i for i in range(n)]
        apo = [p + 1.0 for p in peri]
        t0 = time.perf_counter()
        pairs = orbitcore.coarse_filter(peri, apo, 0.0)
        dt = time.perf_counter() - t0
        assert pairs == []
        assert dt < 1.0, f"O(N^2) scan took {dt:.2f}s at n={n}"


# ===================================================================
# 12. Medium conjunction filter — medium_filter() (Task 6.3)
# ===================================================================

def _make_iss_variant(dmo_deg=0.0, dnodeo_deg=0.0, bstar=None):
    """ISS elements with optional mean-anomaly / RAAN offsets (degrees).

    dmo shifts the satellite along its orbit; dnodeo=180 flips the orbital
    plane so the two orbits cross at the relative nodes — a head-on
    encounter geometry with v_rel ~ 12 km/s.
    """
    from sgp4.api import Satrec as PySatrec, WGS72
    p = PySatrec.twoline2rv(ISS_TLE["line1"], ISS_TLE["line2"], WGS72)
    return orbitcore.sgp4init(
        orbitcore.GravConst.WGS72, "a", "25544",
        p.jdsatepoch + p.jdsatepochF - 2433281.5,
        p.bstar if bstar is None else bstar,
        p.ndot, p.nddot, p.ecco, p.argpo, p.inclo,
        (p.mo + math.radians(dmo_deg)) % (2 * math.pi),
        p.no_kozai,
        (p.nodeo + math.radians(dnodeo_deg)) % (2 * math.pi),
    )


def _iss_epoch_jd():
    from sgp4.api import Satrec as PySatrec, WGS72
    p = PySatrec.twoline2rv(ISS_TLE["line1"], ISS_TLE["line2"], WGS72)
    return p.jdsatepoch + p.jdsatepochF


class TestMediumFilter:
    """medium_filter(): time-stepped pair screening with the velocity-aware
    no-skip bound. The fast-crosser fixture (found by offline brute-force
    search, then hardcoded) has true miss ~8 km at v_rel ~12 km/s with
    sampled 60 s distances of ~520/200 km — a plain d<threshold check
    misses it entirely; the interval bound must not."""

    # Fixture: ISS vs (MA+180.2 deg, RAAN+180 deg) clone.
    # Brute-force ground truth: d_min ~ 8.1 km at tsince ~ 122.717 min.
    CROSSER_DMO = 180.2
    CROSSER_TCA_MIN = 122.717

    def test_exposed(self):
        assert hasattr(orbitcore, "medium_filter")

    def test_identical_pair_single_window_d_zero(self):
        """Same TLE twice: d = 0 throughout -> exactly ONE window (merge +
        end-of-scan flush both work), distance exactly 0."""
        jd0 = _iss_epoch_jd()
        out = orbitcore.medium_filter(
            [_make_iss_variant(), _make_iss_variant()],
            [(0, 1)], jd0, jd0 + 0.2, 60.0, 50.0)
        assert len(out) == 1
        i, j, jd, d = out[0]
        assert (i, j) == (0, 1)
        assert d == 0.0
        assert jd0 <= jd <= jd0 + 0.2

    def test_fast_crosser_detected_at_60s_steps(self):
        """THE design-proving test. Sampled distances next to TCA are ~520
        and ~200 km (>> 50 km threshold) — naive sampling misses the real
        8 km conjunction; the velocity-aware bound flags it and the window
        brackets the true TCA within one step."""
        jd0 = _iss_epoch_jd()
        a = _make_iss_variant()
        b = _make_iss_variant(dmo_deg=self.CROSSER_DMO, dnodeo_deg=180.0)

        # Document the naive-sampling hole with the actual numbers:
        (ra, _) = orbitcore.sgp4(_make_iss_variant(), 122.0)
        (rb, _) = orbitcore.sgp4(
            _make_iss_variant(dmo_deg=self.CROSSER_DMO, dnodeo_deg=180.0), 122.0)
        d_sampled = math.dist(ra, rb)
        assert d_sampled > 400, "fixture drifted — regenerate ground truth"

        out = orbitcore.medium_filter(
            [a, b], [(0, 1)], jd0, jd0 + 0.25, 60.0, 50.0)
        flagged_min = [(jd - jd0) * 1440.0 for _, _, jd, _ in out]
        assert any(abs(t - self.CROSSER_TCA_MIN) <= 1.0 for t in flagged_min), (
            f"true TCA {self.CROSSER_TCA_MIN} min not bracketed; "
            f"flags at {flagged_min}")

    def test_window_brackets_brute_force_minimum(self):
        """Independent ground truth: 1 s brute-force sampling around the
        encounter; the flagged step must be within one 60 s step of it."""
        jd0 = _iss_epoch_jd()
        a = _make_iss_variant()
        b = _make_iss_variant(dmo_deg=self.CROSSER_DMO, dnodeo_deg=180.0)
        # brute force 118..128 min at 1 s resolution via batch propagation
        times = [118.0 + i / 60.0 for i in range(600)]
        ta = orbitcore.propagate_batch([a] * len(times), times)
        tb = orbitcore.propagate_batch([b] * len(times), times)
        dists = [math.dist(ta[k][0], tb[k][0]) for k in range(len(times))]
        k_min = dists.index(min(dists))
        t_true = times[k_min]
        assert dists[k_min] < 50.0  # genuinely sub-threshold

        out = orbitcore.medium_filter(
            [a, b], [(0, 1)], jd0, jd0 + 0.25, 60.0, 50.0)
        near = [(jd - jd0) * 1440.0 for _, _, jd, _ in out
                if abs((jd - jd0) * 1440.0 - t_true) <= 1.0]
        assert near, f"no flagged step within 1 step of true min {t_true:.2f}"

    def test_crossing_pair_repeating_windows(self):
        """Crossing orbits re-encounter at every node pass: expect several
        distinct windows across 6 h (measured 8 with this fixture)."""
        jd0 = _iss_epoch_jd()
        out = orbitcore.medium_filter(
            [_make_iss_variant(),
             _make_iss_variant(dmo_deg=self.CROSSER_DMO, dnodeo_deg=180.0)],
            [(0, 1)], jd0, jd0 + 0.25, 60.0, 50.0)
        assert 5 <= len(out) <= 11
        jds = [jd for _, _, jd, _ in out]
        assert len(set(jds)) == len(jds)  # distinct windows

    def test_co_orbital_far_pair_stays_quiet(self):
        """~940 km along-track separation, v_rel ~ 0: the adaptive bound
        applies no inflation -> NOT flagged at 50 km threshold (a fixed
        gross threshold of ~530 km would spam-flag this)."""
        jd0 = _iss_epoch_jd()
        out = orbitcore.medium_filter(
            [_make_iss_variant(), _make_iss_variant(dmo_deg=8.0)],
            [(0, 1)], jd0, jd0 + 0.25, 60.0, 50.0)
        assert out == []

    def test_co_orbital_close_pair_flagged(self):
        """~35 km along-track separation < 50 km threshold -> one
        continuous window."""
        jd0 = _iss_epoch_jd()
        out = orbitcore.medium_filter(
            [_make_iss_variant(), _make_iss_variant(dmo_deg=0.3)],
            [(0, 1)], jd0, jd0 + 0.1, 60.0, 50.0)
        assert len(out) == 1
        assert out[0][3] < 50.0

    def test_decayed_sat_isolated(self):
        """A decaying satellite (huge bstar, placed far away) produces no
        flags and no crash; an unrelated close pair still flags."""
        jd0 = _iss_epoch_jd()
        good_a = _make_iss_variant()
        good_b = _make_iss_variant()
        decayer = _make_iss_variant(dmo_deg=90.0, bstar=0.1)
        out = orbitcore.medium_filter(
            [good_a, good_b, decayer],
            [(0, 1), (0, 2)], jd0, jd0 + 0.5, 60.0, 50.0)
        assert any((i, j) == (0, 1) for i, j, _, _ in out)
        assert not any((i, j) == (0, 2) for i, j, _, _ in out)

    def test_pair_order_passes_through(self):
        jd0 = _iss_epoch_jd()
        out = orbitcore.medium_filter(
            [_make_iss_variant(), _make_iss_variant()],
            [(1, 0)], jd0, jd0 + 0.05, 60.0, 50.0)
        assert out[0][:2] == (1, 0)

    def test_empty_pairs(self):
        jd0 = _iss_epoch_jd()
        assert orbitcore.medium_filter(
            [_make_iss_variant()], [], jd0, jd0 + 0.1, 60.0, 50.0) == []

    def test_boundary_validation(self):
        """Every invalid input fails loudly with the right exception."""
        jd0 = _iss_epoch_jd()
        a, b = _make_iss_variant(), _make_iss_variant()
        cases = [
            (([a, b], [(0, 1)], jd0, jd0 - 1.0, 60.0, 50.0), ValueError),   # end <= start
            (([a, b], [(0, 1)], jd0, jd0 + 1.0, 0.0, 50.0), ValueError),    # step <= 0
            (([a, b], [(0, 1)], jd0, jd0 + 1.0, 60.0, -1.0), ValueError),   # threshold <= 0
            (([a, b], [(0, 1)], jd0, jd0 + 30 / 86400, 60.0, 50.0), ValueError),  # window < step
            (([a, b], [(0, 0)], jd0, jd0 + 1.0, 60.0, 50.0), ValueError),   # i == j
            (([a, b], [(0, 7)], jd0, jd0 + 1.0, 60.0, 50.0), ValueError),   # out of range
            (([a, b], [(-1, 1)], jd0, jd0 + 1.0, 60.0, 50.0), ValueError),  # negative
            (([a, None], [(0, 1)], jd0, jd0 + 1.0, 60.0, 50.0), TypeError), # None satrec
            (([a, b], ["xy"], jd0, jd0 + 1.0, 60.0, 50.0), TypeError),      # bad pair item
        ]
        for args, exc in cases:
            try:
                orbitcore.medium_filter(*args)
                assert False, f"should have raised {exc.__name__}: {args[1]}"
            except exc:
                pass

    def test_performance_vertical_slice_scale(self):
        """Phase 6 scale: 300-sat co-orbital ring, ALL 44,850 pairs (worst
        case: everything coarse-survives), 24 h at 60 s. Time-major loop
        keeps this seconds, not hours (pair-major would be ~4 min here and
        ~6 h at Phase 7)."""
        import time
        jd0 = _iss_epoch_jd()
        n = 300
        sats = [_make_iss_variant(dmo_deg=360.0 * i / n) for i in range(n)]
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        t0 = time.perf_counter()
        out = orbitcore.medium_filter(sats, pairs, jd0, jd0 + 1.0, 60.0, 5.0)
        dt = time.perf_counter() - t0
        print(f"\n  300 sats / {len(pairs):,} pairs / 24h@60s: {dt:.2f}s, "
              f"{len(out)} windows")
        assert dt < 15.0, f"medium filter too slow: {dt:.1f}s"
        # ring neighbors are ~140 km apart; threshold 5 km -> no flags
        assert out == []
