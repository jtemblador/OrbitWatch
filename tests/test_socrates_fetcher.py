#!/usr/bin/env python3
"""Tests for backend/core/socrates_fetcher.py — the SOCRATES-Plus conjunction
fetcher (Task 8.1, the Phase-8 validation anchor).

Offline/deterministic: every test drives the parser from a saved real CSV
fixture (`fixtures/socrates_sample.csv`, the first 20 conjunctions of an actual
sort-minRange.csv run) and mocks the network. The one live-fetch test is skipped
by default (mirrors test_gp_fetcher's hygiene).
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.socrates_fetcher import (  # noqa: E402
    SOCRATESFetcher,
    _split_name_status,
)

FIXTURE = Path(__file__).parent / "fixtures" / "socrates_sample.csv"
FIXTURE_TEXT = FIXTURE.read_text()

# Patch target: the name as imported into the module under test (see the
# core/ vs backend.core/ dual-instance note in tle_fetcher tests).
_DL = "core.socrates_fetcher.download_text"


def _fetcher():
    return SOCRATESFetcher(cache_dir=Path(tempfile.mkdtemp()))


class TestParse:
    """_parse_csv(): the RFC-4180 SOCRATES CSV → clean typed schema."""

    def setup_method(self):
        self.df = _fetcher()._parse_csv(FIXTURE_TEXT)

    def test_shape_and_columns(self):
        assert len(self.df) == 20
        assert list(self.df.columns) == [
            "norad_id_1", "name_1", "status_1", "dse_1",
            "norad_id_2", "name_2", "status_2", "dse_2",
            "tca", "range_km", "rel_speed_km_s", "fetch_time",
        ]

    def test_dtypes(self):
        assert self.df["norad_id_1"].dtype == "int64"
        assert self.df["norad_id_2"].dtype == "int64"
        for col in ("dse_1", "dse_2", "range_km", "rel_speed_km_s"):
            assert self.df[col].dtype == "float64"
        # TCA is timezone-aware UTC (essential for the epoch-matched comparison).
        assert isinstance(self.df["tca"].dtype, pd.DatetimeTZDtype)
        assert str(self.df["tca"].dt.tz) == "UTC"

    def test_drops_pc_columns(self):
        # MAX_PROB / DILUTION are Pc-related and deliberately out of scope.
        assert "max_prob" not in self.df.columns
        assert "dilution" not in self.df.columns

    def test_first_row_values(self):
        r = self.df.iloc[0]
        assert r["norad_id_1"] == 58359 and r["name_1"] == "STARLINK-30878"
        assert r["norad_id_2"] == 66343 and r["name_2"] == "STARLINK-35467"
        assert r["range_km"] == 0.016 and r["rel_speed_km_s"] == 9.817
        assert r["dse_1"] == 2.032 and r["dse_2"] == 1.989  # the epoch-match key

    def test_dse_is_present_for_both_objects(self):
        # DSE (days since epoch) drives epoch-matching in 8.2 — both must survive.
        assert self.df["dse_1"].notna().all()
        assert self.df["dse_2"].notna().all()

    def test_tca_parses_to_exact_utc(self):
        # 8.2's epoch-matched comparison aligns OUR screen window to this TCA, so
        # it must parse byte-exactly (incl. the .fff milliseconds), as UTC.
        # Row 0 raw TCA = "2026-06-27 20:11:04.572".
        assert self.df.iloc[0]["tca"] == pd.Timestamp(
            "2026-06-27 20:11:04.572", tz="UTC")

    def test_empty_csv_returns_empty_schema(self):
        header = FIXTURE_TEXT.splitlines()[0] + "\n"
        out = _fetcher()._parse_csv(header)
        assert out.empty
        assert "norad_id_1" in out.columns

    def test_unexpected_columns_raise_clearly(self):
        # A format change / non-CSV response must fail loudly naming the real
        # culprit (the raw header), not a cryptic KeyError on a renamed column.
        bad = "FOO,BAR\n1,2\n"
        with pytest.raises(ValueError, match="missing expected columns"):
            _fetcher()._parse_csv(bad)


class TestNameStatusSplit:
    """The bracketed operational-status suffix on SOCRATES names."""

    def test_basic_split(self):
        name, status = _split_name_status(pd.Series(["STARLINK-30878 [P]"]))
        assert name.iloc[0] == "STARLINK-30878" and status.iloc[0] == "P"

    def test_statuses_seen_in_real_data(self):
        s = pd.Series(["A [+]", "B [-]", "C [?]", "D [P]"])
        _, status = _split_name_status(s)
        assert list(status) == ["+", "-", "?", "P"]

    def test_internal_parens_preserved(self):
        # Only the TRAILING bracket is the status — "SHIYAN-21 (SY-21) [+]"
        # must keep the (SY-21), not eat it.
        name, status = _split_name_status(pd.Series(["SHIYAN-21 (SY-21) [+]"]))
        assert name.iloc[0] == "SHIYAN-21 (SY-21)" and status.iloc[0] == "+"

    def test_contains_is_literal_not_regex(self):
        # Name filters must treat the needle literally — object names carry
        # regex-special chars (e.g. "R/B(1) DEB"). As a regex, "R/B(1)" would
        # interpret "(1)" as a group and fail to match the literal string.
        hits = SOCRATESFetcher._contains(
            pd.Series(["R/B(1) DEB", "PAYLOAD A"]), "R/B(1)")
        assert list(hits) == [True, False]

    def test_name_without_bracket(self):
        name, status = _split_name_status(pd.Series(["NO STATUS"]))
        assert name.iloc[0] == "NO STATUS" and pd.isna(status.iloc[0])


class TestSlices:
    """top_n / by_name derive from the cached frame (no extra network)."""

    def test_top_n_is_closest_sorted(self):
        with patch(_DL, return_value=FIXTURE_TEXT):
            top = _fetcher().top_n(5)
        assert len(top) == 5
        # ascending by miss distance, and the global closest first
        assert list(top["range_km"]) == sorted(top["range_km"])
        assert top.iloc[0]["range_km"] == 0.016

    def test_by_name_case_insensitive_either_object(self):
        with patch(_DL, return_value=FIXTURE_TEXT):
            star = _fetcher().by_name("starlink")
        assert len(star) > 0
        # every row has STARLINK in at least one object
        for _, r in star.iterrows():
            assert ("STARLINK" in r["name_1"]) or ("STARLINK" in r["name_2"])

    def test_by_name_finds_debris_secondary(self):
        # Payload-vs-debris is the common SOCRATES case — must be reachable.
        with patch(_DL, return_value=FIXTURE_TEXT):
            deb = _fetcher().by_name("DEB")
        assert len(deb) > 0

    def test_by_catnr_either_position(self):
        # 61895 (OBJECT B) appears as object 1 in two fixture rows.
        with patch(_DL, return_value=FIXTURE_TEXT):
            hits = _fetcher().by_catnr(61895)
        assert len(hits) == 2
        for _, r in hits.iterrows():
            assert 61895 in (r["norad_id_1"], r["norad_id_2"])

    def test_between_is_pairwise(self):
        # Pairwise AND (one STARLINK, one FLOCK) — the fixture has two such rows.
        with patch(_DL, return_value=FIXTURE_TEXT):
            pairs = _fetcher().between("STARLINK", "FLOCK")
        assert len(pairs) == 2
        for _, r in pairs.iterrows():
            names = (r["name_1"], r["name_2"])
            assert any("STARLINK" in n for n in names)
            assert any("FLOCK" in n for n in names)

    def test_no_match_returns_empty_not_error(self):
        with patch(_DL, return_value=FIXTURE_TEXT):
            f = _fetcher()
            assert f.by_catnr(99999999).empty       # unknown NORAD id
            assert f.by_name("NOTAREALSAT").empty    # unknown name
            assert f.between("STARLINK", "NOPE").empty


class TestFetchCache:
    """fetch(): TTL cache, graceful fallback, never overwrite good cache."""

    def test_fetch_parses_and_caches(self):
        f = _fetcher()
        with patch(_DL, return_value=FIXTURE_TEXT) as dl:
            df = f.fetch()
            assert len(df) == 20
            # second call within TTL serves cache — no second download
            again = f.fetch()
            assert len(again) == 20
        dl.assert_called_once()

    def test_cache_round_trip_is_lossless(self):
        # The cache cross-validation: what we serve from Parquet must equal what we
        # parsed — tz-aware tca/fetch_time and int NORAD ids preserved exactly.
        f = _fetcher()
        with patch(_DL, return_value=FIXTURE_TEXT):
            fetched = f.fetch()
        loaded = f.load_cached()
        pd.testing.assert_frame_equal(fetched, loaded)

    def test_force_redownloads(self):
        f = _fetcher()
        with patch(_DL, return_value=FIXTURE_TEXT) as dl:
            f.fetch()
            f.fetch(force=True)
        assert dl.call_count == 2

    def test_stale_cache_redownloads(self):
        f = _fetcher()
        with patch(_DL, return_value=FIXTURE_TEXT) as dl:
            f.fetch()
            # age the cache file past the TTL → next fetch must re-download
            path = f.cache_dir / "socrates.parquet"
            old = (datetime.now(timezone.utc) - timedelta(hours=9)).timestamp()
            os.utime(path, (old, old))
            f.fetch()
        assert dl.call_count == 2

    def test_network_failure_falls_back_to_cache(self):
        f = _fetcher()
        with patch(_DL, return_value=FIXTURE_TEXT):
            f.fetch()                      # seed the cache
        with patch(_DL, side_effect=RuntimeError("boom")):
            # force=True to bypass the fresh cache and hit the network path
            df = f.fetch(force=True)
        assert len(df) == 20               # served stale cache, no exception

    def test_failure_with_no_cache_raises(self):
        f = _fetcher()
        with patch(_DL, side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                f.fetch()

    def test_empty_result_keeps_good_cache(self):
        f = _fetcher()
        with patch(_DL, return_value=FIXTURE_TEXT):
            f.fetch()                      # good cache exists
        header = FIXTURE_TEXT.splitlines()[0] + "\n"
        with patch(_DL, return_value=header):   # SOCRATES returns no rows
            df = f.fetch(force=True)
        assert len(df) == 20               # kept the good cache, didn't blank it


@pytest.mark.skip(reason="hits live CelesTrak — opt-in only")
class TestLiveFetch:
    def test_live_socrates_fetch(self):
        df = SOCRATESFetcher().fetch(force=True)
        assert len(df) > 1000
        assert df["range_km"].min() < 1.0   # SOCRATES lists sub-km approaches
