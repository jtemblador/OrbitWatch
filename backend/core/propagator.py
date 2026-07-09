"""
Satellite Propagator — Orchestrates the full orbit prediction pipeline.

Pipeline: GPFetcher (cached OMM data) → unit conversion → C++ SGP4 → coordinate transforms

This is the single entry point downstream code (API, frontend) should use
to get satellite positions. It handles all the unit conversions between
CelesTrak's OMM format and what the SGP4 engine expects:

    OMM (degrees, rev/day)  →  SGP4 (radians, rad/min)  →  TEME (km)  →  geodetic (lat/lon/alt)

Usage:
    prop = SatellitePropagator()
    result = prop.get_position("ISS (ZARYA)", datetime.now(timezone.utc))
    # {'name': 'ISS (ZARYA)', 'norad_id': 25544, 'lat': 12.3, 'lon': -45.6, 'alt': 420.1, ...}
"""

import math
import os
import sys
from datetime import datetime, timezone

import pandas as pd

# The orbitcore C++ module (.so) lives in backend/, but the orbitcore/ source
# directory can shadow it as a namespace package. Ensure backend/ is on sys.path
# so the compiled .so is found first.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import orbitcore
from backend.core.coordinate_transforms import teme_to_geodetic
from backend.core.tle_fetcher import GPFetcher

# SGP4 internal unit conversion constant
# xpdotp = 1440 / (2π) ≈ 229.1831180523293
# Converts rev/day to rad/min
XPDOTP = 1440.0 / (2.0 * math.pi)


def omm_to_sgp4_params(row: pd.Series) -> dict:
    """
    Convert one GPFetcher DataFrame row into orbitcore.sgp4init() parameters.

    Handles all unit conversions:
        - Angular elements: degrees → radians
        - Mean motion: rev/day → rad/min
        - Mean motion dot: OMM value ÷ (xpdotp × 1440) — already /2 in OMM format
        - Epoch: ISO 8601 datetime → days since 1949 Dec 31 00:00 UTC

    Args:
        row: A single row from GPFetcher's DataFrame

    Returns:
        Dict with keys matching orbitcore.sgp4init() parameter names
    """
    deg2rad = math.pi / 180.0

    # Epoch conversion: datetime → Julian Date → days since 1949 Dec 31
    epoch_dt = row["epoch"]
    if isinstance(epoch_dt, pd.Timestamp):
        epoch_dt = epoch_dt.to_pydatetime()
    if epoch_dt.tzinfo is None:
        epoch_dt = epoch_dt.replace(tzinfo=timezone.utc)

    jd, jd_frac = orbitcore.jday(
        epoch_dt.year, epoch_dt.month, epoch_dt.day,
        epoch_dt.hour, epoch_dt.minute,
        epoch_dt.second + epoch_dt.microsecond / 1e6,
    )
    # epoch for sgp4init = days since 1949 Dec 31 00:00 UTC
    epoch_days = (jd + jd_frac) - 2433281.5

    return {
        "whichconst": orbitcore.WGS72,
        "opsmode": "a",
        "satnum": str(int(row["norad_cat_id"])),
        "epoch": epoch_days,
        "bstar": float(row["bstar"]),
        "ndot": float(row["mean_motion_dot"]) / (XPDOTP * 1440.0),
        "nddot": float(row["mean_motion_ddot"]) / (XPDOTP * 1440.0 * 1440.0),
        "ecco": float(row["eccentricity"]),
        "argpo": float(row["arg_of_pericenter"]) * deg2rad,
        "inclo": float(row["inclination"]) * deg2rad,
        "mo": float(row["mean_anomaly"]) * deg2rad,
        "no_kozai": float(row["mean_motion"]) / XPDOTP,
        "nodeo": float(row["ra_of_asc_node"]) * deg2rad,
    }


