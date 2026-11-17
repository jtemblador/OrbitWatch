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


# ── 11.1e: the trends mode (was scripts/analyze_archive.py) ───────────────────

def _mm(alt_km):
    """Mean motion (rev/day) of a circular orbit at alt_km — inverse of mean_altitude."""
    a = A.R_EARTH + alt_km
    return np.sqrt(A.GM_EARTH / a**3) * 86400.0 / (2.0 * np.pi)


# (norad, name, altitude km, inclination)
_SATS = [(1, "STARLINK-1", 460.0, 53.0), (2, "STARLINK-2", 462.0, 53.0),
         (3, "STARLINK-3", 551.0, 97.0), (4, "STARLINKX 9", 455.0, 53.0),   # not a Starlink
         (5, "COSMOS 1408 DEB", 470.0, 82.0), (6, "ONEWEB-0012", 1200.0, 87.9)]
_CONJ = [(1, 2), (1, 5), (4, 5), (3, 6)]     # SL–SL, SL–other, other–other, SL–other


def _snapshot(gen, slim, sats=_SATS, conj=_CONJ):
    els = [{"NORAD_CAT_ID": n, "OBJECT_NAME": nm, "MEAN_MOTION": _mm(alt), "INCLINATION": inc,
            "ECCENTRICITY": 0.0001, "EPOCH": "2026-01-01T00:00:00"} for n, nm, alt, inc in sats]
    names = {n: nm for n, nm, *_ in sats}
    cj = [{"a": a, "b": b, "tca": "2026-01-01T06:00:00", "miss_km": 1.0, "rel_speed_km_s": 1.0,
           "rtn_km": [0.1, 1.0, 1.0], "regime": "LEO 1"} for a, b in conj]
    meta = {"generated_at": gen, "n_satellites": len(sats), "n_conjunctions": len(cj),
            "screen": {"window_hours": 24}, "max_epoch_age_days": 1.0}
    if slim:
        meta["archive"] = {"format": "slim-v1"}
        return {"meta": meta, "conjunctions": cj,
                "elements": {k: [e[k] for e in els] for k in els[0]}}
    for c in cj:
        c["a_name"], c["b_name"] = names[c["a"]], names[c["b"]]
    return {"meta": meta, "conjunctions": cj, "satellites": [{"omm": e} for e in els]}


def _write(dirpath, snap):
    import gzip
    import json
    name = snap["meta"]["generated_at"].replace(":", "-") + ".json.gz"
    p = os.path.join(dirpath, name)
    with gzip.open(p, "wt") as f:
        json.dump(snap, f)
    return p


def _rows(rows, measure):
    return {g: v for _, m, g, v in rows if m == measure}


class TestTrendMeasures:
    def test_mean_altitude_inverts_and_matches_circular_perigee(self):
        assert A.mean_altitude(_mm(550.0)) == pytest.approx(550.0, abs=1e-9)
        assert A.mean_altitude(15.05) == pytest.approx(A.orbit_shape(15.05, 0.0)[0])
        # orbit-averaged, not perigee: eccentricity lowers perigee, not mean altitude
        assert A.orbit_shape(15.05, 0.01)[0] < A.mean_altitude(15.05)

    def test_band_edges_and_altitude_order(self):
        assert list(A.band_labels([449.999, 450.0, 474.999, 475.0])) == \
            ["425-450", "450-475", "450-475", "475-500"]
        c = A.band_counts([1210, 460, 551, 470, 1205, 552])     # ties: 2/2/2
        assert list(c.index) == ["450-475", "550-575", "1200-1225"]   # by altitude, not tie order
        assert list(c) == [2, 2, 2]

    @pytest.mark.parametrize("slim", [False, True])
    def test_trend_rows_both_shapes(self, slim):
        sats, conj = A.trend_frames(_snapshot("2026-01-01T00:00:00Z", slim))
        rows = A.trend_rows("t", sats, conj)
        assert _rows(rows, "catalog") == {"all": 6, "starlink": 3}       # STARLINKX excluded
        assert _rows(rows, "starlink_by_band") == {"450-475": 2, "550-575": 1}
        fam = _rows(rows, "conj_family")
        assert fam == {"total": 4, "Starlink–Starlink": 1, "Starlink–other": 2, "other–other": 1}
        # pair altitude = mean of the two: (460+462)/2, (460+470)/2, (455+470)/2, (551+1200)/2
        assert _rows(rows, "conj_by_band") == {"450-475": 3, "875-900": 1}

    def test_slim_and_full_give_identical_rows(self):
        full = A.trend_rows("t", *A.trend_frames(_snapshot("2026-01-01T00:00:00Z", False)))
        slim = A.trend_rows("t", *A.trend_frames(_snapshot("2026-01-01T00:00:00Z", True)))
        assert full == slim

    def test_track_objects(self):
        first = A.trend_frames(_snapshot("x", True, sats=[
            (1, "STARLINK-1", 550.0, 53.0), (2, "STARLINK-2", 551.0, 53.0),
            (3, "STARLINK-3", 560.0, 53.0), (4, "STARLINK-4", 460.0, 97.0),
            (5, "STARLINK-5", 400.0, 53.0)], conj=[]))[0]
        last = A.trend_frames(_snapshot("y", True, sats=[
            (1, "STARLINK-1", 465.0, 53.0), (2, "STARLINK-2", 551.0, 53.0),  # 3 decayed
            (4, "STARLINK-4", 461.0, 97.0), (5, "STARLINK-5", 470.0, 53.0),  # 5 raised
            (9, "STARLINK-9", 455.0, 70.0)], conj=[]))[0]                   # 9 new
        tr = A.track_objects(first, last)
        assert tr["from_525_575"] == {"n": 3, "at_450_500": 1, "at_500_525": 0, "below_450": 0,
                                      "still_525_575": 1, "gone": 1}
        assert tr["into_450_475"] == {"n": 4, "already": 1, "came_down": 1, "came_up": 1, "new": 1}
        assert tr["inc_mix_450_475"] == [{97.0: 1}, {53.0: 2, 97.0: 1, 70.0: 1}]

    def test_crowding_exponent(self):
        n = np.array([1000.0, 2000, 3000, 5000])
        slope, r = A.crowding_exponent(n, 3e-4 * n**2)
        assert slope == pytest.approx(2.0) and r == pytest.approx(1.0)

    def test_pivot_merges_550_shell_with_a_missing_half(self):
        df = pd.DataFrame([("2026-01-01T00:00:00Z", "starlink_by_band", "525-550", 5),
                           ("2026-01-01T00:00:00Z", "starlink_by_band", "550-575", 7),
                           ("2026-01-02T00:00:00Z", "starlink_by_band", "525-550", 4)],
                          columns=["generated_at", "measure", "group", "value"])
        assert A.trend_pivot(df)[("starlink_by_band", "525-575")].tolist() == [12, 4]


