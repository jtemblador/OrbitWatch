"""
SOCRATES Fetcher — downloads CelesTrak SOCRATES-Plus conjunction predictions,
the validation anchor for our screener (Phase 8).

SOCRATES screens all active payloads vs the full catalog with STK/CAT on the
NORAD SGP4 propagator — the same method we use — 3×/day, 7 days forward, flagging
everything within 5 km at TCA. CelesTrak publishes each full run as a clean
RFC-4180 CSV (one row per conjunction, sorted by min range), so we download that
file and serve top-N / by-name slices from the cached frame — no HTML scraping,
no auth.

CSV columns (verified Jun 24 vs celestrak.org/SOCRATES/socrates-format.php):
    NORAD_CAT_ID_1, OBJECT_NAME_1, DSE_1, NORAD_CAT_ID_2, OBJECT_NAME_2, DSE_2,
    TCA, TCA_RANGE (km), TCA_RELATIVE_SPEED (km/s), MAX_PROB, DILUTION

We keep the objects + DSE (the epoch-match key — days from the GP epoch used to
the TCA) + TCA + range + speed. We drop MAX_PROB / DILUTION: they're Pc-related
and out of scope, and MAX_PROB uses a fixed *assumed* 100/300/100 m covariance,
not measured covariance — so it isn't operational Pc anyway.

⚠ Per CelesTrak's format doc, object 1 / object 2 are **positional, NOT
primary/secondary** ("the first of the two objects ... not what might be
considered the primary"). So don't assume object 1 is the active payload —
either side can be the debris. Filters here check both sides.

Why the bulk CSV over the live query endpoint (table-socrates.php): the query
endpoint caps at MAX≤1000, returns HTML (FORMAT=csv is ignored), and is one
request per slice. The full-run CSV is the complete set in one cached download,
and its useful query *semantics* — a single object's conjunctions (CATNR), or
conjunctions between two groups (NAME=a,b) — are reproduced locally below
(by_catnr / between) with no network, no 1000-row cap, and no scraping.

Usage:
    f = SOCRATESFetcher()
    df = f.fetch()                          # full run (cached, ~8 h TTL)
    closest = f.top_n(50)                   # 50 closest conjunctions
    iss = f.by_catnr(25544)                 # all ISS conjunctions
    sl_vs_flock = f.between("STARLINK", "FLOCK")
"""

import io
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from backend.core.http_fetch import download_text

# CelesTrak publishes the full SOCRATES run as static, sorted CSVs. We use the
# min-range sort (closest first) and re-sort/filter locally as needed. Other
# sorts exist (sort-maxProb/TCA/relSpeed/SSC.csv) but aren't needed.
SOCRATES_CSV_URL = "https://celestrak.org/SOCRATES/sort-minRange.csv"

DATA_DIR = Path(__file__).parent.parent / "data" / "socrates"
CACHE_NAME = "socrates.parquet"

# SOCRATES refreshes ~3×/day → an 8 h cache is always within one cycle.
MIN_FETCH_INTERVAL = timedelta(hours=8)

# Raw CSV column → our snake_case name (objects + DSE + TCA + range + speed).
_COLUMN_MAP = {
    "NORAD_CAT_ID_1": "norad_id_1",
    "OBJECT_NAME_1": "name_1",
    "DSE_1": "dse_1",
    "NORAD_CAT_ID_2": "norad_id_2",
    "OBJECT_NAME_2": "name_2",
    "DSE_2": "dse_2",
    "TCA": "tca",
    "TCA_RANGE": "range_km",
    "TCA_RELATIVE_SPEED": "rel_speed_km_s",
}

# Output column order (status_1/2 are derived from the bracketed name suffix).
_OUTPUT_COLUMNS = [
    "norad_id_1", "name_1", "status_1", "dse_1",
    "norad_id_2", "name_2", "status_2", "dse_2",
    "tca", "range_km", "rel_speed_km_s", "fetch_time",
]

