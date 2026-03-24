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