def _row_screening_meta(row: pd.Series, now: datetime) -> dict:
    """Build one conjunction-screening metadata dict from a GP DataFrame row.

    Shared by SatellitePropagator.get_all_satrecs and build_satrecs_and_meta so
    both produce the identical meta shape run_screen expects. epoch_age_days is
    recomputed from `now` (the cached column is stamped at fetch time and goes
    stale); periapsis/apoapsis/period/eccentricity/object_id are GP-derived.
    """
    epoch = row["epoch"]
    if hasattr(epoch, "to_pydatetime"):
        epoch = epoch.to_pydatetime()
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    epoch_age_days = round((now - epoch).total_seconds() / 86400.0, 2)

    return {
        "norad_id": int(row["norad_cat_id"]),
        "name": row["object_name"],
        "object_type": row["object_type"] if pd.notna(row["object_type"]) else "UNKNOWN",
        "epoch_age_days": epoch_age_days,
        "periapsis_km": float(row["periapsis"]),
        "apoapsis_km": float(row["apoapsis"]),
        # 7.2 screening: object_id (intl designator, YYYY-DDDxxx) drives the
        # shared-launch co-located guard; eccentricity + period pick the SFS
        # screening-volume regime (screening_volumes.regime_for).
        "object_id": str(row["object_id"]) if pd.notna(row["object_id"]) else "",
        "eccentricity": float(row["eccentricity"]),
        "period_min": float(row["period"]),
    }


def build_satrecs_and_meta(df: pd.DataFrame) -> tuple[list, list[dict]]:
    """Build index-aligned (satrecs, meta) directly from a GP DataFrame — the
    conjunction-pipeline input, without a propagator instance.

    Standalone (no Satrec cache): one sgp4init per row, fresh each call. Used by
    the SOCRATES validation (Phase 8.2), which screens an ad-hoc set of fetched
    objects. The (satrecs, meta) pair is positionally aligned — the contract
    medium_filter/run_screen rely on (a result index maps to meta[i]'s identity).
    """
    now = datetime.now(timezone.utc)
    satrecs = []
    meta = []
    for i in range(len(df)):
        row = df.iloc[i]
        satrecs.append(orbitcore.sgp4init(**omm_to_sgp4_params(row)))
        meta.append(_row_screening_meta(row, now))
    return satrecs, meta