class TestArchiveSource:
    def test_snapshot_time_from_name(self):
        assert A.snapshot_time("/x/2026-10-29T00-58-01.304098Z.json.gz") == \
            pd.Timestamp("2026-10-29 00:58:01")
        item = ("git", "/r", "origin/data", "snapshots/2026-07-08T02-12-42.4Z.json.gz")
        assert A.snapshot_time(item) == pd.Timestamp("2026-07-08 02:12:42")

    def test_parse_until(self):
        assert A.parse_until(None) is None
        assert A.parse_until("2026-10-29") == pd.Timestamp("2026-10-29 23:59:59.999999")
        assert A.parse_until("2026-10-29T01:00") == pd.Timestamp("2026-10-29 01:00")
        assert A.parse_until("2026-10-29T03:00+02:00") == pd.Timestamp("2026-10-29 01:00")

    def test_until_cuts_directory(self, tmp_path):
        for g in ("2026-10-28T21:17:00Z", "2026-10-29T00:58:01Z", "2026-10-29T12:26:08Z"):
            _write(tmp_path, _snapshot(g, True))
        names = lambda items: [os.path.basename(i)[:19] for i in items]  # noqa: E731
        assert len(A.list_snapshots(str(tmp_path))) == 3
        assert names(A.list_snapshots(str(tmp_path), until="2026-10-29T01:00")) == \
            ["2026-10-28T21-17-00", "2026-10-29T00-58-01"]
        assert len(A.list_snapshots(str(tmp_path), until="2026-10-29")) == 3   # end of day
        with pytest.raises(ValueError):
            A.list_snapshots()

    def test_git_ref_source_matches_directory(self, tmp_path):
        import shutil
        import subprocess
        if shutil.which("git") is None:
            pytest.skip("git not installed")
        repo = tmp_path / "repo"
        snaps = repo / "snapshots"
        snaps.mkdir(parents=True)
        paths = [_write(snaps, _snapshot(g, slim)) for g, slim in
                 (("2026-01-01T00:00:00Z", False), ("2026-01-01T08:00:00Z", True))]
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "s", "--no-gpg-sign"]):
            subprocess.run(["git", "-C", str(repo), *cmd], check=True, env=env)
        items = A.list_snapshots(ref="HEAD", repo=str(repo))
        assert [i[-1] for i in items] == [f"snapshots/{os.path.basename(p)}" for p in paths]
        for item, p in zip(items, paths):
            assert A.read_snapshot(item) == A.read_snapshot(p)

    def test_trends_cli_end_to_end(self, tmp_path, capsys):
        d = tmp_path / "snaps"
        d.mkdir()
        _write(d, _snapshot("2026-01-01T00:00:00Z", False))
        _write(d, _snapshot("2026-01-01T08:00:00Z", True))
        _write(d, _snapshot("2026-01-02T08:00:00Z", True, sats=_SATS[:2], conj=[(1, 2)]))  # small
        A.main(["trends", str(d), "--out", str(tmp_path / "out"), "--min-sats", "5",
                "--workers", "1", "--no-figures"])
        csv = pd.read_csv(tmp_path / "out" / "archive_trends.csv")
        assert csv["generated_at"].nunique() == 2                      # the 2-object run skipped
        assert "skipped 1 under --min-sats" in capsys.readouterr().out
        a, b = (g.drop(columns="generated_at").reset_index(drop=True)
                for _, g in csv.groupby("generated_at"))
        pd.testing.assert_frame_equal(a, b)                             # full ≡ slim, row for row
