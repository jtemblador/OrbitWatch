"""
Space-Track fetcher — the epoch-matched lever (Phase 8.3, Stage B).

CelesTrak's free `gp.php` serves only the *newest* element set per object, which
has usually rolled past the epoch SOCRATES screened from — so a current-GP run
under-reproduces (epoch drift, Stage A's honest finding). Space-Track's
`gp_history` class holds every historical SGP4 elset (>138 M of them), so we can
pull the *exact* element set whose epoch matches SOCRATES's reported `DSE` and
lift reproduction to its true same-method level.

Auth is cookie-based (NO API key): POST identity+password to /ajaxauth/login,
reuse the session cookie. Credentials come from SPACETRACK_USER / SPACETRACK_PASS
(a gitignored .env or the environment) — never hard-coded, never committed.

Rate limits (per the API guidelines): < 30 requests / minute and < 300 / hour.
We bundle a whole validation slice into ONE comma-delimited gp_history query, so
a run is a handful of requests; a min-interval throttle keeps us well under the
cap. gp_history is immutable, so a fetched window is Parquet-cached forever.

OMM note: Space-Track `format/json` is the same OMM standard CelesTrak emits, so
we hand the records straight to GPFetcher._parse_json — the canonical GP frame,
identical schema to the rest of the pipeline.
"""

import hashlib
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from backend.core.tle_fetcher import GPFetcher

LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
QUERY_BASE = "https://www.space-track.org/basicspacedata/query"
DATA_DIR = Path(__file__).parent.parent / "data" / "spacetrack"

# < 30 req/min → keep ≥ 2.2 s between requests (margin under the cap).
MIN_REQUEST_INTERVAL_S = 2.2

# The API guidelines' one-shot query for "the newest propagable element set for
# all on-orbit objects", widened from >now-10 to >now-30 days so stale-but-listed
# members of the active population still come back (its max element age runs
# ~28 d). ~32k objects / ~36 MB (10.3). Guidelines cap it at once per hour; CI
# only sends it after the CelesTrak fetch has already failed.
CATALOG_QUERY = "/class/gp/decay_date/null-val/epoch/%3Enow-30/format/json"

# A refilled group must cover at least this share of the requested population,
# or the refill is refused — a partial response must not become the catalog.
# Not 1.0: a few objects reenter between the live snapshot and the refill.
MIN_POPULATION_COVERAGE = 0.95
HTTP_TIMEOUT_S = 90
_USER_AGENT = "OrbitWatch/1.0 (validation; contact tembladorjose@yahoo.com)"


class SpaceTrackError(RuntimeError):
    """Login or query failure against Space-Track."""


# Space-Track's format/json encodes EVERY OMM field as a STRING ("MEAN_MOTION":
# "15.49"), unlike CelesTrak which emits real JSON numbers. GPFetcher._parse_json
# does numeric comparisons (mean_motion <= 0, ...), so we coerce the numeric OMM
# fields back to float/int before reusing that parser.
_FLOAT_FIELDS = (
    "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
    "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR",
    "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
)
_INT_FIELDS = (
    "NORAD_CAT_ID", "EPHEMERIS_TYPE", "ELEMENT_SET_NO", "REV_AT_EPOCH", "DECAYED",
)


def _coerce_numeric(rec: dict) -> dict:
    """Cast Space-Track's stringified OMM numerics to float/int (CelesTrak's typing)."""
    out = dict(rec)
    for k in _FLOAT_FIELDS:
        v = out.get(k)
        if isinstance(v, str) and v.strip():
            try:
                out[k] = float(v)
            except ValueError:
                pass
    for k in _INT_FIELDS:
        v = out.get(k)
        if isinstance(v, str) and v.strip():
            try:
                out[k] = int(float(v))
            except ValueError:
                pass
    return out


