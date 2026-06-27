#!/usr/bin/env python3
"""Tests for backend/core/spacetrack_fetcher.py — the Stage-B epoch-matched lever
(Task 8.3/8.5). Offline/deterministic: Space-Track HTTP is fully mocked (a fake
requests.Session / a stubbed _query), so nothing here touches the network. A
single opt-in live test is skipped by default.

Focus areas (the parts most likely to break silently):
  - _coerce_numeric: Space-Track JSON is ALL strings; without coercion every
    record is dropped (EPHEMERIS_TYPE "0" != 0). This is the load-bearing fix.
  - BulkGPAdapter: nearest-epoch vs latest selection through the fetch_by_catnr
    seam that compare_against_socrates depends on.
  - auth: no-creds guard, success/bad-creds parsing, 401 re-login retry.
  - fetch_history: parse Space-Track all-string JSON + immutable Parquet cache.
"""

import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import core.spacetrack_fetcher as stmod  # noqa: E402
from core.spacetrack_fetcher import (  # noqa: E402
    BulkGPAdapter,
    SpaceTrackError,
    SpaceTrackFetcher,
    _cache_key,
    _coerce_numeric,
    _sorted_ids,
)

UTC = timezone.utc


def _st_record(norad, epoch_iso, mean_motion="15.5", ecc="0.0006"):
    """A Space-Track OMM/JSON record — EVERY value a string (as Space-Track sends)."""
    return {
        "OBJECT_NAME": f"OBJ-{norad}", "OBJECT_ID": "1998-067A",
        "NORAD_CAT_ID": str(norad), "CLASSIFICATION_TYPE": "U",
        "EPOCH": epoch_iso, "MEAN_MOTION": mean_motion, "ECCENTRICITY": ecc,
        "INCLINATION": "51.64", "RA_OF_ASC_NODE": "120.5",
        "ARG_OF_PERICENTER": "90.1", "MEAN_ANOMALY": "270.2",
        "BSTAR": "0.00012345", "MEAN_MOTION_DOT": "0.00001",
        "MEAN_MOTION_DDOT": "0.0", "EPHEMERIS_TYPE": "0",
        "ELEMENT_SET_NO": "999", "REV_AT_EPOCH": "12345", "DECAYED": "0",
    }


# ----------------------------------------------------------------------------
# Fakes for the requests.Session HTTP boundary
# ----------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code=200, text="", url=""):
        self.status_code = status_code
        self.text = text
        self.url = url


class _FakeSession:
    def __init__(self, post_resp=None, get_resps=None):
        self.headers = {}
        self.cookies = {}
        self._post_resp = post_resp or _FakeResp(200, "")
        self._get_resps = list(get_resps or [])
        self.posted, self.gotten = [], []

    def post(self, url, data=None, timeout=None):
        self.posted.append((url, data))
        ok = self._post_resp.status_code == 200 and "failed" not in self._post_resp.text.lower()
        if ok:
            self.cookies = {"chocolatechip": "session"}
        return self._post_resp

    def get(self, url, timeout=None):
        self.gotten.append(url)
        return self._get_resps.pop(0)


@pytest.fixture
def no_sleep(monkeypatch):
    """Neuter the rate-limit sleep so tests run instantly."""
    monkeypatch.setattr(stmod.time, "sleep", lambda *_: None)


# ----------------------------------------------------------------------------
class TestCoerceNumeric:
    def test_strings_become_float_and_int(self):
        out = _coerce_numeric(_st_record(25544, "2026-06-24T12:00:00"))
        assert out["MEAN_MOTION"] == pytest.approx(15.5)
        assert isinstance(out["MEAN_MOTION"], float)
        assert out["NORAD_CAT_ID"] == 25544 and isinstance(out["NORAD_CAT_ID"], int)

    def test_ephemeris_type_zero_is_int_not_string(self):
        # The load-bearing one: '0' != 0 is True → would skip EVERY record.
        out = _coerce_numeric(_st_record(1, "2026-01-01T00:00:00"))
        assert out["EPHEMERIS_TYPE"] == 0 and isinstance(out["EPHEMERIS_TYPE"], int)

    def test_empty_and_invalid_left_untouched(self):
        rec = _st_record(1, "2026-01-01T00:00:00")
        rec["BSTAR"] = ""           # empty → skip, don't crash
        rec["MEAN_MOTION_DOT"] = "n/a"   # unparseable → leave as-is
        out = _coerce_numeric(rec)
        assert out["BSTAR"] == "" and out["MEAN_MOTION_DOT"] == "n/a"

    def test_already_numeric_passes_through(self):
        rec = _st_record(1, "2026-01-01T00:00:00")
        rec["MEAN_MOTION"] = 15.5   # CelesTrak-style real number
        assert _coerce_numeric(rec)["MEAN_MOTION"] == 15.5

    def test_nonnumeric_string_fields_untouched(self):
        out = _coerce_numeric(_st_record(25544, "2026-06-24T12:00:00"))
        assert out["OBJECT_NAME"] == "OBJ-25544"
        assert out["CLASSIFICATION_TYPE"] == "U"

    def test_coercion_makes_parse_succeed(self):
        # End-to-end: an all-string record only parses because of coercion.
        f = SpaceTrackFetcher(user="u", password="p")
        df = f._parse(json.dumps([_st_record(25544, "2026-06-24T12:00:00")]))
        assert len(df) == 1
        assert df["norad_cat_id"].iloc[0] == 25544
        assert isinstance(df["mean_motion"].iloc[0], float)


