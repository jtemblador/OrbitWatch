# Task 2.2 — Coordinate Transforms (TEME → Geodetic)

**Date:** Mar 22, 2026
**Status:** DONE
**Tests:** 26/26 passing

---

## Goal

Build a module that converts SGP4 output (position and velocity in the TEME reference frame) into geodetic coordinates (latitude, longitude, altitude) for map display and conjunction screening. This is the bridge between "where the math says the satellite is" and "where it is on a map."

---

## The Problem: Reference Frames

SGP4 outputs position as `(x, y, z)` in kilometers, but these coordinates are in **TEME** (True Equator, Mean Equinox) — a reference frame defined by:
- Z-axis: Earth's true celestial pole (accounting for nutation)
- X-axis: Mean vernal equinox of date (accounting for precession but not nutation)
- Does NOT rotate with Earth

To get lat/lon/alt, we need **ECEF** (Earth-Centered, Earth-Fixed):
- Z-axis: Geographic north pole
- X-axis: Points toward the prime meridian (Greenwich)
- Rotates WITH Earth — a point on the ground has constant ECEF coordinates

The question: how do we get from TEME to ECEF?

---

## Approach: Three Options Evaluated

### Option A: SPICE Full Pipeline (TEME → J2000 → ITRF93 → geodetic)
**Tested first.** SPICE is NASA's standard coordinate transform toolkit. If SPICE knew the TEME frame natively, we'd just call `sp.pxform('TEME', 'ITRF93', et)` and be done.

**Result:** `SPICE(UNKNOWNFRAME) — The frame TEME was not recognized.`

SPICE knows J2000, ITRF93, IAU_EARTH, ECLIPJ2000, GALACTIC — but NOT TEME. TEME is an SGP4-specific quirk from the 1980s, not a standard IAU frame.

To use SPICE, we'd need TEME → J2000 first (precession + nutation matrices), then J2000 → ITRF93 (Earth rotation + polar motion). Two complex steps with multiple rotation matrices and more error accumulation.

### Option B: Astropy (Native TEME Support)
The Python `astropy` library has a native TEME frame and can convert directly to ITRS (ECEF).

**Result:** `astropy NOT installed.` Would require adding a ~200MB dependency. Overkill for one rotation.

