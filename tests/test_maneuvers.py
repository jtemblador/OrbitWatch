#!/usr/bin/env python3
"""Tests for backend/core/maneuvers.py + its snapshot wiring (Task 11.2a).

The headline lock is ORACLE EQUIVALENCE: a seeded multi-object sequence
(drag decay, element noise, orbit raising, lowering, plane changes, operator /
18 SDS flip-flop, merged re-fits, late-joining objects) replayed run by run
through the production incremental detector — with the state pushed through
encode → JSON → decode between EVERY run, exactly as the cron's --prev chain
does — must confirm exactly the maneuvers the validated prototype
(progress/week11_planning/maneuver/detector.py, a whole-history pandas
implementation) confirms, at the same runs.

Pure / offline / deterministic.
"""

import importlib.util
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core import maneuvers as M  # noqa: E402
from core.snapshot import (  # noqa: E402
    MANEUVER_STATE_RETAIN_D,
    apply_maneuvers,
    archive_record,
    build_snapshot,
)

_ORACLE = os.path.join(os.path.dirname(__file__), "..", "progress", "week11_planning",
                       "maneuver", "detector.py")
T0 = datetime(2026, 9, 1, 5, 17, tzinfo=timezone.utc)
RUN_H = 8.0


def _oracle():
    spec = importlib.util.spec_from_file_location("_mnv_oracle", _ORACLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _n_from_alt(alt_km):
    a = 6378.137 + alt_km
    return math.sqrt(M.MU_KM3_S2 / a ** 3) * 86400 / (2 * math.pi)


def _n_from_a_m(a_m):
    return math.sqrt(M.MU_KM3_S2 / (a_m / 1000) ** 3) * 86400 / (2 * math.pi)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")


KINDS = ["passive", "raise", "lower", "plane", "flip", "merge", "storm"]


def synthetic_runs(seed=7, n_obj=36, n_runs=150):
    """[(gen_datetime, [(id, EPOCH iso, MEAN_MOTION, INCLINATION), ...]), ...]

    One set per object per run at most, epochs strictly increasing per object
    (what the cron sees). Object kinds: passive drag-only, a raiser with
    multi-run campaigns, a lowerer, plane changers, flip-floppers (two fit
    sources alternating ±400 m), re-fit mergers (<3 min apart), fast decayers
    with storm-like drag variability (exercise the BETA lowering rule), late
    joiners.
    """
    rng = np.random.default_rng(seed)
    objs = []
    for k in range(n_obj):
        kind = KINDS[k % len(KINDS)]
        objs.append(dict(id=40000 + k, kind=kind,
                         a=(6378.137 + rng.uniform(420, 560)) * 1000,
                         i=float(rng.uniform(40, 98)),
                         # m/day; "storm" = a low cubesat whose decay varies x0.5-2.5
                         decay=float(rng.uniform(-700, -350) if kind == "storm"
                                     else rng.uniform(-60, -3)),
                         noise=float(rng.uniform(4, 60)),          # m
                         start=int(rng.integers(0, 25)) if k % 5 == 4 else 0,
                         last_ep=None, flip=1))
    runs = []
    for r in range(n_runs):
        gen = T0 + timedelta(hours=RUN_H * r + float(rng.uniform(0, 3)))
        rows = []
        for o in objs:
            if r < o["start"] or rng.random() < 0.15:
                continue
            ep = gen - timedelta(hours=float(rng.uniform(0.5, 7.5)))
            if o["kind"] == "merge" and o["last_ep"] is not None and rng.random() < 0.3:
                ep = o["last_ep"] + timedelta(seconds=float(rng.uniform(20, 170)))
            if o["last_ep"] is not None and ep <= o["last_ep"]:
                continue
            dt_d = 0 if o["last_ep"] is None else (ep - o["last_ep"]).total_seconds() / 86400
            o["a"] += o["decay"] * dt_d * (float(rng.uniform(0.5, 2.5))
                                           if o["kind"] == "storm" else 1.0)
            if o["kind"] == "raise" and rng.random() < 0.08:
                o["a"] += float(rng.uniform(150, 2500))
            if o["kind"] == "lower" and rng.random() < 0.06:
                o["a"] -= float(rng.uniform(150, 1500))
            if o["kind"] == "plane" and rng.random() < 0.05:
                o["i"] += float(rng.choice([-1, 1]) * rng.uniform(0.02, 0.1))
            a_obs = o["a"] + float(rng.normal(0, o["noise"]))
            if o["kind"] == "flip":
                o["flip"] = -o["flip"] if rng.random() < 0.5 else o["flip"]
                a_obs += 400.0 * o["flip"]
            i_obs = o["i"] + float(rng.normal(0, 0.0007))
            o["last_ep"] = ep
            rows.append((o["id"], _iso(ep), round(_n_from_a_m(a_obs), 8), round(i_obs, 4)))
        runs.append((gen, rows))
    return runs


def _oracle_confirmations(runs):
    rec = []
    for gen, rows in runs:
        for nid, ep, n, i in rows:
            rec.append(dict(NORAD_CAT_ID=nid, EPOCH=pd.Timestamp(ep), MEAN_MOTION=n,
                            INCLINATION=i, gen=pd.Timestamp(gen).tz_convert(None)))
    out = _oracle().detect(pd.DataFrame(rec))
    c = out[out.flag_c]
    raw = int(out.flag.sum())
    conf = {(int(r.NORAD_CAT_ID), int(r.EPOCH.value // 1000), pd.Timestamp(r.confirm_gen))
            for r in c.itertuples()}
    return conf, raw, out


def _prod_confirmations(runs, roundtrip=True):
    states, conf = {}, set()
    for gen, rows in runs:
        if roundtrip:
            states = M.decode_state(json.loads(json.dumps(
                M.encode_state(states), allow_nan=False)))
        states, c = M.update_states(states, rows)
        conf |= {(nid, us, pd.Timestamp(gen).tz_convert(None)) for nid, us in c}
    return conf, states


@pytest.fixture(scope="module")
def runs():
    return synthetic_runs()


class TestOracleEquivalence:
    def test_same_confirmed_maneuvers_at_the_same_runs(self, runs):
        want, raw, _ = _oracle_confirmations(runs)
        got, _ = _prod_confirmations(runs, roundtrip=True)
        assert got == want
        # The fixture must exercise the detector, not trivially agree on nothing:
        # confirmations AND raw flags the confirmation step rejected.
        assert len(want) >= 20, len(want)
        assert raw > len(want), (raw, len(want))

    def test_fixture_covers_every_flag_kind(self, runs):
        _, _, out = _oracle_confirmations(runs)
        c = out[out.flag_c]
        assert c.flag_raise.any() and c.flag_lower.any() and c.flag_i.any()
        # flip-floppers raise raw flags that confirmation rejects
        flips = {40000 + k for k in range(36) if KINDS[k % len(KINDS)] == "flip"}
        f = out[out.NORAD_CAT_ID.isin(flips)]
        assert f.flag.sum() > f.flag_c.sum()

    def test_in_memory_equals_roundtrip(self, runs):
        a, sa = _prod_confirmations(runs, roundtrip=False)
        b, sb = _prod_confirmations(runs, roundtrip=True)
        assert a == b
        assert M.encode_state(sa) == M.encode_state(sb)   # lossless state

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_other_seeds(self, seed):
        r = synthetic_runs(seed=seed, n_obj=24, n_runs=90)
        assert _prod_confirmations(r)[0] == _oracle_confirmations(r)[0]


def _warm(nid=25544, alt=420.0, steps=6, decay=-40.0, noise=None):
    """A warmed-up object: `steps` quiet sets 8 h apart on a drag trend."""
    st = None
    a = (6378.137 + alt) * 1000
    t = T0
    rng = np.random.default_rng(nid)
    for _ in range(steps):
        a += decay / 3
        a_obs = a + (rng.normal(0, noise) if noise else 0.0)
        st, conf = M.step(st, M.epoch_us(t), _n_from_a_m(a_obs), 51.64)
        assert conf is None
        t += timedelta(hours=8)
    return st, a, t


class TestDetector:
    def test_reboost_confirmed_on_the_next_set(self):
        st, a, t = _warm()
        st, conf = M.step(st, M.epoch_us(t), _n_from_a_m(a + 2000), 51.64)  # +2 km burn
        assert conf is None and st.pend is not None   # flagged, not yet confirmed
        burn_us = st.ep_us
        st, conf = M.step(st, M.epoch_us(t + timedelta(hours=8)),
                          _n_from_a_m(a + 2000 - 13), 51.64)
        assert conf == burn_us and st.last_mnv_us == burn_us

    def test_flip_flop_is_not_confirmed(self):
        st, a, t = _warm()
        st, _ = M.step(st, M.epoch_us(t), _n_from_a_m(a + 450), 51.64)   # other fit source
        assert st.pend is not None
        st, conf = M.step(st, M.epoch_us(t + timedelta(hours=8)), _n_from_a_m(a - 15), 51.64)
        assert conf is None and st.last_mnv_us is None

    def test_passive_noise_never_flags(self):
        st, a, t = _warm(steps=60, noise=4.0)
        assert st.pend is None and st.last_mnv_us is None

    def test_warmup_blocks_flags(self):
        st, a, t = _warm(steps=3)          # 2 quiet steps < WARMUP
        st, _ = M.step(st, M.epoch_us(t), _n_from_a_m(a + 5000), 51.64)
        assert st.pend is None

    def test_drag_never_excuses_a_raise_and_decay_is_not_a_lowering(self):
        # Fast decay (-600 m/d → -200 m expected per 8 h, thr = 100 m): a step
        # 1.75x the expected decay (res = -150 m) is drag variability, not a
        # lowering — it is past -thr but within -BETA·|expected|.
        st, a, t = _warm(decay=-600.0)
        assert max(100.0, 6 * st.sa) == 100.0
        st, _ = M.step(st, M.epoch_us(t), _n_from_a_m(a - 200 - 150), 51.64)
        assert st.pend is None
        # A positive learned trend (+300 m/d → +100 m expected per 8 h; its
        # quiet residuals pin scale_a at the 50 m clamp, so thr = 300 m) must not
        # excuse a raise: +350 m flags only because expected is capped at 0
        # (uncapped, res = 350 - 100 = 250 < thr).
        st2, a2, t2 = _warm(decay=+300.0)
        assert st2.trend > 0 and max(100.0, 6 * st2.sa) == 300.0
        st2, _ = M.step(st2, M.epoch_us(t2), _n_from_a_m(a2 + 350), 51.64)
        assert st2.pend is not None

    def test_old_or_same_epoch_is_ignored(self):
        st, a, t = _warm()
        before = M.encode_state({1: st})
        M.step(st, st.ep_us, _n_from_a_m(a + 5000), 51.64)
        M.step(st, st.ep_us - 10**9, _n_from_a_m(a + 5000), 51.64)
        assert M.encode_state({1: st}) == before

    def test_merged_refit_does_not_step(self):
        st, a, t = _warm()
        nq, tr = st.nq, st.trend
        st, _ = M.step(st, st.ep_us + 60 * 10**6, _n_from_a_m(a + 5000), 51.64)  # 1 min later
        assert st.pend is None and (st.nq, st.trend) == (nq, tr)

    def test_plane_change(self):
        st, a, t = _warm()
        st, _ = M.step(st, M.epoch_us(t), _n_from_a_m(a), 51.70)
        assert st.pend is not None and st.pend[7] == 2
        st, conf = M.step(st, M.epoch_us(t + timedelta(hours=8)), _n_from_a_m(a), 51.70)
        assert conf is not None


class TestEpochParsing:
    @pytest.mark.parametrize("v", ["2026-09-01T05:17:00.000000", "2026-09-01T05:17:00Z",
                                   "2026-09-01T07:17:00+02:00",
                                   pd.Timestamp("2026-09-01T05:17:00", tz="UTC"),
                                   pd.Timestamp("2026-09-01T05:17:00"),
                                   datetime(2026, 9, 1, 5, 17)])
    def test_forms_agree(self, v):
        assert M.epoch_us(v) == M.epoch_us(T0)

    @pytest.mark.parametrize("v", [None, "garbage", 12.5, pd.NaT])
    def test_unusable_is_none(self, v):
        assert M.epoch_us(v) is None

    def test_microseconds_exact(self):
        assert M.epoch_us("2026-11-16T19:40:43.042368") % 10**6 == 42368


class TestStateSerialization:
    def _states(self):
        st, a, t = _warm()
        st, _ = M.step(st, M.epoch_us(t), _n_from_a_m(a + 2000), 51.64)
        other = M.ObjState(ep_us=M.epoch_us(T0), n=15.1, i=97.4, last_mnv_us=M.epoch_us(T0) - 5)
        return {25544: st, 7: other}

    def test_roundtrip_is_lossless(self):
        s = self._states()
        back = M.decode_state(json.loads(json.dumps(M.encode_state(s), allow_nan=False)))
        assert back == s

    @pytest.mark.parametrize("bad", [None, [], "x", {"v": 99}, {"v": 1, "id": [1]},
                                     {"v": 1, "id": "x", "ep": [], "n": [], "i": [], "tr": [],
                                      "sa": [], "si": [], "q": [], "c": []}])
    def test_wrong_shape_is_a_cold_start(self, bad):
        assert M.decode_state(bad) == {}

    def test_bad_rows_and_bad_sparse_entries_are_dropped_individually(self):
        enc = M.encode_state(self._states())
        enc["n"][0] = "15.1"                  # id 7: string mean motion → dropped
        enc["pend"]["25544"][2] = 1.5         # epoch must be an int → pend dropped
        enc["lm"]["7"] = True                 # bool is not an epoch
        back = M.decode_state(enc)
        assert 7 not in back and back[25544].pend is None

    def test_bool_counters_rejected(self):
        enc = M.encode_state(self._states())
        enc["q"][1] = True
        assert 25544 not in M.decode_state(enc)


class TestAnnotate:
    GEN = "2026-09-03T00:00:00Z"

    def _states(self, ha=None, hb=None):
        g = M.epoch_us(self.GEN)
        s = {}
        for nid, h in ((1, ha), (2, hb)):
            st = M.ObjState(ep_us=g, n=15.0, i=51.0)
            if h is not None:
                st.last_mnv_us = g - int(h * 3.6e9)
            s[nid] = st
        return s

    def _c(self):
        return {"a": 1, "b": 2, "miss_km": 1.0}

    def test_who_and_min_hours(self):
        out = M.annotate_conjunctions([self._c()], self._states(30.04, 12.0), self.GEN)[0]
        assert out["mnv_h"] == 12.0 and out["mnv_who"] == "ab"
        out = M.annotate_conjunctions([self._c()], self._states(None, 5.0), self.GEN)[0]
        assert out["mnv_who"] == "b"
        out = M.annotate_conjunctions([self._c()], self._states(47.9, 60.0), self.GEN)[0]
        assert out["mnv_who"] == "a" and out["mnv_h"] == 47.9

    def test_absent_beyond_48h_or_never(self):
        for ha, hb in ((48.2, None), (None, None), (100.0, 49.0)):
            out = M.annotate_conjunctions([self._c()], self._states(ha, hb), self.GEN)[0]
            assert "mnv_h" not in out and "mnv_who" not in out

    def test_pure(self):
        c = self._c()
        M.annotate_conjunctions([c], self._states(1.0, 1.0), self.GEN)
        assert c == self._c()


# --- snapshot wiring ---------------------------------------------------------

def _catalog(rows):
    """GPFetcher-shaped frame from (id, EPOCH iso, n, i) rows."""
    return pd.DataFrame([{
        "object_name": f"SAT-{nid}", "object_id": "", "norad_cat_id": nid,
        "classification": "U", "epoch": pd.Timestamp(ep, tz="UTC"),
        "epoch_age_days": 0.5, "mean_motion": n, "eccentricity": 0.0002,
        "inclination": i, "ra_of_asc_node": 0.0, "arg_of_pericenter": 0.0,
        "mean_anomaly": 0.0, "bstar": 1e-4, "mean_motion_dot": 0.0,
        "mean_motion_ddot": 0.0, "ephemeris_type": 0, "element_set_no": 999,
        "rev_at_epoch": 1, "object_type": "PAYLOAD"} for nid, ep, n, i in rows])


def _event(a, b):
    return {"sat1_norad_id": a, "sat1_name": f"SAT-{a}", "sat1_object_type": "PAYLOAD",
            "sat2_norad_id": b, "sat2_name": f"SAT-{b}", "sat2_object_type": "PAYLOAD",
            "tca": "2026-09-10T00:00:00+00:00", "miss_distance_km": 1.0,
            "relative_speed_km_s": 10.0, "r_km": 0.1, "t_km": 0.2, "n_km": 0.3,
            "screening_regime": "LEO 1"}


def _chain(runs, events_for=lambda rows: [], prev=None):
    """Drive build_snapshot run by run through a JSON --prev chain."""
    snaps = []
    for gen, rows in runs:
        snap = build_snapshot(_catalog(rows), events_for(rows), {}, _iso(gen) + "Z", prev=prev)
        prev = json.loads(json.dumps(snap, allow_nan=False))
        snaps.append(prev)
    return snaps


class TestSnapshotWiring:
    def test_chain_matches_the_oracle_and_labels_events(self, runs):
        want, _, _ = _oracle_confirmations(runs)
        evs = lambda rows: [_event(rows[0][0], rows[1][0])] if len(rows) > 1 else []  # noqa: E731
        snaps = _chain(runs, evs)
        # The state carried through real snapshots is the oracle-locked detector
        # state, except that `lm` older than the tag window is pruned …
        got = M.encode_state(M.decode_state(snaps[-1]["maneuver_state"]))
        ref = M.encode_state(_prod_confirmations(runs)[1])
        assert {k: v for k, v in got.items() if k != "lm"} == \
            {k: v for k, v in ref.items() if k != "lm"}
        assert set(got["lm"]) < set(ref["lm"])
        # … which is lossless for the labels: every run's tags equal those from
        # an unpruned, never-serialized replay.
        states = {}
        for (gen, rows), snap in zip(runs, snaps):
            states, _ = M.update_states(states, rows)
            ref_tags = M.annotate_conjunctions(
                [{"a": c["a"], "b": c["b"]} for c in snap["conjunctions"]],
                states, _iso(gen) + "Z")
            assert [(c.get("mnv_h"), c.get("mnv_who")) for c in snap["conjunctions"]] == \
                [(c.get("mnv_h"), c.get("mnv_who")) for c in ref_tags]
        assert sum(s["meta"]["maneuvers"]["confirmed"] for s in snaps) == len(want)
        assert snaps[0]["meta"]["maneuvers"]["cold_start"] is True
        assert snaps[-1]["meta"]["maneuvers"]["cold_start"] is False
        tagged = [c for s in snaps for c in s["conjunctions"] if "mnv_h" in c]
        assert tagged and all(0 <= c["mnv_h"] <= 48 and c["mnv_who"] in ("a", "b", "ab")
                              for c in tagged)

    def test_annotation_never_changes_the_screened_fields(self, runs):
        gen, rows = runs[-1]
        prev = _chain(runs[:-1])[-1]
        evs = [_event(r[0], s[0]) for r, s in zip(rows, rows[1:])]
        with_m = build_snapshot(_catalog(rows), evs, {}, _iso(gen) + "Z", prev=prev)
        without = build_snapshot(_catalog(rows), evs, {}, _iso(gen) + "Z", prev=None)
        strip = lambda cs: [{k: v for k, v in c.items() if not k.startswith("mnv_")} for c in cs]  # noqa: E731
        assert any("mnv_h" in c for c in with_m["conjunctions"])
        assert strip(with_m["conjunctions"]) == strip(without["conjunctions"])

    def test_old_snapshot_as_prev_is_a_cold_start(self, runs):
        gen, rows = runs[0]
        pre = {"meta": {"generated_at": "2026-08-31T00:00:00Z"}, "conjunctions": []}
        snap = build_snapshot(_catalog(rows), [], {}, _iso(gen) + "Z", prev=pre)
        assert snap["meta"]["maneuvers"]["cold_start"] is True
        assert len(snap["maneuver_state"]["id"]) == len(rows)

    @pytest.mark.parametrize("bad", ["junk", 5, {"v": 1, "id": [1, 2]}, {"v": 2}])
    def test_malformed_prev_state_never_fatal(self, runs, bad):
        gen, rows = runs[0]
        snap = build_snapshot(_catalog(rows), [], {}, _iso(gen) + "Z",
                              prev={"meta": {}, "conjunctions": [], "maneuver_state": bad})
        assert snap["meta"]["maneuvers"]["cold_start"] is True and "maneuver_state" in snap

    def test_detector_failure_costs_only_the_label(self, monkeypatch, runs):
        gen, rows = runs[0]
        def boom(*a, **k):
            raise RuntimeError("detector bug")
        monkeypatch.setattr(M, "update_states", boom)
        conj = [{"a": 1, "b": 2}]
        out, state, info = apply_maneuvers(conj, _catalog(rows), _iso(gen) + "Z", None)
        assert out is conj and state is None and "detector bug" in info["error"]
        snap = build_snapshot(_catalog(rows), [_event(1, 2)], {}, _iso(gen) + "Z")
        assert "maneuver_state" not in snap and "error" in snap["meta"]["maneuvers"]

    def test_absent_objects_kept_until_stale(self, runs):
        gen, rows = runs[-1]
        prev = _chain(runs[:-1])[-1]
        n_prev = len(prev["maneuver_state"]["id"])
        snap = build_snapshot(_catalog(rows[:1]), [], {}, _iso(gen) + "Z", prev=prev)
        assert len(snap["maneuver_state"]["id"]) == n_prev      # transient gap: kept
        late = gen + timedelta(days=MANEUVER_STATE_RETAIN_D + 1)
        snap = build_snapshot(_catalog(rows[:1]), [], {}, _iso(late) + "Z", prev=prev)
        assert snap["maneuver_state"]["id"] == [rows[0][0]]     # long gone: pruned

    def test_archive_keeps_labels_not_state(self, runs):
        evs = lambda rows: [_event(r[0], s[0]) for r, s in zip(rows, rows[1:])]  # noqa: E731
        snaps = _chain(runs, evs)
        snap = next(s for s in reversed(snaps) if any("mnv_h" in c for c in s["conjunctions"]))
        rec = archive_record(snap)
        assert "maneuver_state" not in rec
        assert sum("mnv_h" in c for c in rec["conjunctions"]) == \
            sum("mnv_h" in c for c in snap["conjunctions"])

    def test_snapshot_is_strict_json(self, runs):
        snap = _chain(runs[:20])[-1]
        json.dumps(snap, allow_nan=False)
        assert all(isinstance(x, int) for x in snap["maneuver_state"]["ep"])


class TestReplayCLI:
    """The seed path deploy.yml takes when the live state is missing."""

    def _write(self, tmp_path, runs):
        import gzip
        for gen, rows in runs:
            rec = {"meta": {"generated_at": _iso(gen) + "Z"}, "conjunctions": [],
                   "elements": {"NORAD_CAT_ID": [r[0] for r in rows],
                                "EPOCH": [r[1] for r in rows],
                                "MEAN_MOTION": [r[2] for r in rows],
                                "INCLINATION": [r[3] for r in rows]}}
            name = _iso(gen).replace(":", "-") + "Z.json.gz"
            with gzip.open(tmp_path / name, "wt", encoding="utf-8") as f:
                json.dump(rec, f)

    def test_seed_equals_the_incremental_state_and_skips_bad_records(self, tmp_path, runs):
        self._write(tmp_path, runs)
        (tmp_path / "2026-12-31T00-00-00Z.json.gz").write_bytes(b"not gzip")
        out = tmp_path / "seed.json"
        assert M._main(["replay", str(tmp_path), "--quiet", "--state-out", str(out)]) == 0
        seed = json.loads(out.read_text())
        assert seed == json.loads(json.dumps(M.encode_state(_prod_confirmations(runs)[1])))

    def test_last_n(self, tmp_path, runs):
        self._write(tmp_path, runs)
        out = tmp_path / "seed.json"
        M._main(["replay", str(tmp_path), "--quiet", "--last", "10", "--state-out", str(out)])
        ref = M.encode_state(_prod_confirmations(runs[-10:])[1])
        assert json.loads(out.read_text()) == json.loads(json.dumps(ref))