# ----------------------------------------------------------------------------
class TestBulkGPAdapter:
    def _hist(self):
        def row(nid, ep):
            return {"norad_cat_id": nid, "object_name": f"S{nid}", "epoch": ep}
        return pd.DataFrame([
            row(1, datetime(2026, 6, 24, tzinfo=UTC)),
            row(1, datetime(2026, 6, 26, 12, tzinfo=UTC)),   # nearest target
            row(1, datetime(2026, 6, 30, tzinfo=UTC)),       # latest
            row(2, datetime(2026, 6, 25, 12, tzinfo=UTC)),
        ])

    def test_nearest_epoch_selected(self):
        targets = {1: datetime(2026, 6, 26, 13, tzinfo=UTC)}
        pick = BulkGPAdapter(self._hist(), targets).fetch_by_catnr(1)
        assert len(pick) == 1
        assert pick["epoch"].iloc[0] == pd.Timestamp("2026-06-26 12:00", tz="UTC")

    def test_latest_when_no_target(self):
        pick = BulkGPAdapter(self._hist()).fetch_by_catnr(1)
        assert pick["epoch"].iloc[0] == pd.Timestamp("2026-06-30", tz="UTC")

    def test_missing_id_returns_empty(self):
        assert BulkGPAdapter(self._hist()).fetch_by_catnr(999).empty

    def test_helper_column_dropped(self):
        pick = BulkGPAdapter(self._hist(), {1: datetime(2026, 6, 26, tzinfo=UTC)}).fetch_by_catnr(1)
        assert "_epoch_utc" not in pick.columns

    def test_empty_frame_returns_empty(self):
        assert BulkGPAdapter(pd.DataFrame()).fetch_by_catnr(1).empty

    def test_naive_target_handled(self):
        # robustness: a tz-naive target must not crash (treated as UTC)
        pick = BulkGPAdapter(self._hist(), {1: datetime(2026, 6, 26, 12)}).fetch_by_catnr(1)
        assert pick["epoch"].iloc[0] == pd.Timestamp("2026-06-26 12:00", tz="UTC")

    def test_int_key_lookup_across_numpy_dtypes(self):
        # groupby keys are numpy ints; a python-int query must still hit them.
        adp = BulkGPAdapter(self._hist())
        assert not adp.fetch_by_catnr(int(2)).empty


# ----------------------------------------------------------------------------
class TestHelpers:
    def test_sorted_ids_dedups_and_ints(self):
        assert _sorted_ids(["2", 2, 1, 3, "1"]) == [1, 2, 3]

    def test_cache_key_deterministic_and_sensitive(self):
        a = _cache_key([1, 2], "2026-06-01", "2026-06-05")
        assert a == _cache_key([1, 2], "2026-06-01", "2026-06-05")
        assert a != _cache_key([1, 2], "2026-06-01", "2026-06-06")
        assert a != _cache_key([1, 3], "2026-06-01", "2026-06-05")