class SpaceTrackFetcher:
    """Authenticated Space-Track client for gp / gp_history pulls."""

    def __init__(self, user: str | None = None, password: str | None = None,
                 cache_dir: Path = DATA_DIR):
        self._user = user or os.environ.get("SPACETRACK_USER")
        self._pass = password or os.environ.get("SPACETRACK_PASS")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session: requests.Session | None = None
        self._last_request = 0.0
        self._parser = GPFetcher()  # reuse the canonical OMM-JSON parser

    # -- auth ---------------------------------------------------------------

    def login(self) -> requests.Session:
        """Establish a session cookie. Raises SpaceTrackError on bad creds."""
        if not self._user or not self._pass:
            raise SpaceTrackError(
                "Space-Track credentials not set. Put SPACETRACK_USER and "
                "SPACETRACK_PASS in a .env file (gitignored) or export them in "
                "your shell, then re-run.")
        s = requests.Session()
        s.headers.update({"User-Agent": _USER_AGENT})
        try:
            r = s.post(LOGIN_URL,
                       data={"identity": self._user, "password": self._pass},
                       timeout=HTTP_TIMEOUT_S)
        except requests.exceptions.RequestException as e:
            raise SpaceTrackError(f"Could not reach Space-Track to log in: {e}")
        if r.status_code == 401 or "failed" in r.text.lower():
            raise SpaceTrackError(
                "Space-Track login rejected — check SPACETRACK_USER / "
                "SPACETRACK_PASS. (If on a VPN, a blocked exit IP can also cause "
                "this; try without the VPN.)")
        if r.status_code != 200 or not s.cookies:
            raise SpaceTrackError(
                f"Unexpected Space-Track login response (HTTP {r.status_code}).")
        self._session = s
        return s

    # -- low-level query ----------------------------------------------------

    def _throttle(self) -> None:
        dt = time.monotonic() - self._last_request
        if dt < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - dt)
        self._last_request = time.monotonic()

    def _query(self, predicate_path: str) -> str:
        """GET a basicspacedata query, re-logging-in once if the session lapsed."""
        if self._session is None:
            self.login()
        url = QUERY_BASE + predicate_path
        for attempt in (1, 2):
            self._throttle()
            assert self._session is not None
            try:
                r = self._session.get(url, timeout=HTTP_TIMEOUT_S)
            except requests.exceptions.RequestException as e:
                raise SpaceTrackError(f"Space-Track query failed: {e}")
            if r.status_code == 401 and attempt == 1:
                self.login()  # session expired — re-auth and retry once
                continue
            if r.status_code >= 400:
                raise SpaceTrackError(
                    f"Space-Track query HTTP {r.status_code}: {url}")
            return r.text
        raise SpaceTrackError("Space-Track query failed after re-login.")

    def _parse(self, raw_json: str) -> pd.DataFrame:
        records = json.loads(raw_json) if raw_json.strip() else []
        if not records:
            return pd.DataFrame()
        # Space-Track JSON is all-strings → coerce numerics to match CelesTrak,
        # then reuse the canonical OMM-JSON parser (identical GP frame schema).
        return self._parser._parse_json([_coerce_numeric(r) for r in records])

    # -- public fetches -----------------------------------------------------

    def fetch_latest(self, norad_ids) -> pd.DataFrame:
        """Current elset (class/gp) for each id — a Space-Track-sourced
        alternative to CelesTrak for the current-GP baseline (one bulk request).
        Not cached: 'latest' changes as new elsets are published."""
        ids = _sorted_ids(norad_ids)
        if not ids:
            return pd.DataFrame()
        id_csv = ",".join(str(i) for i in ids)
        path = (f"/class/gp/NORAD_CAT_ID/{id_csv}"
                f"/orderby/{urllib.parse.quote('NORAD_CAT_ID asc')}/format/json")
        return self._parse(self._query(path))

    def fetch_catalog(self) -> pd.DataFrame:
        """Newest elset for every on-orbit object — ONE bulk class/gp request
        (CATALOG_QUERY). Not cached: callers keep only the slice they need."""
        return self._parse(self._query(CATALOG_QUERY))

    def fetch_history(self, norad_ids, epoch_start: datetime,
                      epoch_end: datetime, force: bool = False) -> pd.DataFrame:
        """All historical elsets (class/gp_history) for `norad_ids` whose EPOCH
        falls in [epoch_start, epoch_end]. One comma-delimited query for the whole
        set. Immutable → Parquet-cached forever (keyed by ids + window)."""
        ids = _sorted_ids(norad_ids)
        if not ids:
            return pd.DataFrame()
        e0 = epoch_start.astimezone(timezone.utc).strftime("%Y-%m-%d")
        e1 = epoch_end.astimezone(timezone.utc).strftime("%Y-%m-%d")

        cache = self.cache_dir / f"gphist_{_cache_key(ids, e0, e1)}.parquet"
        if cache.exists() and not force:
            # The cached frame's fetch_time/epoch_age_days are from first fetch and
            # thus stale, but harmless: matching keys on absolute `epoch`, and
            # _attach_epoch_ok recomputes element age at TCA from it.
            return pd.read_parquet(cache)

        id_csv = ",".join(str(i) for i in ids)
        path = (f"/class/gp_history/NORAD_CAT_ID/{id_csv}"
                f"/EPOCH/{e0}--{e1}"
                f"/orderby/{urllib.parse.quote('EPOCH asc')}/format/json")
        df = self._parse(self._query(path))
        if not df.empty:
            df.to_parquet(cache, index=False)
        return df


