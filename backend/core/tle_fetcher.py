"""
GP Data Fetcher — Downloads orbital data from CelesTrak using the OMM JSON format.

Uses JSON instead of legacy TLE format because:
- TLE is limited to 5-digit NORAD catalog numbers (cap hit ~July 2026)
- JSON provides ISO 8601 dates (no Y2K epoch ambiguity)
- JSON includes all OMM fields in a structured, parseable format
- CelesTrak recommends migrating away from TLE for new development

The SGP4 propagator still needs TLE-style orbital elements — we extract those
from the JSON fields (mean_motion, eccentricity, inclination, etc.) and pass
them directly to the C++ SGP4 engine via the sgp4 Python library's Satrec.

CelesTrak API docs: https://celestrak.org/NORAD/documentation/gp-data-formats.php
Rate limit: data updates every 2 hours — don't fetch more often than that.
Bandwidth cap: 100 MB/day.

Usage:
    fetcher = GPFetcher()
    df = fetcher.fetch("stations")      # Fetch Phase 1 (space stations)
    df = fetcher.load_cached("stations") # Load from local Parquet cache
"""

import json
import math
import os
import ssl
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# WGS-72 constants (must match SGP4 — see key_information.md)
GM_EARTH = 398600.8  # km³/s² (WGS-72 value used by SGP4)
R_EARTH = 6378.135   # km (WGS-72 equatorial radius)

# CelesTrak GP API base URL — always use .org (not .com)
CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php"

# Predefined satellite groups (Phase 1–4)
CELESTRAK_GROUPS = {
    "stations": f"{CELESTRAK_BASE}?GROUP=stations&FORMAT=json",
    "visual": f"{CELESTRAK_BASE}?GROUP=visual&FORMAT=json",
    "starlink": f"{CELESTRAK_BASE}?GROUP=starlink&FORMAT=json",
    "active": f"{CELESTRAK_BASE}?GROUP=active&FORMAT=json",
}

DATA_DIR = Path(__file__).parent.parent / "data" / "tle"

# Minimum time between fetches (CelesTrak updates every 2 hours)
MIN_FETCH_INTERVAL = timedelta(hours=2)