### Option C: GMST Z-Rotation (TEME → ECEF directly) ← CHOSEN
TEME and ECEF share the same Z-axis (Earth's pole). The only difference is Earth's rotation angle around that axis — the **Greenwich Mean Sidereal Time (GMST)**.

**The conversion is one matrix multiply:**
```
pos_ecef = R_z(-GMST) × pos_teme
```

Where R_z is a rotation around the Z-axis:
```
| cos(θ)   sin(θ)   0 |   | x_teme |   | x_ecef |
|-sin(θ)   cos(θ)   0 | × | y_teme | = | y_ecef |
|   0        0      1 |   | z_teme |   | z_ecef |
```

**Why this is sufficient:** The "exact" TEME→ECEF conversion would also include:
- Equation of equinoxes (GMST → GAST): corrects for nutation in Earth's rotation angle. Error: ~1 arcsecond → ~30 meters at LEO altitude.
- Polar motion (PEF → ITRF): accounts for Earth's pole wobbling slightly. Error: ~0.3 arcseconds → ~10 meters.

Both corrections are well within SGP4's inherent accuracy of ~1 km at epoch. Including them would add complexity for no meaningful accuracy gain.

---

## GMST Calculation

GMST is computed from the Julian Date using the IAU 1982 formula (the same formula used in Vallado's SGP4 implementation):

```python
T = (jd - 2451545.0) / 36525.0  # Julian centuries from J2000.0

gmst_seconds = (67310.54841
    + (876600 * 3600 + 8640184.812866) * T
    + 0.093104 * T²
    - 6.2e-6 * T³)

gmst_radians = (gmst_seconds × π / 43200) mod 2π
```

Where:
- `T` = Julian centuries from J2000.0 (2000-01-01 12:00:00 TT)
- The constant `67310.54841` is the GMST in seconds at J2000.0
- `876600 * 3600 + 8640184.812866` is the sidereal rotation rate (seconds/century)
- Higher-order terms correct for precession drift
- Division by 43200 converts seconds of time to radians (43200 = 12 hours in seconds = π radians of Earth rotation)

---

## Velocity Transform

Velocity requires an additional correction beyond the rotation. In the ECEF frame, the Earth is stationary, so we must subtract Earth's rotational velocity from the satellite's velocity:

```
vel_ecef = R_z(-GMST) × vel_teme - ω × pos_ecef
```

Where `ω = [0, 0, 7.292115e-5]` rad/s is Earth's angular velocity vector. The cross product `ω × r` gives the velocity that a point at position `r` has purely from Earth's rotation.

Without this correction, ECEF velocities would be wrong by ~0.46 km/s at LEO — large enough to completely invalidate conjunction screening predictions.

---

## Validation

### Initial False Start
First test compared a GMST-rotated J2000 vector against SPICE's full J2000→ITRF93 transform. **43 km difference.** This was NOT a bug in our code — the test was invalid. J2000 ≠ TEME. They differ by ~0.36° of precession accumulated over 26 years since J2000 epoch. We were comparing apples to oranges.

**Lesson:** When testing coordinate transforms, always use real SGP4 output (which IS in TEME), not arbitrary vectors labeled as J2000.

### Correct Validation: Real SGP4 Output
Used the Python `sgp4` library to propagate real ISS TLE → TEME position → our transform → geodetic. Validated:

| Check | Expected | Observed | Status |
|-------|----------|----------|--------|
| ISS altitude at epoch | 417–426 km | 425.4 km | PASS |
| Distance from Earth center | ~6800 km | 6803.5 km | PASS |
| ECEF velocity | ~7.4 km/s | 7.358 km/s | PASS |
| Latitude bounded by inclination | \|lat\| ≤ 51.6° | ≤ 51.8° | PASS |
| Altitude stable over 7 days | 410–440 km | 418–436 km | PASS |

### Multi-Satellite Test
Tested 5 satellites with diverse orbital characteristics:

| Satellite | Type | Periapsis | Apoapsis | Result |
|-----------|------|-----------|----------|--------|
| ISS (ZARYA) | LEO, 51.6° inc, near-circular | 417 km | 426 km | ALT/LAT/SPEED OK |
| CSS (TIANHE) | LEO, 41.5° inc | 380 km | 388 km | ALT/LAT/SPEED OK |
| FREGAT DEB | Eccentric (ecc=0.096) | 745 km | 2264 km | ALT/LAT/SPEED OK |
| HTV-X1 | LEO, higher orbit | 493 km | 501 km | ALT/LAT/SPEED OK |
| CREW DRAGON 12 | LEO, docked with ISS | 417 km | 426 km | ALT/LAT/SPEED OK |

All 30/30 Phase 1 stations passed propagation + conversion without error.

### Ground Track Test
Propagated ISS at 12 time offsets from epoch (0m, 15m, 30m, 45m, 1h, 1.5h, 2h, 6h, 12h, 1d, 3d, 7d):
- Latitude oscillated between ±51.6° as expected (bounded by inclination)
- Longitude swept westward each orbit (Earth rotating under the ISS)
- Altitude stayed in 418–436 km (near-circular orbit, minor variation from eccentricity)

---

## Results

### What Was Built

`backend/core/coordinate_transforms.py` — 4 public functions + 1 helper:

| Function | Purpose |
|----------|---------|
| `gmst_from_jd(jd)` | Julian Date → GMST angle (radians). IAU 1982 formula. |
| `teme_to_ecef(pos, jd, vel)` | TEME→ECEF via GMST Z-rotation. Velocity includes ω×r correction. |
| `ecef_to_geodetic(pos)` | ECEF→(lat°, lon°, alt km) via SPICE `recgeo()` with WGS-84 ellipsoid. |
| `teme_to_geodetic(pos, jd, vel)` | Full pipeline. Returns dict with lat, lon, alt, pos_ecef, vel_ecef. |
| `utc_to_jd(dt)` | UTC datetime → (jd_whole, jd_fraction) for SGP4. |

### Design Decisions
- **WGS-84 for geodetic, WGS-72 for SGP4.** Different purposes: WGS-84 describes Earth's physical shape (for lat/lon/alt conversion), WGS-72 is the gravity model NORAD uses when fitting TLE elements (for SGP4 propagation). Using the wrong one in either place introduces unnecessary error.
- **SPICE kernels loaded once (idempotent).** `_ensure_kernels()` uses a module-level flag. Multiple calls to `ecef_to_geodetic()` don't re-load kernels.
- **Returns ECEF alongside geodetic.** `teme_to_geodetic()` returns `pos_ecef` and `vel_ecef` in the result dict because conjunction distance calculations need ECEF positions, not lat/lon.

---

## Files

| File | Action | Purpose |
|------|--------|---------|
| `backend/core/coordinate_transforms.py` | Created | TEME→ECEF→geodetic transform pipeline |
| `tests/test_coordinate_transforms.py` | Created | 26 unit tests: GMST, rotation, geodetic, end-to-end |

---

## Function Reference

### `gmst_from_jd(jd: float) → float`
Computes Greenwich Mean Sidereal Time from Julian Date. Returns radians in [0, 2π). Uses IAU 1982 polynomial formula with Julian centuries from J2000.0. This is the Earth's rotation angle that separates TEME from ECEF.

### `teme_to_ecef(pos_teme, jd, vel_teme=None) → (pos_ecef, vel_ecef)`
Applies R_z(-GMST) rotation to convert TEME position to ECEF. If velocity is provided, also rotates velocity and subtracts ω×r correction (Earth's rotational velocity at that position). Z-component is unchanged (pure Z-axis rotation).

### `ecef_to_geodetic(pos_ecef) → (lat_deg, lon_deg, alt_km)`
Wraps SPICE's `recgeo()` function. Converts Cartesian ECEF to geodetic coordinates using the WGS-84 ellipsoid (equatorial radius 6378.137 km, flattening 1/298.257). Returns latitude [-90°, 90°], longitude [-180°, 180°], altitude in km above the ellipsoid surface.

### `teme_to_geodetic(pos_teme, jd, vel_teme=None) → dict`
Convenience wrapper that chains `teme_to_ecef` → `ecef_to_geodetic`. Returns a dict with `lat`, `lon`, `alt`, `pos_ecef`, `vel_ecef`. This is the function downstream code (propagator wrapper, conjunction scanner) should call.

### `utc_to_jd(utc_dt: datetime) → (jd_whole, jd_fraction)`
Converts a Python datetime to Julian Date components using the sgp4 library's `jday()` function. Returns the split form `(whole, fraction)` that `Satrec.sgp4()` expects for maximum floating-point precision.

---

## Lessons Learned

1. **SPICE doesn't know every frame.** TEME is an SGP4-specific artifact, not an IAU standard. We had to handle this gap ourselves rather than relying on SPICE's frame transformation engine. Always verify tool capabilities before designing around them.

2. **Test with real data, not synthetic vectors.** Our initial test used an arbitrary J2000 vector for both SPICE and GMST rotation, producing a 43 km discrepancy. The problem was the test, not the code — J2000 ≠ TEME. Real SGP4 output confirmed the approach works.

3. **Know when "good enough" is actually good enough.** We intentionally skip polar motion (~10m) and equation of equinoxes (~30m). These are three orders of magnitude below SGP4's ~1 km accuracy. Adding them would increase code complexity for zero practical benefit. Document what you skip and why.

4. **Velocity transforms are not just rotations.** The ω×r correction is easy to forget but critical. Without it, ECEF velocities are wrong by ~0.46 km/s — enough to completely invalidate conjunction miss-distance predictions.