# SOCRATES appends an operational-status tag to the name, e.g.
# "STARLINK-30878 [P]", "WT 1B [+]", "SHIYAN-21 (SY-21) [+]". Match the *trailing*
# bracket only (non-greedy + end-anchor), so an internal "(SY-21)" is left intact.
_NAME_STATUS_RE = r"^(.*?)\s*\[([^\]]*)\]\s*$"


def _split_name_status(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Split a SOCRATES name series into (clean_name, status) columns.

    "STARLINK-30878 [P]" → ("STARLINK-30878", "P"). A name without a bracket
    keeps its (stripped) self and a null status.
    """
    extracted = series.str.extract(_NAME_STATUS_RE)
    name = extracted[0].fillna(series.str.strip())
    status = extracted[1]
    return name, status


class SOCRATESFetcher:
    """Fetches + caches the SOCRATES full-run CSV; serves slice views
    (top_n / by_name / by_catnr / between).

    Mirrors GPFetcher's fetch/serve discipline (Parquet cache, TTL, graceful
    cache-fallback, never overwrite good cache with empty) — see tle_fetcher.py.
    """

    def __init__(self, cache_dir: Path = DATA_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, force: bool = False) -> pd.DataFrame:
        """Return the SOCRATES full run as a clean DataFrame.

        Serves the Parquet cache if it's younger than MIN_FETCH_INTERVAL (SOCRATES
        only updates 3×/day); otherwise downloads the CSV, parses, and caches.
        Falls back to cache on any network/parse failure, and never overwrites a
        good cache with an empty result.
        """
        if not force:
            cached = self._load_if_fresh()
            if cached is not None:
                return cached

        try:
            raw_csv = download_text(SOCRATES_CSV_URL)
            df = self._parse_csv(raw_csv)
            print(f"Fetched SOCRATES run ({len(df)} conjunctions)")
        except Exception as e:
            print(f"SOCRATES fetch failed ({e}), loading cache")
            return self._load_cached_or_fail()

        if df.empty:
            print("  Warning: SOCRATES returned no conjunctions — keeping cache")
            return self._load_cached_or_fail()

        self._cache_to_parquet(df)
        return df

    def top_n(self, n: int) -> pd.DataFrame:
        """The `n` closest conjunctions (smallest miss distance)."""
        return self.fetch().nsmallest(n, "range_km").reset_index(drop=True)

    def by_name(self, substring: str) -> pd.DataFrame:
        """Conjunctions where either object's name contains `substring`
        (case-insensitive, literal) — e.g. by_name("STARLINK")."""
        df = self.fetch()
        hit = (self._contains(df["name_1"], substring)
               | self._contains(df["name_2"], substring))
        return df[hit].reset_index(drop=True)

    def by_catnr(self, norad_id: int) -> pd.DataFrame:
        """All conjunctions involving a specific object (in either position).
        Local equivalent of the SOCRATES `CATNR=<id>` query — e.g. by_catnr(25544)
        for every ISS conjunction. Primary key for 8.2's per-object validation."""
        df = self.fetch()
        hit = (df["norad_id_1"] == norad_id) | (df["norad_id_2"] == norad_id)
        return df[hit].reset_index(drop=True)

    def between(self, name_a: str, name_b: str) -> pd.DataFrame:
        """Conjunctions with one object matching `name_a` and the other `name_b`
        (case-insensitive, literal, either ordering). Local equivalent of the
        SOCRATES pairwise `NAME=a,b` query — e.g. between("STARLINK", "FLOCK")."""
        df = self.fetch()
        a1 = self._contains(df["name_1"], name_a)
        a2 = self._contains(df["name_2"], name_a)
        b1 = self._contains(df["name_1"], name_b)
        b2 = self._contains(df["name_2"], name_b)
        return df[(a1 & b2) | (a2 & b1)].reset_index(drop=True)

    @staticmethod
    def _contains(names: pd.Series, substring: str) -> pd.Series:
        # regex=False: object names carry regex-special chars (e.g. "R/B(1) DEB",
        # "CZ-6A") that would otherwise be mis-interpreted as a pattern.
        return names.str.contains(substring, case=False, na=False, regex=False)

    def load_cached(self) -> pd.DataFrame:
        """Load the previously cached SOCRATES run from Parquet."""
        path = self.cache_dir / CACHE_NAME
        if not path.exists():
            raise FileNotFoundError("No cached SOCRATES data. Run fetch() first.")
        return pd.read_parquet(path)

    # --- internals ---------------------------------------------------------

    def _parse_csv(self, text: str) -> pd.DataFrame:
        """Parse the RFC-4180 SOCRATES CSV into the output schema."""
        raw = pd.read_csv(io.StringIO(text))
        if raw.empty:
            return pd.DataFrame(columns=_OUTPUT_COLUMNS)

        # Fail loudly if the source schema changed (or a non-CSV slipped through):
        # otherwise rename() silently no-ops and a later column access raises a
        # confusing KeyError on a *renamed* name. Name the real culprit instead.
        missing = [c for c in _COLUMN_MAP if c not in raw.columns]
        if missing:
            raise ValueError(
                f"SOCRATES CSV missing expected columns {missing} — format may "
                f"have changed or the response wasn't CSV. Got: {list(raw.columns)}")

        df = raw.rename(columns=_COLUMN_MAP)
        df["name_1"], df["status_1"] = _split_name_status(df["name_1"])
        df["name_2"], df["status_2"] = _split_name_status(df["name_2"])

        df["norad_id_1"] = df["norad_id_1"].astype(int)
        df["norad_id_2"] = df["norad_id_2"].astype(int)
        for col in ("dse_1", "dse_2", "range_km", "rel_speed_km_s"):
            df[col] = df[col].astype(float)
        # SOCRATES TCA is "YYYY-MM-DD HH:MM:SS.fff" UTC.
        df["tca"] = pd.to_datetime(df["tca"], utc=True)

        df["fetch_time"] = datetime.now(timezone.utc)
        return df[_OUTPUT_COLUMNS].copy()

    def _cache_to_parquet(self, df: pd.DataFrame) -> None:
        """Atomic write (temp file + rename) so an interrupted refresh can't
        leave a half-written cache — this is the CI refresh path."""
        path = self.cache_dir / CACHE_NAME
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet", dir=self.cache_dir)
        try:
            os.close(tmp_fd)
            df.to_parquet(tmp_path, index=False)
            Path(tmp_path).replace(path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _load_if_fresh(self) -> pd.DataFrame | None:
        """Return the cache if it's younger than MIN_FETCH_INTERVAL, else None."""
        path = self.cache_dir / CACHE_NAME
        if not path.exists():
            return None

        # Fast path: the file mtime alone tells us if it's stale (avoids reading a
        # 148k-row Parquet just to discard it).
        file_age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc)
        if file_age >= MIN_FETCH_INTERVAL:
            return None

        df = pd.read_parquet(path)
        if df.empty or "fetch_time" not in df.columns:
            return None

        last = pd.Timestamp(df["fetch_time"].iloc[0])
        if pd.isna(last):
            return None
        if last.tzinfo is None:
            last = last.tz_localize("UTC")
        age = datetime.now(timezone.utc) - last.to_pydatetime()
        if age < MIN_FETCH_INTERVAL:
            # No print here: top_n/by_name/by_catnr/between each call fetch(), so a
            # cache-hit message would spam. The download path (fetch) still logs.
            return df
        return None

    def _load_cached_or_fail(self) -> pd.DataFrame:
        try:
            return self.load_cached()
        except FileNotFoundError:
            raise RuntimeError(
                "Cannot fetch SOCRATES and no cached data. Check your connection.")
