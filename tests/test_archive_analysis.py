#!/usr/bin/env python3
"""Tests for scripts/archive_analysis.py (Phase 11.1) — the pure metric
functions only: the constellation rule ported from the frontend, the
vectorised SFS regime / larger-of-two / ellipsoid logic the box
counterfactual relies on, and the consecutive-run matcher behind the
prediction-stability numbers. Offline: synthetic frames, no archive.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
import archive_analysis as A  # noqa: E402
from backend.core.screening_volumes import (  # noqa: E402
    LEO_1, LEO_2, LEO_3, LEO_4, DEEP_SPACE, regime_for)


class TestConstellationPort:
    """Same cases as tests/test_10_10_constellation_filter.js (the JS original)."""

    @pytest.mark.parametrize("name,fam", [
        ("STARLINK-5278", "STARLINK"), ("STARLINK-32363", "STARLINK"),
        ("ONEWEB-0012", "ONEWEB"), ("FLOCK 4Q-16", "FLOCK"), ("SKYSAT-A", "FLOCK"),
        ("HAWK-8A", "HAWK"), ("HAWKSAT-1", None), ("OBJECT STARLINK", None),
        ("COSMOS 1408 DEB", None), ("YAOGAN-2", None), (None, None), ("", None),
        ("HULIANWANG JISHU SHIYAN 1A", "HULIANWANG"), ("STARLINKX", None)])
    def test_family(self, name, fam):
        assert A.constellation_of(name) == fam

    def test_pair_class(self):
        assert A.pair_class("STARLINK-1", "STARLINK-2") == "Starlink–Starlink"
        assert A.pair_class("COSMOS 1408 DEB", "STARLINK-2") == "Starlink–other"
        assert A.pair_class("ONEWEB-0012", "ONEWEB-0013") == "other–other"
        assert A.pair_class(None, None) == "other–other"


class TestVolumeLogic:
    def test_regime_index_matches_regime_for(self):
        vols = (LEO_1, LEO_2, LEO_3, LEO_4, DEEP_SPACE)
        grid = [(p, e, t) for p in (200, 499.9, 500, 500.1, 750, 751, 1200, 1500, 2000, 2001,
                                    20000, 35786)
                for e in (0.0, 0.1, 0.2499, 0.25, 0.7) for t in (95, 720, 1436, 1300, 1800)]
        p, e, t = map(np.array, zip(*grid))
        got = A.regime_index(p, e, t)
        want = [vols.index(regime_for(*g)) if regime_for(*g) in vols else -1 for g in grid]
        assert list(got) == want

    def test_larger_of_two(self):
        # LEO 1 (0) beats LEO 2 (1) either way round; deep space (10 km) loses to LEO 2 (25 km)
        assert list(A.pair_volume([0, 1, 4], [1, 0, 1])) == [0, 0, 1]

    def test_inside_matches_contains(self):
        rng = np.random.default_rng(0)
        rtn = rng.uniform([-0.5, -50, -55], [0.5, 50, 55], size=(400, 3))
        for i, v in enumerate((LEO_1, LEO_2, LEO_3)):
            got = A.inside(np.full(len(rtn), i), rtn)
            assert list(got) == [v.contains(*x) for x in rtn]

    def test_hold_starlink_in_leo2_only_moves_starlink_leo1(self):
        out = A.hold_starlink_in_leo2([0, 0, 1, 2], [True, False, True, True])
        assert list(out) == [1, 0, 1, 2]

    def test_counterfactual_drops_box_only_event(self):
        # in-track 30 km: inside LEO 1 (44), outside LEO 2 (25)
        rtn = np.array([[0.0, 30.0, 0.0], [0.0, 10.0, 5.0]])
        held = A.pair_volume(A.hold_starlink_in_leo2([0, 0], [True, True]),
                             A.hold_starlink_in_leo2([0, 0], [True, True]))
        assert list(A.inside(held, rtn)) == [False, True]


def _ev(rows):
    cols = ["k", "p1", "p2", "tca", "miss_km", "lead_h", "age_d", "ep1", "ep2"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ("tca", "ep1", "ep2"):
        df[c] = pd.to_datetime(df[c])
    return df


class TestMatchConsecutive:
    runs = pd.DataFrame({"gen": pd.to_datetime(["2026-01-01 00:00", "2026-01-01 08:00",
                                                "2026-01-02 08:00"])})
    E0, E1 = "2025-12-31 12:00", "2026-01-01 06:00"

    def test_identical_vs_fresh_and_dmiss(self):
        ev = _ev([
            (0, 1, 2, "2026-01-01 20:00", 1.0, 20, 0.5, self.E0, self.E0),
            (1, 1, 2, "2026-01-01 20:05", 1.0, 12, 0.8, self.E0, self.E0),   # same elements
            (0, 3, 4, "2026-01-01 20:00", 1.0, 20, 0.5, self.E0, self.E0),
            (1, 3, 4, "2026-01-01 20:00", 3.5, 12, 0.6, self.E1, self.E0),   # p1 refreshed
        ])
        m = A.match_consecutive(ev, self.runs).sort_values("dmiss").reset_index(drop=True)
        assert list(m["fresh"]) == [False, True]
        assert list(m["both_fresh"]) == [False, False]
        assert m["dmiss"].tolist() == pytest.approx([0.0, 2.5])
        # lead / age are the EARLIER prediction's
        assert m["lead_h"].tolist() == [20, 20] and m["age_d"].tolist() == [0.5, 0.5]

    def test_tca_tolerance_and_closest_wins(self):
        ev = _ev([
            (0, 1, 2, "2026-01-01 20:00", 1.0, 20, 0.5, self.E0, self.E0),
            (0, 1, 2, "2026-01-01 20:20", 9.0, 20, 0.5, self.E0, self.E0),
            (1, 1, 2, "2026-01-01 20:14", 2.0, 12, 0.5, self.E1, self.E1),
            (1, 5, 6, "2026-01-01 20:00", 2.0, 12, 0.5, self.E1, self.E1),   # no predecessor
        ])
        m = A.match_consecutive(ev, self.runs)
        assert len(m) == 1 and m["dmiss"].iloc[0] == pytest.approx(7.0)   # 20:20 is 6 min away
        ev2 = ev[~((ev["k"] == 0) & (ev["miss_km"] == 9.0))]
        assert A.match_consecutive(ev2, self.runs)["dmiss"].tolist() == pytest.approx([1.0])
        far = ev2.assign(tca=ev2["tca"].where(ev2["k"] == 0, pd.Timestamp("2026-01-01 20:16")))
        assert A.match_consecutive(far, self.runs).empty                     # 16 min > 900 s

    def test_outage_gap_excluded(self):
        ev = _ev([
            (1, 1, 2, "2026-01-02 20:00", 1.0, 20, 0.5, self.E0, self.E0),
            (2, 1, 2, "2026-01-02 20:00", 2.0, 12, 0.5, self.E1, self.E1),   # 24 h later
        ])
        assert A.match_consecutive(ev, self.runs).empty

    def test_binned(self):
        df = pd.DataFrame({"age_d": [0.1, 0.2, 0.7, 5.0], "dmiss": [0.1, 0.3, 2.0, 4.0]})
        b = A.binned(df, "age_d", (0, 0.25, 1, np.inf))
        assert [r["n"] for r in b] == [2, 1, 1]
        assert b[0]["median_km"] == pytest.approx(0.2) and b[1]["gt1km_pct"] == 100.0
        assert b[2]["hi"] is None