class GPFetcher:
    def __init__(self, cache_dir: Path = DATA_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, group: str = "stations", force: bool = False) -> pd.DataFrame:
        """
        Fetch GP data from CelesTrak for the given group.

        Skips the network request if cached data is less than 2 hours old
        (CelesTrak only updates every 2 hours — fetching more often wastes
        bandwidth and risks getting IP-blocked).

        Args:
            group: Satellite group ("stations", "visual", "starlink", "active")
            force: Skip cache age check and always fetch from network

        Returns:
            DataFrame with OMM fields + fetch_time
        """
        url = CELESTRAK_GROUPS.get(group)
        if not url:
            raise ValueError(f"Unknown group: {group}. Options: {list(CELESTRAK_GROUPS.keys())}")

        # Check if cached data is fresh enough
        if not force:
            cached = self._load_if_fresh(group)
            if cached is not None:
                return cached

        try:
            raw_json = self._download(url)
            records = json.loads(raw_json)
            print(f"Fetched {group} GP data from CelesTrak ({len(records)} objects)")
        except urllib.error.HTTPError as e:
            # Don't retry 403/404 — CelesTrak will block us
            if e.code in (403, 404):
                print(f"CelesTrak returned HTTP {e.code} — do not retry. Loading cache.")
            else:
                print(f"CelesTrak HTTP error {e.code}, loading cache")
            return self._load_cached_or_fail(group)
        except Exception as e:
            print(f"CelesTrak fetch failed ({e}), loading cache")
            return self._load_cached_or_fail(group)

        df = self._parse_json(records)

        # Don't overwrite good cache with empty results (CelesTrak sometimes
        # returns empty during data refresh windows)
        if df.empty:
            print(f"  Warning: CelesTrak returned empty data for {group}")
            return self._load_cached_or_fail(group)

        self._cache_to_parquet(df, group)
        return df

    def fetch_by_catnr(self, norad_id: int) -> pd.DataFrame:
        """Fetch GP data for a single satellite by NORAD catalog number."""
        url = f"{CELESTRAK_BASE}?CATNR={norad_id}&FORMAT=json"
        try:
            raw_json = self._download(url)
            records = json.loads(raw_json)
            return self._parse_json(records)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ValueError(
                    f"CelesTrak returned HTTP {e.code} for NORAD ID {norad_id} — "
                    "object may not exist or you've been rate-limited."
                )
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to fetch NORAD ID {norad_id}: {e}")

    def load_cached(self, group: str = "stations") -> pd.DataFrame:
        """Load previously cached GP data from Parquet."""
        parquet_path = self.cache_dir / f"{group}.parquet"
        if not parquet_path.exists():
            raise FileNotFoundError(f"No cached data for group '{group}'. Run fetch() first.")
        return pd.read_parquet(parquet_path)

    def _load_if_fresh(self, group: str) -> pd.DataFrame | None:
        """Return cached data if it's less than 2 hours old, else None."""
        parquet_path = self.cache_dir / f"{group}.parquet"
        if not parquet_path.exists():
            return None

        df = pd.read_parquet(parquet_path)
        if "fetch_time" not in df.columns or df.empty:
            return None

        last_fetch_raw = df["fetch_time"].iloc[0]
        last_fetch_ts = pd.Timestamp(last_fetch_raw)
        if last_fetch_ts.tzinfo is None:
            last_fetch_ts = last_fetch_ts.tz_localize("UTC")
        last_fetch_dt: datetime = last_fetch_ts.to_pydatetime()

        age = datetime.now(timezone.utc) - last_fetch_dt
        if age < MIN_FETCH_INTERVAL:
            age_min = int(age.total_seconds()) // 60
            remaining_min = int((MIN_FETCH_INTERVAL - age).total_seconds()) // 60
            print(f"Using cached {group} data (fetched {age_min}m ago, next update in {remaining_min}m)")
            return df

        return None

    def _load_cached_or_fail(self, group: str) -> pd.DataFrame:
        """Try to load cache, raise if no cache exists."""
        try:
            return self.load_cached(group)
        except FileNotFoundError:
            raise RuntimeError(
                f"Cannot fetch from CelesTrak and no cached data for '{group}'. "
                "Check your network connection."
            )

    def _download(self, url: str) -> str:
        """Download data from CelesTrak."""
        # SSL verification disabled: CelesTrak's cert chain triggers SSL errors
        # in some environments. Acceptable for public, non-sensitive orbital data.
        # TODO: Re-enable once CelesTrak cert issues are resolved in our env.
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers={"User-Agent": "OrbitWatch/1.0"})
        # urlopen raises HTTPError for non-2xx status codes automatically
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        return resp.read().decode("utf-8")

    @staticmethod
    def _derive_orbit_params(mean_motion: float, eccentricity: float) -> dict:
        """
        Compute derived orbital parameters from mean motion and eccentricity.

        These aren't provided by gp.php but are essential for conjunction screening:
        - Period determines SGP4 mode (< 225 min = near-Earth, >= 225 min = deep-space)
        - Apoapsis/periapsis enable quick altitude-band filtering to skip
          satellite pairs that can never come close to each other
        """
        # period (minutes) = 1440 min/day ÷ mean_motion rev/day
        period = 1440.0 / mean_motion

        # semimajor axis from Kepler's 3rd law: a = (GM / n²)^(1/3)
        # n must be in rad/s: mean_motion rev/day × 2π / 86400
        n_rad_s = mean_motion * 2.0 * math.pi / 86400.0
        semimajor_axis = (GM_EARTH / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)

        # Apoapsis/periapsis as altitude above Earth surface (km)
        apoapsis = semimajor_axis * (1.0 + eccentricity) - R_EARTH
        periapsis = semimajor_axis * (1.0 - eccentricity) - R_EARTH

        return {
            "period": round(period, 4),
            "semimajor_axis": round(semimajor_axis, 3),
            "apoapsis": round(apoapsis, 3),
            "periapsis": round(periapsis, 3),
        }

    def _parse_json(self, records: list[dict]) -> pd.DataFrame:
        """
        Parse CelesTrak JSON (OMM format) into a DataFrame.

        Captures three categories of data:
        1. SGP4 inputs: orbital elements needed for propagation
        2. Derived orbital params: period, apoapsis, periapsis (computed from elements)
        3. Object metadata: type, size, decay status (for collision risk assessment)

        Skips records that are malformed, decayed, or use non-SGP4 ephemeris types.
        A single bad record will not crash the entire batch.
        """
        now = datetime.now(timezone.utc)
        rows = []
        skipped = 0
        for rec in records:
            try:
                # Validate required SGP4 fields exist
                mean_motion = rec["MEAN_MOTION"]
                eccentricity = rec["ECCENTRICITY"]

                # Skip physically invalid records
                if mean_motion <= 0:
                    skipped += 1
                    continue
                if eccentricity < 0 or eccentricity >= 1:
                    skipped += 1
                    continue

                # Skip non-SGP4 ephemeris types (type 0 = SGP4, others are incompatible)
                if rec.get("EPHEMERIS_TYPE", 0) != 0:
                    skipped += 1
                    continue

                # Skip decayed objects — propagating them produces underground positions
                if rec.get("DECAYED", 0) == 1:
                    skipped += 1
                    continue

                # Parse ISO 8601 epoch
                epoch = datetime.fromisoformat(rec["EPOCH"]).replace(tzinfo=timezone.utc)

                # Epoch staleness: how old is this TLE? Old = inaccurate propagation.
                # SGP4 error: ~1 km at epoch, ~5-10 km/day, ~50-100+ km at 7 days
                epoch_age_days = (now - epoch).total_seconds() / 86400.0

                # Compute derived orbital parameters for screening
                derived = self._derive_orbit_params(mean_motion, eccentricity)

                rows.append({
                    # Identity
                    "object_name": rec["OBJECT_NAME"].strip(),
                    "object_id": rec["OBJECT_ID"],              # International designator
                    "norad_cat_id": rec["NORAD_CAT_ID"],
                    "classification": rec["CLASSIFICATION_TYPE"],

                    # Epoch
                    "epoch": epoch,
                    "epoch_age_days": round(epoch_age_days, 2),  # staleness metric

                    # Orbital elements (SGP4 inputs)
                    "mean_motion": mean_motion,                  # rev/day
                    "eccentricity": eccentricity,
                    "inclination": rec["INCLINATION"],           # degrees
                    "ra_of_asc_node": rec["RA_OF_ASC_NODE"],     # degrees (RAAN)
                    "arg_of_pericenter": rec["ARG_OF_PERICENTER"],  # degrees
                    "mean_anomaly": rec["MEAN_ANOMALY"],         # degrees

                    # Drag and perturbation terms
                    "bstar": rec["BSTAR"],                       # drag term (1/earth radii)
                    "mean_motion_dot": rec["MEAN_MOTION_DOT"],   # 1st derivative of mean motion
                    "mean_motion_ddot": rec["MEAN_MOTION_DDOT"], # 2nd derivative of mean motion

                    # Derived orbital parameters (computed from mean_motion + eccentricity)
                    # Period determines SGP4 mode: < 225 min = near-Earth, >= 225 min = deep-space
                    # Apoapsis/periapsis enable altitude-band filtering for conjunction screening
                    **derived,

                    # Object metadata (from CelesTrak when available, else None)
                    # Populated by sup-gp.php or Space-Track — gp.php omits these
                    "object_type": rec.get("OBJECT_TYPE"),       # PAYLOAD, ROCKET BODY, DEBRIS, UNKNOWN
                    "rcs_size": rec.get("RCS_SIZE"),             # SMALL, MEDIUM, LARGE (radar cross-section)
                    "country_code": rec.get("COUNTRY_CODE"),     # owner/operator country
                    "launch_date": rec.get("LAUNCH_DATE"),       # ISO date
                    "decay_date": rec.get("DECAY_DATE"),         # ISO date (None if still in orbit)

                    # Element set metadata
                    "ephemeris_type": rec["EPHEMERIS_TYPE"],
                    "element_set_no": rec["ELEMENT_SET_NO"],
                    "rev_at_epoch": rec["REV_AT_EPOCH"],

                    # Fetch metadata
                    "fetch_time": now,
                })
            except (KeyError, ValueError, TypeError) as e:
                # Skip malformed records — don't let one bad record kill the batch
                name = rec.get("OBJECT_NAME", "UNKNOWN")
                cat_id = rec.get("NORAD_CAT_ID", "?")
                print(f"  Skipping malformed record: {name} ({cat_id}): {e}")
                skipped += 1

        df = pd.DataFrame(rows)
        if skipped:
            print(f"  Parsed {len(df)} satellites ({skipped} skipped: decayed/invalid/non-SGP4)")
        else:
            print(f"  Parsed {len(df)} satellites")
        return df

    def _cache_to_parquet(self, df: pd.DataFrame, group: str):
        """Save parsed GP data to Parquet for offline use.

        Uses atomic write (temp file + rename) to prevent cache corruption
        if the process is killed mid-write.
        """
        parquet_path = self.cache_dir / f"{group}.parquet"
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".parquet", dir=self.cache_dir
        )
        try:
            os.close(tmp_fd)
            df.to_parquet(tmp_path, index=False)
            Path(tmp_path).replace(parquet_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        print(f"  Cached to {parquet_path}")


if __name__ == "__main__":
    fetcher = GPFetcher()
    df = fetcher.fetch("stations")
    print()
    print(df[["object_name", "norad_cat_id", "object_type", "period", "periapsis", "apoapsis", "decayed"]].to_string(index=False))
