"""Frozen offline `stations` catalog for the test suite (Phase 11.3a).

Why: the suite's default dataset is the cached CelesTrak `stations` group, but
the cache (`backend/data/tle/*.parquet`) is gitignored, so on a fresh clone with
no network ~150 tests errored with "No cached data for group 'stations'". CI used
to paper over it by fetching the live group before pytest — which also made the
suite depend on CelesTrak being up and on whatever the live group held that day.

What: `tests/fixtures/stations_omm.json` holds 28 real OMM records (raw CelesTrak
gp.php shape) cut from an archived pre-10.10 snapshot on the `data` branch — see
the file's own `source`/`selection` fields. `seed_cache(dir)` parses them through
the REAL `GPFetcher._parse_json` and writes `stations.parquet` exactly as a live
fetch would, except that `fetch_time` / `epoch_age_days` are pinned to the
capture time so the parquet is byte-for-byte deterministic.

Time: a frozen catalog ages, so tests that used to propagate at the wall clock
propagate at `catalog_now()` instead — the catalog's own fetch time, i.e. "now"
as it was when the elements were current (~1 day after the ISS epoch here). With
a live cache that is within hours of the real now, so the tests mean the same
thing they always did; with the fixture they stay valid forever.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stations_omm.json"
GROUP = "stations"


def load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def captured_at() -> datetime:
    """When the fixture's records were fetched (the archived snapshot's
    generated_at), as an aware UTC datetime."""
    ts = load_fixture()["captured_at"].replace("Z", "+00:00")
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def seed_cache(cache_dir: Path) -> Path:
    """Write `<cache_dir>/stations.parquet` from the fixture via the real
    parser. Returns the parquet path."""
    from backend.core.tle_fetcher import GPFetcher

    fetcher = GPFetcher(cache_dir=Path(cache_dir))
    df = fetcher._parse_json(load_fixture()["records"])
    t0 = captured_at()
    # Pin the two wall-clock-derived columns to the capture time so the cache
    # is deterministic and reads as "fetched then" (stale → a live fetch()
    # would re-download rather than serve the fixture as fresh).
    df["fetch_time"] = t0
    df["epoch_age_days"] = [
        round((t0 - e).total_seconds() / 86400.0, 2) for e in df["epoch"]]
    fetcher._cache_to_parquet(df, GROUP)
    return Path(cache_dir) / f"{GROUP}.parquet"


def catalog_now(group: str = GROUP) -> datetime:
    """The loaded catalog's fetch time — the test suite's stand-in for "now".

    Reads whichever cache the suite is pointed at (the frozen fixture by
    default; the live cache under ORBITWATCH_TEST_LIVE_CACHE=1)."""
    from backend.core.tle_fetcher import GPFetcher

    df = GPFetcher().load_cached(group)
    ts = pd.Timestamp(df["fetch_time"].iloc[0])
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def catalog_now_iso(group: str = GROUP) -> str:
    """`catalog_now()` as a query-string-safe ISO string (Z suffix — a `+`
    in a query string decodes to a space)."""
    return catalog_now(group).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
