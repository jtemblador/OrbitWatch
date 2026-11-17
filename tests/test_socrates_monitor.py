#!/usr/bin/env python3
"""Tests for backend/core/socrates_monitor.py — the 11.2b cron agreement block.

Offline/deterministic: synthetic SOCRATES rows (socrates_fetcher schema) and
synthetic run_screen events. The sandbox can't reach celestrak.org, so the
live path is exercised only by the first cron run on main (see the task log).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.core.socrates_monitor import (  # noqa: E402
    IN_VOLUME_KM,
    agreement_block,
)

START = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
EPOCH = START - timedelta(hours=12)          # every object's element epoch
SCREENED = list(range(100, 120))
EPOCHS = {i: EPOCH for i in SCREENED}


def _t(hours):
    return START + timedelta(hours=hours)


def _soc(id1, id2, h, miss, speed=10.0, dse=0.5):
    return {"norad_id_1": id1, "name_1": f"S{id1}", "status_1": "+", "dse_1": dse,
            "norad_id_2": id2, "name_2": f"S{id2}", "status_2": "+", "dse_2": dse,
            "tca": pd.Timestamp(_t(h)), "range_km": miss, "rel_speed_km_s": speed}


def _ev(id1, id2, h, miss):
    return {"sat1_norad_id": id1, "sat2_norad_id": id2, "sat1_name": f"S{id1}",
            "sat2_name": f"S{id2}", "tca": _t(h).isoformat(), "miss_distance_km": miss}


# SOCRATES runs span ~7 days from its run start; these two bracket rows put our
# whole 24 h window inside its run (they're between unscreened debris, so they
# never enter either denominator themselves).
_BRACKET = [_soc(900, 901, -2.0, 1.0), _soc(900, 901, 160.0, 1.0)]


def _block(soc_rows, ours, **kw):
    df = pd.DataFrame(soc_rows + _BRACKET)
    return agreement_block(df, ours, SCREENED, EPOCHS, START, 24.0, **kw)


class TestAgreementBlock:
    def test_identical_screens_agree_both_ways(self):
        # The same encounters on both sides → 100 % both ways, zero deltas.
        soc = [_soc(100, 101, 3.0, 0.1), _soc(102, 103, 9.0, 0.3), _soc(104, 105, 15.0, 2.0)]
        ours = [_ev(100, 101, 3.0, 0.1), _ev(103, 102, 9.0, 0.3), _ev(104, 105, 15.0, 2.0)]
        b = _block(soc, ours)
        assert b["status"] == "ok"
        s2o, o2s = b["socrates_to_ours"], b["ours_to_socrates"]
        assert (s2o["n"], s2o["n_matched"], s2o["rate"]) == (2, 2, 1.0)  # 2.0 km: not in-volume
        assert (o2s["n"], o2s["n_matched"], o2s["rate"]) == (3, 3, 1.0)
        assert s2o["median_abs_dtca_s"] == 0.0 and o2s["median_abs_dmiss_km"] == 0.0
        json.dumps(b, allow_nan=False)

    def test_only_guaranteed_in_volume_socrates_rows_count_against_us(self):
        # A 3 km SOCRATES row can legitimately be outside our SFS ellipsoid
        # (0.4 km radial), so our not reporting it is not a disagreement; a
        # 0.2 km row is inside EVERY handbook volume, so missing it is.
        soc = [_soc(100, 101, 3.0, 3.0), _soc(102, 103, 5.0, 0.2)]
        b = _block(soc, [])
        assert b["socrates_to_ours"]["n"] == 1
        assert b["socrates_to_ours"]["n_matched"] == 0
        assert b["n_socrates_in_scope"] == 2

    def test_in_volume_radius_is_the_smallest_sfs_semi_axis(self):
        from backend.core.screening_volumes import DEEP_SPACE, LEO_1, LEO_2, LEO_3, LEO_4
        assert IN_VOLUME_KM == min(min(v.r_km, v.t_km, v.n_km)
                                   for v in (LEO_1, LEO_2, LEO_3, LEO_4, DEEP_SPACE))

    def test_socrates_side_collapses_to_closest_pass_per_pair(self):
        # We keep one event per pair per window (the closest); SOCRATES lists
        # every pass. Its second pass must not count as one we "missed".
        soc = [_soc(100, 101, 2.0, 0.3), _soc(100, 101, 14.0, 0.1)]
        b = _block(soc, [_ev(100, 101, 14.0, 0.1)])
        assert (b["socrates_to_ours"]["n"], b["socrates_to_ours"]["n_matched"]) == (1, 1)

    def test_unscreened_objects_and_slow_pairs_are_out_of_scope(self):
        soc = [_soc(100, 555, 3.0, 0.1),               # 555 = debris we don't screen
               _soc(100, 101, 4.0, 0.1, speed=0.2)]    # slow: our 7.2 suppression may drop it
        b = _block(soc, [])
        assert b["n_socrates_in_scope"] == 1           # the debris row is out of population
        assert b["socrates_to_ours"]["n"] == 0         # the slow one isn't a fair reference

    def test_ours_to_socrates_uses_the_5km_threshold(self):
        ours = [_ev(100, 101, 3.0, 4.0),   # < 5 km, SOCRATES didn't list it → a miss
                _ev(102, 103, 4.0, 9.0)]   # > 5 km: SOCRATES never lists these
        b = _block([], ours)
        o2s = b["ours_to_socrates"]
        assert (o2s["n"], o2s["n_matched"], o2s["rate"]) == (1, 0, 0.0)
        assert b["n_ours_in_scope"] == 1

    def test_window_overlap_and_edge_trim(self):
        # Our encounter 2 min before our window end is in the candidate pool but
        # not a denominator (its partner may sit just outside) — no false miss.
        ours = [_ev(100, 101, 24.0 - 2 / 60, 1.0)]
        b = _block([], ours)
        assert b["ours_to_socrates"]["n"] == 0
        assert b["overlap"]["hours"] == pytest.approx(24.0 - 2 * 600 / 3600, abs=0.01)

    def test_stale_socrates_run_reports_no_overlap(self):
        df = pd.DataFrame([_soc(100, 101, -200.0, 0.1), _soc(100, 101, -30.0, 0.1)])
        b = agreement_block(df, [_ev(100, 101, 3.0, 0.1)], SCREENED, EPOCHS, START, 24.0)
        assert b["status"] == "no_overlap"
        assert "socrates_to_ours" not in b
        json.dumps(b, allow_nan=False)

    def test_empty_socrates_run_reports_no_overlap(self):
        cols = list(_soc(1, 2, 0, 0).keys())
        b = agreement_block(pd.DataFrame(columns=cols), [], SCREENED, EPOCHS, START, 24.0)
        assert b["status"] == "no_overlap" and b["socrates_rows"] == 0

    def test_epoch_drift_shows_as_deltas_and_unmatched_passes(self):
        # Same pair, our elements drifted: TCA 40 s off + 0.25 km → still the same
        # encounter; a 2 h TCA shift → a different pass, counted as pair-flagged
        # but not matched (n_pair_flagged separates the two failure modes).
        soc = [_soc(100, 101, 3.0, 0.1), _soc(102, 103, 8.0, 0.2)]
        ours = [_ev(100, 101, 3.0 + 40 / 3600, 0.35), _ev(102, 103, 10.0, 0.2)]
        s2o = _block(soc, ours)["socrates_to_ours"]
        assert (s2o["n"], s2o["n_matched"], s2o["n_pair_flagged"]) == (2, 1, 2)
        assert s2o["median_abs_dtca_s"] == pytest.approx(40.0, abs=1e-3)
        assert s2o["median_abs_dmiss_km"] == pytest.approx(0.25, abs=1e-6)

    def test_our_side_is_bucketed_by_our_element_age_at_tca(self):
        epochs = dict(EPOCHS)
        epochs[104] = START - timedelta(days=4)   # one stale object → its event is ">3d"
        ours = [_ev(100, 101, 3.0, 1.0), _ev(104, 105, 3.0, 1.0)]
        df = pd.DataFrame(_BRACKET)
        b = agreement_block(df, ours, SCREENED, epochs, START, 24.0)
        by = b["ours_to_socrates"]["by_dse"]
        assert (by["<1d"]["n"], by["1-3d"]["n"], by[">3d"]["n"]) == (1, 0, 1)
        assert by["1-3d"]["rate"] is None          # empty bucket: no rate, not 0 %
