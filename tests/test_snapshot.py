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

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.snapshot import (  # noqa: E402
    SCHEMA_VERSION,
    _conjunction,
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

    def test_conjunction_ids_are_subset_of_satellites(self):
        df = pd.DataFrame([_row(norad_cat_id=1), _row(norad_cat_id=2)])
        snap = build_snapshot(df, [_event(sat1_norad_id=1, sat2_norad_id=2)], {}, "t")
        ids = {s["NORAD_CAT_ID"] for s in snap["satellites"]}
        for c in snap["conjunctions"]:
            assert c["a"] in ids and c["b"] in ids


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
