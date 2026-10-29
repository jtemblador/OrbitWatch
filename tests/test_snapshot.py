#!/usr/bin/env python3
"""Tests for backend/core/snapshot.py — the static-site snapshot builder (Task 9.2).

Pure/offline: the builder is dict-in/string-out, so fully deterministic. The
headline lock is the OMM cross-validation — our shipped OMM fields must
reconstruct the same orbit the browser's satellite.js `json2satrec` will, checked
via the reference python-sgp4 OMM loader vs our C++ SGP4 engine.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.snapshot import (  # noqa: E402
    ARCHIVE_ELEMENT_KEYS,
    ARCHIVE_FORMAT,
    SCHEMA_VERSION,
    _conjunction,
    archive_record,
    carry_forward_history,
    _epoch_iso,
    _num,
    _satellite,
    build_snapshot,
)


def _row(**over):
    """A GPFetcher-schema catalog row (pd.Series)."""
    base = {
        "object_name": "ISS (ZARYA)", "object_id": "1998-067A", "norad_cat_id": 25544,
        "classification": "U", "epoch": pd.Timestamp("2026-06-01T12:00:00", tz="UTC"),
        "epoch_age_days": 1.5, "mean_motion": 15.5, "eccentricity": 0.0006,
        "inclination": 51.64, "ra_of_asc_node": 120.5, "arg_of_pericenter": 90.1,
        "mean_anomaly": 270.2, "bstar": 1.2e-4, "mean_motion_dot": 1e-5,
        "mean_motion_ddot": 0.0, "ephemeris_type": 0, "element_set_no": 999,
        "rev_at_epoch": 12345, "object_type": "PAYLOAD",
    }
    base.update(over)
    return pd.Series(base)


def _event(**over):
    base = {
        "sat1_norad_id": 25544, "sat1_name": "ISS (ZARYA)", "sat1_object_type": "PAYLOAD",
        "sat2_norad_id": 12345, "sat2_name": "DEB", "sat2_object_type": "DEBRIS",
        "tca": "2026-06-01T18:00:00+00:00", "miss_distance_km": 1.23456,
        "relative_speed_km_s": 10.5432, "r_km": -0.1234, "t_km": 0.5678, "n_km": 0.9,
        "screening_regime": "LEO 1",
    }
    base.update(over)
    return base


def _cj(**over):
    """A COMPACT snapshot conjunction record — i.e. `_conjunction()` output.

    `carry_forward_history` deliberately works on this schema rather than on
    run_screen events: it makes the trail a pure snapshot→snapshot transform,
    so it is testable with dict literals (no pandas, no screen) and reusable by
    a future archive backfill without rework.
    """
    base = {
        "a": 100, "b": 200, "a_name": "SAT-A", "b_name": "SAT-B",
        "a_type": "PAYLOAD", "b_type": "PAYLOAD",
        "tca": "2026-06-01T18:00:00+00:00", "miss_km": 1.5,
        "rel_speed_km_s": 10.0, "rtn_km": [0.1, 0.2, 0.3], "regime": "LEO 1",
    }
    base.update(over)
    return base


_PREV_GEN = "2026-06-01T00:00:00Z"
_PREV_EPOCH = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())


def _reject_const(x):
    raise ValueError(f"non-finite JSON constant: {x!r}")


class TestNum:
    def test_finite(self):
        assert _num(1.5) == 1.5

    def test_nan_none_inf_to_default(self):
        assert _num(float("nan")) == 0.0
        assert _num(None) == 0.0
        assert _num(float("inf")) == 0.0

    def test_unparseable_uses_default(self):
        assert _num("x", default=-1.0) == -1.0


class TestEpoch:
    def test_tz_aware_utc_to_naive_iso(self):
        # the json2satrec format: no offset suffix, implicitly UTC
        s = _epoch_iso(pd.Timestamp("2026-06-01T12:00:00.123456", tz="UTC"))
        assert s == "2026-06-01T12:00:00.123456"
        assert "+" not in s and "Z" not in s

    def test_non_utc_is_converted(self):
        # a +02:00 stamp must convert to UTC before dropping the tz; microseconds
        # are always emitted (strict OMM parsers require the fractional field)
        assert _epoch_iso(pd.Timestamp("2026-06-01T14:00:00+02:00")) == "2026-06-01T12:00:00.000000"


class TestSatelliteOMM:
    def test_json2satrec_keys_present(self):
        s = _satellite(_row())
        for k in ("CCSDS_OMM_VERS", "EPOCH", "OBJECT_NAME", "NORAD_CAT_ID", "MEAN_MOTION",
                  "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER",
                  "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
                  "EPHEMERIS_TYPE"):
            assert k in s, f"missing OMM key {k}"

    def test_types(self):
        s = _satellite(_row())
        assert isinstance(s["NORAD_CAT_ID"], int) and isinstance(s["EPHEMERIS_TYPE"], int)
        assert isinstance(s["MEAN_MOTION"], float) and isinstance(s["OBJECT_NAME"], str)

    def test_object_type_display_field(self):
        assert _satellite(_row())["OBJECT_TYPE"] == "PAYLOAD"
        assert _satellite(_row(object_type=None))["OBJECT_TYPE"] == "UNKNOWN"

    def test_nan_numeric_becomes_zero(self):
        assert _satellite(_row(mean_motion_ddot=float("nan")))["MEAN_MOTION_DDOT"] == 0.0

    def test_missing_column_is_graceful(self):
        r = _row().drop("object_id")   # a row without the column → .get(None)
        assert _satellite(r)["OBJECT_ID"] == ""


class TestConjunction:
    def test_compact_fields_and_rounding(self):
        c = _conjunction(_event())
        assert c["a"] == 25544 and c["b"] == 12345
        assert c["a_name"] == "ISS (ZARYA)" and c["b_type"] == "DEBRIS"
        assert c["miss_km"] == 1.235 and c["rel_speed_km_s"] == 10.543
        assert c["rtn_km"] == [-0.123, 0.568, 0.9]
        assert c["regime"] == "LEO 1"
        assert c["tca"] == "2026-06-01T18:00:00+00:00"   # passed through untouched


class TestBuildSnapshot:
    def test_meta_and_shape(self):
        df = pd.DataFrame([_row(), _row(norad_cat_id=200, object_name="B")])
        snap = build_snapshot(df, [_event()], {"mode": "SFS", "window_hours": 72},
                              "2026-07-03T00:00:00Z", "CelesTrak active")
        m = snap["meta"]
        assert m["schema_version"] == SCHEMA_VERSION
        assert m["generated_at"] == "2026-07-03T00:00:00Z" and m["source"] == "CelesTrak active"
        assert m["n_satellites"] == 2 and m["n_conjunctions"] == 1
        assert m["screen"] == {"mode": "SFS", "window_hours": 72}
        assert len(snap["satellites"]) == 2 and len(snap["conjunctions"]) == 1

    def test_max_epoch_age(self):
        df = pd.DataFrame([_row(epoch_age_days=1.0), _row(epoch_age_days=4.567)])
        assert build_snapshot(df, [], {}, "t")["meta"]["max_epoch_age_days"] == 4.57

    def test_all_nan_age_is_none_not_nan(self):
        df = pd.DataFrame([_row(epoch_age_days=float("nan"))])
        assert build_snapshot(df, [], {}, "t")["meta"]["max_epoch_age_days"] is None

    def test_empty_catalog(self):
        df = pd.DataFrame(columns=list(_row().index))
        snap = build_snapshot(df, [], {}, "t")
        assert snap["meta"]["n_satellites"] == 0
        assert snap["meta"]["max_epoch_age_days"] is None

    def test_strict_json_no_nan(self):
        # the whole snapshot must serialize with allow_nan=False AND parse under a
        # NaN-rejecting reader (what the browser's JSON.parse does).
        df = pd.DataFrame([_row(mean_motion_ddot=float("nan"), bstar=float("nan"))])
        payload = json.dumps(build_snapshot(df, [_event()], {}, "t"), allow_nan=False)
        json.loads(payload, parse_constant=_reject_const)

    def test_prev_snapshot_attaches_history(self):
        df = pd.DataFrame([_row(norad_cat_id=25544), _row(norad_cat_id=12345)])
        prev = {"meta": {"generated_at": _PREV_GEN},
                "conjunctions": [_cj(a=25544, b=12345,
                                     tca="2026-06-01T18:00:00+00:00", miss_km=40.06)]}
        snap = build_snapshot(df, [_event()], {}, "t", prev=prev)
        assert snap["conjunctions"][0]["hist"] == [[_PREV_EPOCH, 40.06]]

    def test_without_prev_no_hist_key_is_emitted(self):
        # Absent, not null and not [] — so the ~69% of events with no trail cost
        # zero bytes, and the pre-9.10 output is unchanged.
        snap = build_snapshot(pd.DataFrame([_row()]), [_event()], {}, "t")
        assert all("hist" not in c for c in snap["conjunctions"])

    def test_prev_without_meta_degrades_quietly(self):
        snap = build_snapshot(pd.DataFrame([_row()]), [_event()], {}, "t", prev={})
        assert all("hist" not in c for c in snap["conjunctions"])

    def test_conjunction_ids_are_subset_of_satellites(self):
        df = pd.DataFrame([_row(norad_cat_id=1), _row(norad_cat_id=2)])
        snap = build_snapshot(df, [_event(sat1_norad_id=1, sat2_norad_id=2)], {}, "t")
        ids = {s["NORAD_CAT_ID"] for s in snap["satellites"]}
        for c in snap["conjunctions"]:
            assert c["a"] in ids and c["b"] in ids

    def test_run_telemetry_lands_in_meta(self):
        # GitHub deletes run logs after 90 days; meta.run is what survives in
        # the archive (screen time, which catalog source fed the run).
        run = {"screen_seconds": 812.5, "catalog_source": "spacetrack"}
        snap = build_snapshot(pd.DataFrame([_row()]), [], {}, "t", run=run)
        assert snap["meta"]["run"] == run

    def test_no_run_key_without_telemetry(self):
        # Absent, not null — the pre-10.10 meta shape is unchanged.
        assert "run" not in build_snapshot(pd.DataFrame([_row()]), [], {}, "t")["meta"]


def _full_snapshot():
    df = pd.DataFrame([_row(norad_cat_id=25544, object_name="ISS (ZARYA)"),
                       _row(norad_cat_id=12345, object_name="COSMOS DEB",
                            object_type="DEBRIS", mean_motion=14.9)])
    prev = {"meta": {"generated_at": _PREV_GEN},
            "conjunctions": [_cj(a=25544, b=12345,
                                 tca="2026-06-01T18:00:00+00:00", miss_km=2.0)]}
    return build_snapshot(df, [_event(sat2_name="COSMOS DEB")], {"mode": "SFS"},
                          "2026-07-03T00:00:00Z",
                          "CelesTrak active", prev=prev, run={"screen_seconds": 1.0})


class TestArchiveRecord:
    def test_drops_full_omm_keeps_meta_conjunctions_elements(self):
        rec = archive_record(_full_snapshot())
        assert set(rec) == {"meta", "conjunctions", "elements"}
        assert rec["meta"]["archive"]["format"] == ARCHIVE_FORMAT
        assert rec["meta"]["n_satellites"] == 2 and rec["meta"]["run"] == {"screen_seconds": 1.0}

    def test_elements_are_columnar_and_aligned(self):
        snap = _full_snapshot()
        el = archive_record(snap)["elements"]
        assert list(el) == list(ARCHIVE_ELEMENT_KEYS)
        assert all(len(col) == len(snap["satellites"]) for col in el.values())
        for i, sat in enumerate(snap["satellites"]):
            for k in ARCHIVE_ELEMENT_KEYS:
                assert el[k][i] == sat[k]
        # (NORAD_CAT_ID, EPOCH) is the Space-Track gp_history key that recovers
        # the dropped full element set — both must survive.
        assert {"NORAD_CAT_ID", "EPOCH"} <= set(el)

    def test_conjunction_names_move_to_elements(self):
        snap = _full_snapshot()
        rec = archive_record(snap)
        c = rec["conjunctions"][0]
        assert not {"a_name", "b_name", "a_type", "b_type"} & set(c)
        assert c["hist"] == snap["conjunctions"][0]["hist"]    # 9.10 trail kept
        assert c["miss_km"] == snap["conjunctions"][0]["miss_km"]
        # every dropped name is still recoverable through the elements table
        names = dict(zip(rec["elements"]["NORAD_CAT_ID"], rec["elements"]["OBJECT_NAME"]))
        full = snap["conjunctions"][0]
        assert names[c["a"]] == full["a_name"] and names[c["b"]] == full["b_name"]

    def test_does_not_mutate_the_published_snapshot(self):
        snap = _full_snapshot()
        before = json.dumps(snap, sort_keys=True)
        archive_record(snap)
        assert json.dumps(snap, sort_keys=True) == before

    def test_strict_json_and_empty_snapshot(self):
        rec = archive_record(build_snapshot(pd.DataFrame(columns=list(_row().index)),
                                            [], {}, "t"))
        assert rec["conjunctions"] == [] and all(v == [] for v in rec["elements"].values())
        json.loads(json.dumps(archive_record(_full_snapshot()), allow_nan=False),
                   parse_constant=_reject_const)


class TestCarryForwardHistory:
    """9.10 — the self-propagating `hist` chain.

    Each rebuild reads only the PREVIOUS snapshot, so the cost is O(current
    events) and the 147 MB `data`-branch archive is never touched. The link key
    is (unordered pair, TCA +/- 900 s): the pair ALONE is wrong, because
    `_dedupe_to_unique_pairs` keeps one event per pair per window and
    co-orbiting satellites conjunct repeatedly, so the same pair names a
    different encounter as the window slides.
    """

    def test_match_attaches_prior_point(self):
        out = carry_forward_history([_cj(miss_km=0.37)], [_cj(miss_km=40.06)], _PREV_GEN)
        assert out[0]["hist"] == [[_PREV_EPOCH, 40.06]]

    def test_point_is_stamped_with_the_run_time_not_the_tca(self):
        # The sparkline's x-axis is "when was this predicted" — lead time is
        # derived downstream as (tca - run time). Stamping the TCA instead would
        # collapse every point onto ~the same x.
        out = carry_forward_history([_cj()], [_cj(miss_km=2.0)], _PREV_GEN)
        assert out[0]["hist"][0][0] == _PREV_EPOCH

    def test_is_pure_and_does_not_mutate_its_input(self):
        cur = [_cj()]
        carry_forward_history(cur, [_cj(miss_km=9.9)], _PREV_GEN)
        assert "hist" not in cur[0]

    def test_pair_order_is_irrelevant(self):
        out = carry_forward_history([_cj(a=100, b=200)],
                                    [_cj(a=200, b=100, miss_km=7.0)], _PREV_GEN)
        assert out[0]["hist"] == [[_PREV_EPOCH, 7.0]]

    def test_different_pair_is_not_matched(self):
        out = carry_forward_history([_cj(a=100, b=200)], [_cj(a=100, b=999)], _PREV_GEN)
        assert "hist" not in out[0]

    def test_tca_drift_at_the_window_edge_matches(self):
        out = carry_forward_history([_cj(tca="2026-06-01T18:15:00+00:00")],
                                    [_cj(miss_km=3.0)], _PREV_GEN)
        assert out[0]["hist"] == [[_PREV_EPOCH, 3.0]]

    def test_tca_drift_past_the_window_does_not_match(self):
        out = carry_forward_history([_cj(tca="2026-06-01T18:15:01+00:00")],
                                    [_cj()], _PREV_GEN)
        assert "hist" not in out[0]

    def test_chain_extends_across_runs(self):
        prev = [_cj(miss_km=1.18, hist=[[1, 40.06]])]
        out = carry_forward_history([_cj(miss_km=0.37)], prev, _PREV_GEN)
        assert out[0]["hist"] == [[1, 40.06], [_PREV_EPOCH, 1.18]]

    def test_depth_capped_dropping_the_oldest(self):
        prev = [_cj(miss_km=4.0, hist=[[1, 1.0], [2, 2.0], [3, 3.0]])]
        out = carry_forward_history([_cj()], prev, _PREV_GEN)
        # 3 prior points + the live value = the 4-point max depth measured
        # across the 41-day archive.
        assert out[0]["hist"] == [[2, 2.0], [3, 3.0], [_PREV_EPOCH, 4.0]]

    def test_no_previous_snapshot_is_a_noop(self):
        for prev in (None, []):
            out = carry_forward_history([_cj()], prev, _PREV_GEN)
            assert "hist" not in out[0]

    def test_malformed_prev_records_are_skipped_not_fatal(self):
        # `prev` is downloaded from the live site — a corrupt record must cost us
        # that one trail, never the build.
        prev = [{"a": 100},                                  # missing everything else
                _cj(a=100, b=201, tca="not-a-date"),         # unparseable TCA
                _cj(a=100, b=200, miss_km=2.0)]              # the real match
        out = carry_forward_history([_cj()], prev, _PREV_GEN)
        assert out[0]["hist"] == [[_PREV_EPOCH, 2.0]]

    def test_malformed_trail_points_are_dropped_not_propagated(self):
        # The trail is copied forward every run, so a bad point would ride the
        # chain for HISTORY_MAX_POINTS runs and reach the browser each time.
        prev = [_cj(miss_km=9.0, hist=[[1, 1.0], "junk", [2], [3, "x"],
                                       [4, float("nan")], [5, 5.0]])]
        out = carry_forward_history([_cj()], prev, _PREV_GEN, max_points=99)
        assert out[0]["hist"] == [[1, 1.0], [5, 5.0], [_PREV_EPOCH, 9.0]]

    def test_non_list_trail_is_ignored(self):
        out = carry_forward_history([_cj()], [_cj(miss_km=9.0, hist="nope")], _PREV_GEN)
        assert out[0]["hist"] == [[_PREV_EPOCH, 9.0]]

    def test_round_trips_real_conjunction_output_not_just_the_fixture(self):
        """Lock the trail against the REAL `_conjunction` shape.

        Every other test here feeds `_cj()`, a hand-written copy of that shape —
        so if `_conjunction` ever renamed `a`/`b`/`tca`/`miss_km`, the fixture
        would quietly keep the old names, these tests would stay green, and
        production would silently stop producing history. This one builds the
        previous run through the real function instead.
        """
        prev = [_conjunction(_event(miss_distance_km=40.06))]
        cur = [_conjunction(_event(miss_distance_km=0.37))]
        out = carry_forward_history(cur, prev, _PREV_GEN)
        assert out[0]["hist"] == [[_PREV_EPOCH, 40.06]]

    def test_unparseable_generated_at_disables_history(self):
        out = carry_forward_history([_cj()], [_cj()], "garbage")
        assert "hist" not in out[0]

    def test_naive_timestamps_are_read_as_utc(self):
        # A naive stamp through datetime.timestamp() would silently land in LOCAL
        # time — hours of drift into the trail, invisible in a UTC-configured CI.
        out = carry_forward_history([_cj()], [_cj(miss_km=5.0)],
                                    "2026-06-01T00:00:00")
        assert out[0]["hist"] == [[_PREV_EPOCH, 5.0]]

    def test_hist_survives_strict_json(self):
        out = carry_forward_history([_cj(miss_km=0.37)], [_cj(miss_km=40.06)], _PREV_GEN)
        json.loads(json.dumps(out, allow_nan=False), parse_constant=_reject_const)


class TestOMMCrossValidation:
    """The headline lock: our shipped OMM must reconstruct the same orbit
    satellite.js json2satrec will — checked via the reference python-sgp4 OMM
    loader vs our C++ SGP4 engine (same Vallado algorithm). A wrong field name,
    unit, or EPOCH format would diverge by km, not sub-meter."""

    def test_omm_reconstructs_same_orbit_as_cpp(self):
        import orbitcore
        from core.demo_seed import build_synthetic_shell
        from core.propagator import omm_to_sgp4_params
        try:
            from sgp4 import omm as pyomm
            from sgp4.api import Satrec
        except Exception:
            pytest.skip("python-sgp4 omm module unavailable")

        shell = build_synthetic_shell(n=3)
        # build_synthetic_shell uses out-of-range synthetic NORAD IDs; the
        # reference OMM loader enforces the Alpha-5 cap (≤ 339999). Real catalog
        # IDs are far below it, and satnum doesn't affect the orbit — remap so the
        # cross-check exercises the ELEMENT serialization, not the fake IDs.
        shell["norad_cat_id"] = [25544 + i for i in range(len(shell))]
        for i in range(len(shell)):
            row = shell.iloc[i]
            omm = _satellite(row)                       # the EXACT dict we ship
            sat_ref = Satrec()
            pyomm.initialize(sat_ref, omm)              # reference OMM → satrec
            sat_cpp = orbitcore.sgp4init(**omm_to_sgp4_params(row))  # our engine
            jd = sat_ref.jdsatepoch + sat_ref.jdsatepochF
            _e, r_ref, _v = sat_ref.sgp4(jd, 6.0 / 24.0)   # +6 h
            r_cpp, _vc = orbitcore.sgp4(sat_cpp, 360.0)    # +360 min
            assert math.dist(r_ref, r_cpp) < 0.01          # < 10 m (km units)