class SatellitePropagator:
    """
    High-level satellite position predictor.

    Wraps the full pipeline: cached OMM data → C++ SGP4 → coordinate transforms.
    Caches initialized Satrec objects so repeated propagations of the same
    satellite don't re-run sgp4init().
    """

    def __init__(
        self,
        group: str = "stations",
        fetcher: GPFetcher | None = None,
        live: bool = False,
        max_sats: int | None = None,
    ):
        """
        Args:
            group: CelesTrak satellite group to use
            fetcher: Optional GPFetcher instance (creates one if not provided)
            live: if True, fetch fresh GP data on load (GPFetcher.fetch, which
                re-downloads only when the cache is older than CelesTrak's 2 h
                update interval and otherwise serves cache; falls back to cache
                offline) instead of serving a static parquet. Off by default so
                the test suite stays offline/deterministic.
            max_sats: if set, slice the loaded catalog to one dense shell of at
                most this many satellites (slice_to_shell). Keeps the live
                Starlink group tractable until Phase 7.1 lifts the cap.
        """
        self.group = group
        self.fetcher = fetcher or GPFetcher()
        self.live = live
        self.max_sats = max_sats
        self._df: pd.DataFrame | None = None
        self._satrec_cache: dict[int, tuple[orbitcore.Satrec, float, float]] = {}
        # Lookup indexes — built once on first data load, O(1) lookups after that.
        # Critical for scaling: linear scan of 6000 Starlink names on every call is too slow.
        self._name_index: dict[str, int] | None = None  # upper(name) → df row index
        self._norad_index: dict[int, int] | None = None  # norad_id → df row index

    def _ensure_data(self) -> pd.DataFrame:
        """Load OMM data if not already loaded (fresh fetch in live mode)."""
        if self._df is None:
            if self.live:
                # Fresh data at the allowed cadence: fetch() re-downloads only
                # when the cache is >2 h stale, else serves cache; on network
                # failure it falls back to the cached parquet.
                df = self.fetcher.fetch(self.group)
            else:
                df = self.fetcher.load_cached(self.group)

            if self.max_sats is not None:
                from backend.core.tle_fetcher import slice_to_shell
                df = slice_to_shell(df, max_sats=self.max_sats)

            # Positional indexing (get_all_satrecs / _find_satellite use iloc on
            # index labels) requires a clean 0..n-1 RangeIndex.
            self._df = df.reset_index(drop=True)
            self._build_indexes()
        return self._df

    def catalog_size(self) -> int:
        """Number of satellites in the loaded catalog. Cheap after the first
        load (the DataFrame is cached) — used by the API to cap synchronous
        screening before paying for a full O(N^2) screen."""
        return len(self._ensure_data())

    def data_freshness(self) -> dict:
        """Catalog freshness for the API: when we last fetched, and the oldest
        orbital epoch in the set (epoch age drives SGP4 accuracy)."""
        df = self._ensure_data()
        if len(df) == 0:
            return {"last_fetched": None, "max_epoch_age_days": 0.0}

        last_fetched = pd.Timestamp(df["fetch_time"].iloc[0])
        if last_fetched.tzinfo is None:
            last_fetched = last_fetched.tz_localize("UTC")

        epochs = pd.to_datetime(df["epoch"], utc=True)
        now = pd.Timestamp.now(tz="UTC")
        max_age_days = (now - epochs).dt.total_seconds().max() / 86400.0

        return {
            "last_fetched": last_fetched.isoformat(),
            "max_epoch_age_days": round(float(max_age_days), 2),
        }

    def _build_indexes(self):
        """Build O(1) lookup indexes from the DataFrame."""
        df = self._df
        self._name_index = dict(zip(df["object_name"].str.upper(), df.index))
        self._norad_index = dict(zip(df["norad_cat_id"].astype(int), df.index))

    def reload_data(self):
        """Force reload from cache (call after a fresh fetch)."""
        self._df = None
        self._satrec_cache.clear()
        self._name_index = None
        self._norad_index = None

    def _get_satrec(self, row: pd.Series) -> tuple:
        """
        Get or create an initialized Satrec for a satellite.

        Returns:
            (satrec, jd_epoch, jd_epoch_frac) — the Satrec and its epoch JD
        """
        norad_id = int(row["norad_cat_id"])

        if norad_id not in self._satrec_cache:
            params = omm_to_sgp4_params(row)
            satrec = orbitcore.sgp4init(**params)
            self._satrec_cache[norad_id] = (
                satrec, satrec.jdsatepoch, satrec.jdsatepochF
            )

        return self._satrec_cache[norad_id]

    def _find_satellite(self, name: str) -> pd.Series:
        """Look up a satellite by name (case-insensitive). O(1) via index."""
        df = self._ensure_data()
        idx = self._name_index.get(name.upper())
        if idx is None:
            available = list(self._name_index.keys())[:10]
            raise KeyError(
                f"Satellite '{name}' not found in {self.group} group. "
                f"Available ({len(self._name_index)}): {available}..."
            )
        return df.iloc[idx]

    def find_by_norad_id(self, norad_id: int) -> pd.Series:
        """Look up a satellite by NORAD catalog number. O(1) via index."""
        df = self._ensure_data()
        idx = self._norad_index.get(norad_id)
        if idx is None:
            raise KeyError(f"NORAD ID {norad_id} not found in {self.group} group.")
        return df.iloc[idx]

    def get_position(self, name: str, utc_dt: datetime) -> dict:
        """
        Propagate a satellite to a specific time and return its position.

        Args:
            name: Satellite name (e.g., "ISS (ZARYA)")
            utc_dt: UTC datetime to propagate to

        Returns:
            dict with: name, norad_id, lat, lon, alt, pos_ecef, vel_ecef,
                       speed_km_s, timestamp, epoch_age_days
        """
        row = self._find_satellite(name)
        return self._propagate_row(row, utc_dt)

    def get_position_by_norad_id(self, norad_id: int, utc_dt: datetime) -> dict:
        """Propagate a satellite by NORAD ID."""
        row = self.find_by_norad_id(norad_id)
        return self._propagate_row(row, utc_dt)

    def _propagate_row(self, row: pd.Series, utc_dt: datetime) -> dict:
        """Core propagation logic for a single satellite at a single time."""
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)

        satrec, jd_epoch, jd_epoch_frac = self._get_satrec(row)

        # Compute tsince (minutes from epoch)
        jd_now, jd_now_frac = orbitcore.jday(
            utc_dt.year, utc_dt.month, utc_dt.day,
            utc_dt.hour, utc_dt.minute,
            utc_dt.second + utc_dt.microsecond / 1e6,
        )
        tsince = ((jd_now + jd_now_frac) - (jd_epoch + jd_epoch_frac)) * 1440.0

        # Propagate → TEME position/velocity
        pos_teme, vel_teme = orbitcore.sgp4(satrec, tsince)

        # Convert TEME → geodetic
        jd_total = jd_now + jd_now_frac
        geo = teme_to_geodetic(pos_teme, jd_total, vel_teme)

        # Compute speed from TEME velocity (inertial speed)
        vx, vy, vz = vel_teme
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)

        return {
            "name": row["object_name"],
            "norad_id": int(row["norad_cat_id"]),
            "lat": geo["lat"],
            "lon": geo["lon"],
            "alt": geo["alt"],
            "pos_teme": list(pos_teme),
            "pos_ecef": geo["pos_ecef"],
            "vel_ecef": geo["vel_ecef"],
            "speed_km_s": speed,
            "timestamp": utc_dt,
            "epoch_age_days": float(row["epoch_age_days"]),
        }

    def get_all_positions(self, utc_dt: datetime) -> tuple[list[dict], list[dict]]:
        """
        Propagate all satellites in the group to a specific time.

        Args:
            utc_dt: UTC datetime to propagate to

        Returns:
            (results, errors) — results are position dicts (same format as
            get_position), errors are dicts with name, norad_id, reason.
        """
        df = self._ensure_data()
        results = []
        errors = []

        for i in range(len(df)):
            row = df.iloc[i]
            try:
                results.append(self._propagate_row(row, utc_dt))
            except RuntimeError as e:
                errors.append({
                    "name": row["object_name"],
                    "norad_id": int(row["norad_cat_id"]),
                    "reason": str(e),
                })

        return results, errors

    def get_all_satrecs(self) -> tuple[list, list[dict]]:
        """
        Return every satellite's initialized Satrec plus screening metadata,
        index-aligned, for the conjunction pipeline.

        The C++ filters (coarse_filter, medium_filter) work in positional
        indices: medium_filter returns (i, j) into the satrecs list passed
        in, NOT NORAD IDs. So the satrecs list and the metadata list must
        share the same order — both are built from one pass over the cached
        DataFrame here, so callers can map a result index straight back to a
        satellite's identity.

        Satrecs come from the existing per-sat cache (sgp4init is expensive
        and runs at most once per satellite); periapsis/apoapsis are the
        GP-derived altitude columns already in the Parquet (km above
        surface), suitable for coarse_filter directly.

        Returns:
            (satrecs, meta) where meta[k] describes satrecs[k]:
                {norad_id, name, object_type, epoch_age_days,
                 periapsis_km, apoapsis_km}
        """
        df = self._ensure_data()
        now = datetime.now(timezone.utc)
        satrecs = []
        meta = []
        for i in range(len(df)):
            row = df.iloc[i]
            satrec, _, _ = self._get_satrec(row)   # uses the per-sat cache
            satrecs.append(satrec)
            meta.append(_row_screening_meta(row, now))

        return satrecs, meta

    def get_positions_at_times(
        self, name: str, utc_dts: list[datetime]
    ) -> list[dict]:
        """
        Propagate one satellite at multiple times (for ground tracks).

        Args:
            name: Satellite name
            utc_dts: List of UTC datetimes

        Returns:
            List of position dicts ordered by time
        """
        row = self._find_satellite(name)
        return [self._propagate_row(row, dt) for dt in utc_dts]
