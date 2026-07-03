"""
Screening volumes — the industry-standard RTN ellipsoids the conjunction
screener uses to decide whether a close approach counts.

Source: Spaceflight Safety Handbook for Operators (18 & 19 SDS), V1.7, April
2023, Table 3 (HAC vs HAC screening volumes) — the apples-to-apples case for
our SGP4 self-screen. Full notes + page cites in
progress/week6and7_planning/sfs_handbook_summary.md (re-read addendum).

Two things the handbook makes explicit and that drive this module:

  1. The volume is an ELLIPSOID, not a box (p.10, "ellipsoid and covariance
     screenings"). A miss vector (r, t, n) in the primary's RTN frame is
     inside when

         (r / R)^2 + (t / T)^2 + (n / N)^2  <=  1.

  2. The semi-axes are ASYMMETRIC: radial is tight (0.4 km — radial position
     is well-determined), in-track / cross-track are loose (up to 44 / 51 km —
     along-track timing is the dominant uncertainty). A single Euclidean miss
     threshold cannot express that — which is the whole point of Phase 7.2.

The regime is chosen by perigee altitude, gated on eccentricity < 0.25.
"""

from dataclasses import dataclass

# SFS regimes apply to near-circular orbits; above this eccentricity (HEO and
# the like) they don't, and we fall back to the most conservative volume.
ECC_MAX = 0.25


@dataclass(frozen=True)
class ScreeningVolume:
    """One RTN screening ellipsoid (SFS Table 3). Semi-axes in km."""
    name: str
    r_km: float   # radial semi-axis
    t_km: float   # in-track (transverse) semi-axis
    n_km: float   # cross-track (normal) semi-axis

    def circumscribing_radius(self) -> float:
        """Largest semi-axis — the smallest sphere that contains the ellipsoid.

        This is the no-skip gross threshold for the medium filter: every point
        inside the ellipsoid has Euclidean norm <= its largest semi-axis, so a
        Euclidean pre-screen at this radius cannot miss any in-volume event.
        (It is the largest semi-axis, NOT the box corner sqrt(R^2+T^2+N^2) —
        this is an ellipsoid, not a box.)
        """
        return max(self.r_km, self.t_km, self.n_km)

    def contains(self, r_km: float, t_km: float, n_km: float) -> bool:
        """True if the RTN miss vector lies inside (or on) this ellipsoid."""
        return (
            (r_km / self.r_km) ** 2
            + (t_km / self.t_km) ** 2
            + (n_km / self.n_km) ** 2
        ) <= 1.0


# SFS Handbook V1.7, Table 3 — HAC vs HAC screening volumes.
# Regime by perigee altitude (km above surface); all gated on ecc < 0.25.
LEO_1 = ScreeningVolume("LEO 1", 0.4, 44.0, 51.0)   # perigee <= 500 km
LEO_2 = ScreeningVolume("LEO 2", 0.4, 25.0, 25.0)   # 500 < perigee <= 750
LEO_3 = ScreeningVolume("LEO 3", 0.4, 12.0, 12.0)   # 750 < perigee <= 1200
LEO_4 = ScreeningVolume("LEO 4", 0.4, 2.0, 2.0)     # 1200 < perigee <= 2000
# Deep space (GEO/HEO/MEO): 1300 < period < 1800 min, ecc < 0.25, inc < 35 deg.
DEEP_SPACE = ScreeningVolume("Deep Space", 10.0, 10.0, 10.0)

# Best-effort default for orbits this HAC table doesn't cover (ecc >= 0.25, or a
# perigee > 2000 km outside the deep-space period band — i.e. MEO/HEO). This is a
# LEO screener: our catalogs are LEO 1 and never reach here, so we default to it
# rather than special-casing regimes we don't screen. A real MEO/HEO screen would
# use that regime's own SFS volume (e.g. the early-orbit Table 7 HEO = 40/77/107
# km) — out of scope. NB this is a pragmatic default, NOT a no-skip bound: LEO 1's
# radial axis (0.4 km) is tighter than deep space's (10 km), so it would
# under-screen a true deep-space pair radially. Irrelevant for LEO-only data.
_FALLBACK = LEO_1


def regime_for(
    perigee_km: float,
    eccentricity: float,
    period_min: float,
) -> ScreeningVolume:
    """
    Pick the SFS HAC screening volume (Table 3) for one object's orbit.

    Regime is keyed off perigee altitude for LEO and period for deep space, all
    gated on eccentricity < 0.25. Orbits outside the table (MEO/HEO) default to
    LEO 1 — a pragmatic choice for a LEO screener, NOT a no-skip bound (see the
    _FALLBACK note). Our catalogs are LEO, so this isn't exercised in practice.

    Args:
        perigee_km:   perigee altitude above the surface, km (the parquet
                      'periapsis' column).
        eccentricity: orbital eccentricity (the SFS regimes require < 0.25).
        period_min:   orbital period in minutes (selects the deep-space band).

    Returns:
        The applicable ScreeningVolume.
    """
    if eccentricity < ECC_MAX:
        if perigee_km <= 500.0:
            return LEO_1
        if perigee_km <= 750.0:
            return LEO_2
        if perigee_km <= 1200.0:
            return LEO_3
        if perigee_km <= 2000.0:
            return LEO_4
        # perigee > 2000 km: deep space only inside the GEO/HEO/MEO period band.
        if 1300.0 < period_min < 1800.0:
            return DEEP_SPACE
    return _FALLBACK


def is_screenable(
    perigee_km: float,
    eccentricity: float,
    period_min: float,
) -> bool:
    """True iff this orbit is covered by a GENUINE SFS Table-3 volume — i.e.
    `regime_for` returns a real regime, not the LEO-1 `_FALLBACK`.

    Covered: LEO 1-4 (perigee <= 2000 km, ecc < 0.25) and the deep-space band
    (perigee > 2000 km, 1300 < period < 1800 min, ecc < 0.25 — GEO). NOT covered
    (returns False): MEO/GNSS (perigee > 2000 km but period outside the band) and
    HEO (ecc >= 0.25). Those get NO handbook volume, so we DISPLAY them but do not
    screen them with a wrong-size LEO bubble — screening only where the industry
    standard actually applies (Phase 9.2 decision).
    """
    if eccentricity < ECC_MAX:
        if perigee_km <= 2000.0:
            return True
        if 1300.0 < period_min < 1800.0:
            return True
    return False


def gross_threshold_km(volumes: list[ScreeningVolume]) -> float:
    """
    The single Euclidean gross threshold for a whole-catalog medium-filter scan:
    the largest circumscribing radius over every object's regime. Screening the
    medium filter at this radius cannot miss any in-volume conjunction in the
    catalog (each pair's own ellipsoid is contained in its objects' bounding
    spheres, which are <= this).
    """
    if not volumes:
        return 0.0
    return max(v.circumscribing_radius() for v in volumes)