# ----------------------------------------------------------------------------
class TestAuth:
    def test_no_creds_raises(self, monkeypatch):
        monkeypatch.delenv("SPACETRACK_USER", raising=False)
        monkeypatch.delenv("SPACETRACK_PASS", raising=False)
        with pytest.raises(SpaceTrackError, match="credentials not set"):
            SpaceTrackFetcher().login()

    def test_login_success_sets_session(self, monkeypatch, no_sleep):
        fake = _FakeSession(post_resp=_FakeResp(200, ""))
        monkeypatch.setattr(stmod.requests, "Session", lambda: fake)
        f = SpaceTrackFetcher(user="u", password="p")
        assert f.login() is fake
        assert f._session is fake

    def test_login_bad_creds_401_raises(self, monkeypatch, no_sleep):
        fake = _FakeSession(post_resp=_FakeResp(401, ""))
        monkeypatch.setattr(stmod.requests, "Session", lambda: fake)
        with pytest.raises(SpaceTrackError, match="login rejected"):
            SpaceTrackFetcher(user="u", password="p").login()

    def test_login_failed_body_raises(self, monkeypatch, no_sleep):
        fake = _FakeSession(post_resp=_FakeResp(200, '{"Login":"Failed"}'))
        monkeypatch.setattr(stmod.requests, "Session", lambda: fake)
        with pytest.raises(SpaceTrackError, match="login rejected"):
            SpaceTrackFetcher(user="u", password="p").login()

    def test_error_never_leaks_password(self, monkeypatch, no_sleep):
        fake = _FakeSession(post_resp=_FakeResp(401, ""))
        monkeypatch.setattr(stmod.requests, "Session", lambda: fake)
        try:
            SpaceTrackFetcher(user="u", password="SECRET123").login()
        except SpaceTrackError as e:
            assert "SECRET123" not in str(e)


# ----------------------------------------------------------------------------
class TestQueryRetry:
    def test_relogins_once_on_401(self, monkeypatch, no_sleep):
        f = SpaceTrackFetcher(user="u", password="p")
        f._session = _FakeSession(get_resps=[_FakeResp(401, "")])  # stale session
        fresh = _FakeSession(get_resps=[_FakeResp(200, "BODY")])
        monkeypatch.setattr(stmod.requests, "Session", lambda: fresh)  # re-login
        assert f._query("/x") == "BODY"
        assert f._session is fresh

    def test_http_error_raises(self, monkeypatch, no_sleep):
        f = SpaceTrackFetcher(user="u", password="p")
        f._session = _FakeSession(get_resps=[_FakeResp(500, "boom")])
        with pytest.raises(SpaceTrackError, match="HTTP 500"):
            f._query("/x")


# ----------------------------------------------------------------------------
class TestFetch:
    def test_fetch_history_parses_and_caches(self, monkeypatch, tmp_path):
        f = SpaceTrackFetcher(user="u", password="p", cache_dir=tmp_path)
        calls = []

        def fake_query(path):
            calls.append(path)
            return json.dumps([_st_record(25544, "2026-06-24T12:00:00")])
        monkeypatch.setattr(f, "_query", fake_query)

        d0 = datetime(2026, 6, 22, tzinfo=UTC)
        d1 = datetime(2026, 6, 26, tzinfo=UTC)
        df = f.fetch_history([25544], d0, d1)
        assert len(df) == 1 and df["norad_cat_id"].iloc[0] == 25544
        assert "gp_history" in calls[0] and "EPOCH" in calls[0]

        df2 = f.fetch_history([25544], d0, d1)   # immutable → cache hit
        assert len(calls) == 1 and len(df2) == 1

    def test_fetch_latest_uses_gp_class(self, monkeypatch, tmp_path):
        f = SpaceTrackFetcher(user="u", password="p", cache_dir=tmp_path)
        seen = {}

        def fake_query(path):
            seen["path"] = path
            return json.dumps([_st_record(25544, "2026-06-26T00:00:00")])
        monkeypatch.setattr(f, "_query", fake_query)

        df = f.fetch_latest([25544])
        assert len(df) == 1
        assert "/class/gp/" in seen["path"] and "gp_history" not in seen["path"]

    def test_empty_ids_no_query(self, monkeypatch, tmp_path):
        f = SpaceTrackFetcher(user="u", password="p", cache_dir=tmp_path)
        monkeypatch.setattr(f, "_query", lambda p: (_ for _ in ()).throw(AssertionError("queried")))
        assert f.fetch_latest([]).empty
        assert f.fetch_history([], datetime(2026, 1, 1, tzinfo=UTC),
                               datetime(2026, 1, 2, tzinfo=UTC)).empty


# ----------------------------------------------------------------------------
@pytest.mark.skip(reason="hits live Space-Track (needs SPACETRACK_USER/PASS) — opt-in only")
class TestLiveSpaceTrack:
    def test_live_iss_history(self):
        f = SpaceTrackFetcher()
        df = f.fetch_history([25544], datetime(2026, 6, 20, tzinfo=UTC),
                             datetime(2026, 6, 26, tzinfo=UTC))
        assert not df.empty and (df["norad_cat_id"] == 25544).all()