class BulkGPAdapter:
    """Serve a pre-fetched GP frame through compare_against_socrates's
    `fetch_by_catnr(nid)` seam — so the orchestrator runs UNCHANGED against
    historical (or Space-Track-current) elements instead of live CelesTrak.

    With `epoch_targets` (gp_history): returns each object's elset whose epoch is
    nearest the epoch SOCRATES used (TCA − DSE) — the epoch-matched lever.
    Without targets (class/gp): returns each object's single latest elset.
    """

    def __init__(self, gp_df: pd.DataFrame, epoch_targets: dict | None = None):
        self._df = gp_df
        self._targets = {int(k): v for k, v in (epoch_targets or {}).items()}
        # O(1) per-object lookup instead of re-scanning the frame each call.
        if gp_df.empty:
            self._by_id = {}
        else:
            df = gp_df.copy()
            df["_epoch_utc"] = pd.to_datetime(df["epoch"], utc=True)
            self._by_id = dict(tuple(df.groupby("norad_cat_id")))

    def fetch_by_catnr(self, norad_id: int) -> pd.DataFrame:
        nid = int(norad_id)
        rows = self._by_id.get(nid)
        if rows is None or rows.empty:
            return pd.DataFrame()
        target = self._targets.get(nid)
        if target is not None:
            t = pd.Timestamp(target)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            ages = (rows["_epoch_utc"] - t).abs()
            pick = rows.loc[[ages.idxmin()]]
        else:
            pick = rows.loc[[rows["_epoch_utc"].idxmax()]]  # latest
        return pick.drop(columns="_epoch_utc").reset_index(drop=True)


def refill_group_cache(group: str, norad_ids, st: SpaceTrackFetcher | None = None,
                       gp: GPFetcher | None = None,
                       min_coverage: float = MIN_POPULATION_COVERAGE,
                       labels: dict | None = None) -> pd.DataFrame:
    """Refill a GPFetcher group cache from Space-Track, keeping the group's
    population — the CelesTrak-outage fallback (10.10; 10.3 ran it by hand).

    Space-Track has no "active" group, so the caller supplies the population:
    CI passes the NORAD ids of the snapshot that is already live. Objects
    launched since that snapshot are missed for this one run. The cache is
    written with a fresh fetch_time, so the next GPFetcher.fetch(group) uses it
    instead of retrying CelesTrak.

    `labels` ({norad_id: (name, type)}, e.g. from population_from_snapshot)
    overrides Space-Track's names/types with the population source's: the two
    name 16% of the active catalog differently ("TIANHE" vs "CSS (TIANHE)") and
    Space-Track types ~600 of its payloads UNKNOWN, which would make search,
    station grouping and type filters flicker for one run. Elements are never
    touched.

    Raises SpaceTrackError (and writes nothing) on an empty population or when
    fewer than `min_coverage` of the ids come back.
    """
    ids = set(_sorted_ids(norad_ids))
    if not ids:
        raise SpaceTrackError("Refusing to refill from an empty population.")
    cat = (st or SpaceTrackFetcher()).fetch_catalog()
    df = cat[cat["norad_cat_id"].isin(ids)] if not cat.empty else cat
    if not df.empty:
        # class/gp is already newest-per-object; dedupe defensively anyway.
        df = (df.sort_values("epoch").drop_duplicates("norad_cat_id", keep="last")
                .reset_index(drop=True))
    if len(df) < min_coverage * len(ids):
        raise SpaceTrackError(
            f"Space-Track returned {len(df)} of {len(ids)} requested objects "
            f"(< {min_coverage:.0%}) — refusing to refill '{group}' from a "
            f"partial catalog.")
    if labels:
        df = df.copy()
        for col, i in (("object_name", 0), ("object_type", 1)):
            keep = df[col]
            df[col] = [(labels[n][i] or k) if n in labels else k
                       for n, k in zip(df["norad_cat_id"], keep)]
    (gp or GPFetcher())._cache_to_parquet(df, group)
    return df


def population_from_snapshot(path) -> dict:
    """{NORAD id: (OBJECT_NAME, OBJECT_TYPE)} for every satellite in a
    snapshot.json — the population AND its labels for refill_group_cache."""
    with open(path, encoding="utf-8") as f:
        snap = json.load(f)
    return {int(s["NORAD_CAT_ID"]): (s.get("OBJECT_NAME"), s.get("OBJECT_TYPE"))
            for s in snap.get("satellites") or []}


def _sorted_ids(norad_ids) -> list[int]:
    return sorted({int(n) for n in norad_ids})


def _cache_key(ids: list[int], e0: str, e1: str) -> str:
    raw = f"{','.join(map(str, ids))}|{e0}|{e1}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


if __name__ == "__main__":
    import sys

    # `python -m backend.core.spacetrack_fetcher refill <group> <snapshot.json>`
    # refills the <group> cache from Space-Track over the population in
    # <snapshot.json> — what deploy.yml runs when the CelesTrak fetch fails.
    # Exit 1 on any failure so the workflow can fall back further.
    if len(sys.argv) == 4 and sys.argv[1] == "refill":
        try:
            pop = population_from_snapshot(sys.argv[3])
            df = refill_group_cache(sys.argv[2], pop.keys(), labels=pop)
        except (SpaceTrackError, OSError, ValueError, KeyError) as e:
            print(f"Space-Track refill failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"refilled {sys.argv[2]} from Space-Track: {len(df)} of {len(pop)} objects")
    else:
        print("usage: python -m backend.core.spacetrack_fetcher refill <group> <snapshot.json>",
              file=sys.stderr)
        sys.exit(2)
