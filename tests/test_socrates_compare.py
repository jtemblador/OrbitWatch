#!/usr/bin/env python3
"""Tests for backend/core/socrates_compare.py — the SOCRATES comparison core
(Task 8.2). Focuses on the pure, reusable matcher (match_events); the live
orchestrator (compare_against_socrates) is exercised by an opt-in integration
test in 8.5 and was validated end-to-end against real ISS data at build.

Offline/deterministic: synthetic SOCRATES rows + synthetic screen events.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.socrates_compare import (  # noqa: E402
    _dse_bucket,
    _stats,
    match_events,
)


def _soc_row(id1, id2, tca, miss, dse1=0.5, dse2=0.5, speed=10.0):
    return {
        "norad_id_1": id1, "name_1": f"SAT-{id1}", "status_1": "+", "dse_1": dse1,
        "norad_id_2": id2, "name_2": f"SAT-{id2}", "status_2": "+", "dse_2": dse2,
        "tca": pd.Timestamp(tca, tz="UTC"), "range_km": miss,
        "rel_speed_km_s": speed,
    }


def _our_event(id1, id2, tca_iso, miss):
    return {"sat1_norad_id": id1, "sat2_norad_id": id2,
            "tca": tca_iso, "miss_distance_km": miss}


class TestMatchEvents:
    def test_pair_within_window_matches_with_deltas(self):
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-28 12:00:00", 0.50)])
        our = [_our_event(1, 2, "2026-06-28T12:00:30+00:00", 0.55)]
        res, summ = match_events(soc, our)
        assert res[0]["matched"] is True
        assert res[0]["tca_delta_s"] == 30.0
        assert abs(res[0]["miss_delta_km"] - 0.05) < 1e-9
        assert summ["reproduction_rate"] == 1.0

    def test_pair_order_independent(self):
        # our event lists the pair in the opposite order — frozenset must match.
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-28 12:00:00", 0.5)])
        our = [_our_event(2, 1, "2026-06-28T12:00:00+00:00", 0.5)]
        res, _ = match_events(soc, our)
        assert res[0]["matched"] is True

    def test_outside_window_is_missed(self):
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-28 12:00:00", 0.5)])
        # 20 min away, default window is 10 min → no match
        our = [_our_event(1, 2, "2026-06-28T12:20:00+00:00", 0.5)]
        res, summ = match_events(soc, our)
        assert res[0]["matched"] is False
        assert res[0]["ours_miss_km"] is None
        assert summ["reproduction_rate"] == 0.0
        assert summ["n_extra"] == 1            # the unmatched our-event

    def test_closest_tca_wins(self):
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-28 12:00:00", 0.5)])
        our = [
            _our_event(1, 2, "2026-06-28T12:05:00+00:00", 0.9),   # 5 min
            _our_event(1, 2, "2026-06-28T12:00:10+00:00", 0.6),   # 10 s — closer
        ]
        res, summ = match_events(soc, our)
        assert res[0]["tca_delta_s"] == 10.0
        assert res[0]["ours_miss_km"] == 0.6
        assert summ["n_extra"] == 1            # the 5-min event is left over

    def test_extra_and_missed_counted(self):
        soc = pd.DataFrame([
            _soc_row(1, 2, "2026-06-28 12:00:00", 0.5),     # will match
            _soc_row(3, 4, "2026-06-29 00:00:00", 2.0),     # no our-event → missed
        ])
        our = [
            _our_event(1, 2, "2026-06-28T12:00:05+00:00", 0.5),
            _our_event(7, 8, "2026-06-28T15:00:00+00:00", 1.0),   # extra (unknown pair)
        ]
        res, summ = match_events(soc, our)
        assert [r["matched"] for r in res] == [True, False]
        assert summ["n_matched"] == 1 and summ["n_socrates"] == 2
        assert summ["reproduction_rate"] == 0.5
        assert summ["n_extra"] == 1

    def test_empty_our_events_all_missed(self):
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-28 12:00:00", 0.5)])
        res, summ = match_events(soc, [])
        assert res[0]["matched"] is False
        assert summ["reproduction_rate"] == 0.0
        assert summ["tca_delta_s"]["n"] == 0   # no matched deltas

    def test_segmented_by_dse(self):
        # one fresh (<1d) matched, one stale (>3d) missed — the degradation signal.
        soc = pd.DataFrame([
            _soc_row(1, 2, "2026-06-28 12:00:00", 0.5, dse1=0.3, dse2=0.4),
            _soc_row(3, 4, "2026-06-29 00:00:00", 2.0, dse1=4.0, dse2=5.0),
        ])
        our = [_our_event(1, 2, "2026-06-28T12:00:00+00:00", 0.5)]
        _, summ = match_events(soc, our)
        assert summ["by_dse"]["<1d"]["reproduction_rate"] == 1.0
        assert summ["by_dse"][">3d"]["reproduction_rate"] == 0.0

    def test_by_dse_always_has_three_buckets(self):
        # Stable shape for the report, even when a bucket is empty.
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-28 12:00:00", 0.5,
                                     dse1=0.3, dse2=0.4)])
        _, summ = match_events(soc, [])
        assert set(summ["by_dse"]) == {"<1d", "1-3d", ">3d"}
        assert summ["by_dse"]["1-3d"]["n_socrates"] == 0   # empty bucket present


class TestOrchestrator:
    """compare_against_socrates wiring — offline/deterministic via synthetic GP
    (real sgp4init) + a mocked screen, so the orchestration (id collection,
    missing-object drop, window, epoch_ok attach) is tested without network."""

    def test_oversized_slice_raises_before_any_fetch(self):
        from core.socrates_compare import compare_against_socrates
        # 20 distinct objects; cap at 5 → must raise before touching the network.
        soc = pd.DataFrame([
            _soc_row(i, i + 1000, "2026-06-28 12:00:00", 0.5) for i in range(10)])
        with pytest.raises(ValueError, match="distinct objects"):
            compare_against_socrates(soc, gp_fetcher=None, max_objects=5)

    def test_all_objects_missing_raises(self):
        from core.socrates_compare import compare_against_socrates
        soc = pd.DataFrame([_soc_row(1, 2, "2026-06-01 12:00:00", 1.0)])

        class EmptyFetcher:
            def fetch_by_catnr(self, nid):
                return pd.DataFrame()   # nothing fetchable

        with pytest.raises(RuntimeError, match="No GP data"):
            compare_against_socrates(soc, EmptyFetcher())

    def test_wiring_drops_missing_and_attaches_epoch_ok(self, monkeypatch):
        import core.socrates_compare as scmod
        from core.demo_seed import build_synthetic_shell
        from core.socrates_compare import compare_against_socrates

        shell = build_synthetic_shell(n=3)          # epoch = 2026-06-01 00:00 UTC
        a, b = (int(x) for x in shell["norad_cat_id"].tolist()[:2])
        MISSING = 999999

        class FakeFetcher:
            def fetch_by_catnr(self, nid):
                return shell[shell["norad_cat_id"] == nid].copy()   # empty if MISSING

        # TCA 12 h after the shell epoch → our element age = 0.5 d, matching dse.
        tca = "2026-06-01 12:00:00"
        soc = pd.DataFrame([
            _soc_row(a, b, tca, 1.0, dse1=0.5, dse2=0.5),
            _soc_row(a, MISSING, tca, 2.0),         # references missing → dropped
        ])

        def fake_screen(satrecs, meta, start, duration_hours, **kw):
            return [{"sat1_norad_id": a, "sat2_norad_id": b,
                     "tca": pd.Timestamp(tca, tz="UTC").isoformat(),
                     "miss_distance_km": 1.1}]
        monkeypatch.setattr(scmod, "run_screen", fake_screen)

        out = compare_against_socrates(soc, FakeFetcher())
        s = out["summary"]
        assert out["missing_objects"] == [MISSING]
        assert s["n_missing_objects"] == 1
        assert s["n_socrates"] == 1            # the missing-referencing row dropped
        assert s["n_matched"] == 1 and s["reproduction_rate"] == 1.0
        # epoch_ok computed end-to-end: our 0.5 d age == reported dse 0.5
        assert out["results"][0]["epoch_ok"] is True
        assert s["n_epoch_ok"] == 1


class TestHelpers:
    def test_dse_bucket_boundaries(self):
        assert _dse_bucket(0.99) == "<1d"
        assert _dse_bucket(1.0) == "1-3d"
        assert _dse_bucket(3.0) == "1-3d"
        assert _dse_bucket(3.01) == ">3d"

    def test_stats_uses_magnitude(self):
        # deltas can be signed; stats report |value| (how close, not direction)
        s = _stats([-1.0, 2.0, -3.0])
        assert s["n"] == 3
        assert s["median_abs"] == 2.0
        assert s["max_abs"] == 3.0

    def test_stats_empty(self):
        assert _stats([]) == {"n": 0}


@pytest.mark.skip(reason="hits live CelesTrak (GP per object) — opt-in only")
class TestLiveCompare:
    def test_live_iss_comparison(self):
        from core.socrates_compare import compare_against_socrates
        from core.socrates_fetcher import SOCRATESFetcher
        from core.tle_fetcher import GPFetcher

        iss = SOCRATESFetcher().by_catnr(25544)
        out = compare_against_socrates(iss, GPFetcher())
        s = out["summary"]
        assert s["n_socrates"] >= 1
        # same-method SGP4: matched events agree on TCA to well under the window
        if s["n_matched"]:
            assert s["tca_delta_s"]["max_abs"] < 60.0
