#!/usr/bin/env python3
"""Tests for backend/core/conjunctions.py — the Python stages of the
conjunction pipeline (Task 6.6: fine_filter).

Validates:
- Exact TCA + miss distance vs an independent 0.01 s brute-force reference
- The week-plan criteria: refined miss <= medium-filter flagged distance,
  TCA inside the bracket
- Integration with teme_to_rtn (orthonormality of the returned states)
- Edge-bracket widening, identical-pair flat objective, error paths
"""

import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import orbitcore
from core.conjunctions import (
    ConjunctionScreener,
    _dedupe_to_unique_pairs,
    fine_filter,
    fine_filter_batch,
    run_screen,
)
from core.coordinate_transforms import teme_to_rtn, utc_to_jd
from core.screening_volumes import LEO_1, ScreeningVolume

# A deliberately generous ellipsoid (10 km on every axis) for tests that need an
# encounter to fall *inside* the volume — the real LEO-1 radial axis (0.4 km) is
# tighter than the crosser fixture's radial separation (see the exclusion test).
_GENEROUS = ScreeningVolume("TEST", 10.0, 10.0, 10.0)

# --- Fixtures: ISS + the fast-crosser (same as TestMediumFilter) ----------

ISS_L1 = "1 25544U 98067A   24056.27396747  .00015798  00000+0  28508-3 0  9991"
ISS_L2 = "2 25544  51.6415  32.0835 0004287  51.5994  12.5648 15.49571617441044"

# Crosser ground truth (this file, test_exact_tca_and_miss_vs_brute_force):
# encounter near tsince ~122.72 min at v_rel ~12 km/s.
CROSSER_DMO_DEG = 180.2
CROSSER_TCA_MIN_APPROX = 122.72


def _parsed():
    from sgp4.api import Satrec as PySatrec, WGS72
    return PySatrec.twoline2rv(ISS_L1, ISS_L2, WGS72)


def _iss_epoch_jd():
    p = _parsed()
    return p.jdsatepoch + p.jdsatepochF


def _make_variant(dmo_deg=0.0, dnodeo_deg=0.0, bstar=None):
    p = _parsed()
    return orbitcore.sgp4init(
        orbitcore.GravConst.WGS72, "a", "25544",
        p.jdsatepoch + p.jdsatepochF - 2433281.5,
        p.bstar if bstar is None else bstar,
        p.ndot, p.nddot, p.ecco, p.argpo, p.inclo,
        (p.mo + math.radians(dmo_deg)) % (2 * math.pi),
        p.no_kozai,
        (p.nodeo + math.radians(dnodeo_deg)) % (2 * math.pi),
    )


def _crosser_pair():
    return (_make_variant(),
            _make_variant(dmo_deg=CROSSER_DMO_DEG, dnodeo_deg=180.0))


class TestFineFilter:
    """fine_filter(): exact TCA + miss distance inside a flagged window."""

    def test_exact_tca_and_miss_vs_brute_force(self):
        """Independent reference: 0.01 s brute-force grid around the
        encounter. The optimizer must match it to ~0.05 s in time and
        ~10 m in distance. (A 1 s grid is NOT good enough as truth: at
        12 km/s closing speed it overshoots the true miss by several km —
        which is exactly why the fine filter exists.)"""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()

        # 0.01 s grid spanning +-3 s of the approximate TCA
        t0 = CROSSER_TCA_MIN_APPROX - 0.05
        times = [t0 + i * (0.01 / 60.0) for i in range(600)]
        ta = orbitcore.propagate_batch([a] * len(times), times)
        tb = orbitcore.propagate_batch([b] * len(times), times)
        dists = [math.dist(ta[k][0], tb[k][0]) for k in range(len(times))]
        k_min = dists.index(min(dists))
        ref_tca_min, ref_miss = times[k_min], dists[k_min]

        out = fine_filter(a, b,
                          jd0 + (CROSSER_TCA_MIN_APPROX - 1.0) / 1440.0,
                          jd0 + (CROSSER_TCA_MIN_APPROX + 1.0) / 1440.0)
        tca_min = (out["jd_tca"] - jd0) * 1440.0
        assert abs(tca_min - ref_tca_min) * 60.0 < 0.05  # seconds
        assert abs(out["miss_km"] - ref_miss) < 0.01     # km
        assert 11.0 < out["rel_speed_km_s"] < 13.0       # fast crosser

    def test_refined_miss_not_above_flagged_distance(self):
        """Week-plan criterion: the refined miss is a minimum, so it must
        be <= the medium filter's sampled distance for that window."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        rows = orbitcore.medium_filter(
            [a, b], [(0, 1)], jd0, jd0 + 0.25, 60.0, 50.0)
        # the window nearest the known encounter
        _, _, jd_flag, d_flag = min(
            rows, key=lambda r: abs((r[2] - jd0) * 1440.0
                                    - CROSSER_TCA_MIN_APPROX))
        step_day = 60.0 / 86400.0
        out = fine_filter(a, b, jd_flag - step_day, jd_flag + step_day)
        assert out["miss_km"] <= d_flag
        assert out["miss_km"] < 50.0  # genuinely sub-threshold

    def test_tca_inside_bracket(self):
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        lo = jd0 + (CROSSER_TCA_MIN_APPROX - 1.0) / 1440.0
        hi = jd0 + (CROSSER_TCA_MIN_APPROX + 1.0) / 1440.0
        out = fine_filter(a, b, lo, hi)
        assert lo <= out["jd_tca"] <= hi

    def test_states_feed_rtn_transform(self):
        """Returned TEME states integrate with teme_to_rtn: the RTN miss
        vector's norm equals miss_km (orthonormality, cross-module)."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        out = fine_filter(a, b,
                          jd0 + (CROSSER_TCA_MIN_APPROX - 1.0) / 1440.0,
                          jd0 + (CROSSER_TCA_MIN_APPROX + 1.0) / 1440.0)
        r, t, n = teme_to_rtn(out["pos_a_teme"], out["vel_a_teme"],
                              out["pos_b_teme"])
        norm = math.sqrt(r * r + t * t + n * n)
        assert abs(norm - out["miss_km"]) < 1e-9

    def test_tca_utc_consistent_with_jd(self):
        """tca_utc must round-trip to jd_tca (via the independent
        utc_to_jd helper) within ~1 ms."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        out = fine_filter(a, b,
                          jd0 + (CROSSER_TCA_MIN_APPROX - 1.0) / 1440.0,
                          jd0 + (CROSSER_TCA_MIN_APPROX + 1.0) / 1440.0)
        assert out["tca_utc"].tzinfo == timezone.utc
        jd_w, jd_f = utc_to_jd(out["tca_utc"])
        assert abs((jd_w + jd_f) - out["jd_tca"]) * 86400.0 < 1e-3

    def test_edge_bracket_widens_and_recovers(self):
        """Bracket deliberately placed AFTER the encounter (124..126 min):
        minimum lands on the low edge -> one widen recovers the true TCA
        outside the original bracket."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        out = fine_filter(a, b, jd0 + 124.0 / 1440.0, jd0 + 126.0 / 1440.0)
        tca_min = (out["jd_tca"] - jd0) * 1440.0
        assert abs(tca_min - CROSSER_TCA_MIN_APPROX) < 0.1
        assert out["miss_km"] < 10.0

    def test_identical_pair_flat_objective(self):
        """Same elements twice: distance is 0 everywhere (flat objective)
        — must return 0 without errors."""
        jd0 = _iss_epoch_jd()
        out = fine_filter(_make_variant(), _make_variant(),
                          jd0, jd0 + 2.0 / 1440.0)
        assert out["miss_km"] == 0.0
        assert out["rel_speed_km_s"] == 0.0

    def test_unpropagatable_bracket_raises(self):
        """A heavy-drag satellite decays long before a far-future bracket:
        every evaluation fails -> RuntimeError, not a silent answer."""
        good = _make_variant()
        decayer = _make_variant(bstar=0.1)
        jd0 = _iss_epoch_jd()
        try:
            fine_filter(good, decayer, jd0 + 60.0, jd0 + 60.0 + 2.0 / 1440.0)
            assert False, "should have raised RuntimeError"
        except RuntimeError:
            pass

    def test_bad_bracket_order_raises(self):
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        try:
            fine_filter(a, b, jd0 + 1.0, jd0)
            assert False, "should have raised ValueError"
        except ValueError:
            pass


class TestFineFilterBatch:
    """fine_filter_batch(): the Phase 7.3 batched fine stage. It must reproduce
    the scipy per-window oracle (fine_filter) within SGP4-meaningful tolerance,
    and isolate decayed / co-moving / edge windows exactly as the oracle does."""

    def _windows(self):
        """Several real medium-filter windows for the crosser (the crossing
        repeats, so a multi-hour scan yields a handful)."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        rows = orbitcore.medium_filter(
            [a, b], [(0, 1)], jd0, jd0 + 0.25, 60.0, 50.0)
        return a, b, rows

    def test_matches_oracle_across_all_windows(self):
        """The headline correctness claim: on every window the batched Newton
        TCA solve agrees with the scipy oracle to <10 m miss, <50 ms TCA, and
        <1 mm/s relative speed — i.e. the speedup costs nothing in accuracy."""
        a, b, rows = self._windows()
        assert len(rows) >= 2
        batch = fine_filter_batch([a, b], rows, 60.0)
        assert len(batch) == len(rows)

        step_day = 60.0 / 86400.0
        for (i, j, jd_flag, _d), got in zip(rows, batch):
            ref = fine_filter(a, b, jd_flag - step_day, jd_flag + step_day)
            assert got is not None
            assert abs(got["miss_km"] - ref["miss_km"]) < 0.01          # 10 m
            assert abs(got["jd_tca"] - ref["jd_tca"]) * 86400.0 < 0.05  # 50 ms
            assert abs(got["rel_speed_km_s"] - ref["rel_speed_km_s"]) < 1e-3

    def test_matches_brute_force_ground_truth(self):
        """Independent ground-truth anchor — NOT via scipy. A 0.01 s brute-force
        grid around the encounter is the truth; the batched Newton TCA must match
        it to <50 ms / <10 m. This pins the new method to first principles so it
        can never silently inherit an optimizer error from the oracle path."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        # 0.01 s grid spanning +-3 s of the approximate TCA (a, b share epoch).
        t0 = CROSSER_TCA_MIN_APPROX - 0.05
        times = [t0 + i * (0.01 / 60.0) for i in range(600)]
        ta = orbitcore.propagate_batch([a] * len(times), times)
        tb = orbitcore.propagate_batch([b] * len(times), times)
        dists = [math.dist(ta[k][0], tb[k][0]) for k in range(len(times))]
        k_min = dists.index(min(dists))
        ref_tca_min, ref_miss = times[k_min], dists[k_min]

        jd_flag = jd0 + CROSSER_TCA_MIN_APPROX / 1440.0
        out = fine_filter_batch([a, b], [(0, 1, jd_flag, 0.0)], 60.0)
        assert out[0] is not None
        tca_min = (out[0]["jd_tca"] - jd0) * 1440.0
        assert abs(tca_min - ref_tca_min) * 60.0 < 0.05   # <50 ms vs ground truth
        assert abs(out[0]["miss_km"] - ref_miss) < 0.01    # <10 m vs ground truth

    def test_returned_states_are_rtn_consistent(self):
        """The returned TEME states must be self-consistent end to end: the
        norm of (pos_a - pos_b) equals the reported miss (feeds teme_to_rtn)."""
        a, b, rows = self._windows()
        for got in fine_filter_batch([a, b], rows, 60.0):
            assert got is not None
            dr = [got["pos_a_teme"][k] - got["pos_b_teme"][k] for k in range(3)]
            assert abs(math.sqrt(sum(x * x for x in dr)) - got["miss_km"]) < 1e-6

    def test_empty_rows_returns_empty(self):
        a, b = _crosser_pair()
        assert fine_filter_batch([a, b], [], 60.0) == []

    def test_result_aligns_with_rows(self):
        """results[k] corresponds to rows[k] — the index contract run_screen
        relies on to map (i, j) back to satellite identity."""
        a, b, rows = self._windows()
        batch = fine_filter_batch([a, b], rows, 60.0)
        assert len(batch) == len(rows)
        # every window for this fast, well-separated crosser refines cleanly
        assert all(r is not None for r in batch)

    def test_chunking_is_transparent(self, monkeypatch):
        """Refining in tiny chunks gives the same per-window result as one big
        pass — the chunk boundary mustn't drop, reorder, or misalign windows."""
        import core.conjunctions as conj

        a, b, rows = self._windows()
        assert len(rows) >= 2
        whole = fine_filter_batch([a, b], rows, 60.0)
        monkeypatch.setattr(conj, "_FINE_CHUNK", 1)   # force one window/chunk
        chunked = fine_filter_batch([a, b], rows, 60.0)
        assert len(whole) == len(chunked) == len(rows)
        for w, c in zip(whole, chunked):
            assert (w is None) == (c is None)
            if w is not None:
                assert abs(w["miss_km"] - c["miss_km"]) < 1e-9
                assert abs(w["jd_tca"] - c["jd_tca"]) < 1e-12

    def test_co_moving_pair_is_finite_not_nan(self):
        """A co-moving pair (|Δv|≈0) has no defined crossing; the Newton step
        must stay put rather than divide by zero — a finite ~0 km miss, not
        NaN. (Same elements twice => distance 0 for all time.)"""
        a, _ = _crosser_pair()
        jd0 = _iss_epoch_jd()
        rows = [(0, 1, jd0 + 0.05, 0.0)]          # one synthetic window
        out = fine_filter_batch([a, a], rows, 60.0)
        assert len(out) == 1 and out[0] is not None
        assert math.isfinite(out[0]["miss_km"])
        assert out[0]["miss_km"] < 1e-6
        assert out[0]["rel_speed_km_s"] < 1e-9

    def test_decayed_window_returns_none(self):
        """A pair that can't be propagated across its window (heavy-drag sat,
        far-future time) comes back as None — the batched analogue of
        fine_filter raising, so run_screen can drop just that window."""
        good = _make_variant()
        decayer = _make_variant(bstar=0.1)
        jd0 = _iss_epoch_jd()
        rows = [(0, 1, jd0 + 60.0 + 1.0 / 1440.0, 0.0)]   # ~60 days out
        out = fine_filter_batch([good, decayer], rows, 60.0)
        assert out == [None]

    def test_edge_window_widens_and_recovers(self):
        """A window whose sample sits a full step past the encounter lands the
        minimum on the bracket edge; the one-shot widen recovers the true TCA
        outside the original bracket — matching fine_filter's safety net."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        # jd_flag at ~125 min -> bracket [124,126] min; true TCA ~122.7 is below.
        rows = [(0, 1, jd0 + 125.0 / 1440.0, 0.0)]
        out = fine_filter_batch([a, b], rows, 60.0)
        assert out[0] is not None
        tca_min = (out[0]["jd_tca"] - jd0) * 1440.0
        assert abs(tca_min - CROSSER_TCA_MIN_APPROX) < 0.1
        assert out[0]["miss_km"] < 10.0


# --- ConjunctionScreener / run_screen (Task 6.7) --------------------------

def _crosser_meta():
    """Index-aligned metadata for _crosser_pair(): two co-altitude ISS-band
    objects (so coarse_filter always pairs them). Carries the 7.2 fields
    (object_id / eccentricity / period_min) so the SFS path can classify them
    (both -> LEO 1); different launch ids so they're not suppressed as same-launch."""
    return [
        {"norad_id": 25544, "name": "ISS (ZARYA)", "object_type": "PAYLOAD",
         "epoch_age_days": 1.0, "periapsis_km": 410.0, "apoapsis_km": 420.0,
         "object_id": "1998-067A", "eccentricity": 0.0004, "period_min": 92.9},
        {"norad_id": 90000, "name": "ISS CROSSER", "object_type": "DEBRIS",
         "epoch_age_days": 1.0, "periapsis_km": 410.0, "apoapsis_km": 420.0,
         "object_id": "2099-001A", "eccentricity": 0.0004, "period_min": 92.9},
    ]


def _iss_epoch_dt():
    """The ISS TLE epoch as a UTC datetime — screening start so tsince=0 is
    the element epoch and the ~122.7 min encounter falls in the window."""
    p = _parsed()
    yr, mo, dy, hr, mn, sec = orbitcore.invjday(p.jdsatepoch, p.jdsatepochF)
    return (datetime(yr, mo, dy, hr, 0, 0, tzinfo=timezone.utc)
            + timedelta(minutes=mn, seconds=sec))


class TestConjunctionScreener:
    """run_screen()/ConjunctionScreener: the full coarse->medium->fine->RTN
    cascade, driven deterministically by the crosser fixture."""

    def test_screen_finds_crosser(self):
        a, b = _crosser_pair()
        ev = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0)
        assert len(ev) >= 1                      # ~8 windows per 6 h
        top = ev[0]
        assert top["miss_distance_km"] < 7.0     # documented refined min ~6.6 km
        assert 11.0 < top["relative_speed_km_s"] < 13.0
        assert {top["sat1_norad_id"], top["sat2_norad_id"]} == {25544, 90000}
        assert top["sat1_name"] and top["sat2_name"]

    def test_events_sorted_by_miss(self):
        a, b = _crosser_pair()
        ev = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0)
        misses = [e["miss_distance_km"] for e in ev]
        assert misses == sorted(misses)

    def test_rtn_norm_equals_miss(self):
        """Each event's RTN components reconstruct its miss distance
        (orthonormal frame ⇒ r²+t²+n² = miss²)."""
        a, b = _crosser_pair()
        ev = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0)
        for e in ev:
            norm = math.sqrt(e["r_km"]**2 + e["t_km"]**2 + e["n_km"]**2)
            assert abs(norm - e["miss_distance_km"]) < 1e-9

    def test_threshold_excludes_distant_pair(self):
        """A 1 km report threshold rejects the 6.6 km crosser even though the
        velocity-aware medium bound brackets it."""
        a, b = _crosser_pair()
        ev = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 1.0)
        assert ev == []

    def test_disjoint_altitude_coarse_cut(self):
        """Non-overlapping altitude bands → coarse_filter drops the pair;
        nothing reaches medium/fine."""
        a, b = _crosser_pair()
        meta = _crosser_meta()
        meta[1] = {**meta[1], "periapsis_km": 20000.0, "apoapsis_km": 20200.0}
        ev = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)
        assert ev == []

    def test_screener_delegates_to_propagator(self):
        """ConjunctionScreener pulls satrecs+meta from a propagator's
        get_all_satrecs() and returns what run_screen would."""
        a, b = _crosser_pair()
        meta = _crosser_meta()

        class _FakeProp:
            def get_all_satrecs(self):
                return [a, b], meta

        start = _iss_epoch_dt()
        direct = run_screen([a, b], meta, start, 6.0, 50.0)
        via = ConjunctionScreener(_FakeProp()).screen(start, 6.0, 50.0)
        assert len(via) == len(direct)
        assert via[0]["miss_distance_km"] == direct[0]["miss_distance_km"]

    def test_event_maps_correct_indices(self):
        """A decoy at index 0 (disjoint altitude → coarse-cut) shifts the real
        pair to satrecs (1, 2). Events must carry meta[1]/meta[2]'s identities,
        proving the index→identity mapping isn't hardcoded to (0, 1)."""
        a, b = _crosser_pair()
        decoy = _make_variant(dmo_deg=30.0)
        iss_meta, crosser_meta = _crosser_meta()
        meta = [
            {"norad_id": 11111, "name": "DECOY", "object_type": "DEBRIS",
             "epoch_age_days": 1.0,
             "periapsis_km": 19000.0, "apoapsis_km": 20500.0},  # GPS band
            iss_meta,        # index 1
            crosser_meta,    # index 2
        ]
        ev = run_screen([decoy, a, b], meta, _iss_epoch_dt(), 6.0, 50.0)
        assert len(ev) >= 1
        for e in ev:
            assert {e["sat1_norad_id"], e["sat2_norad_id"]} == {25544, 90000}
            assert "DECOY" not in (e["sat1_name"], e["sat2_name"])

    def test_misaligned_inputs_raise(self):
        """satrecs/meta length mismatch would misattribute events — run_screen
        must reject it up front, not silently screen."""
        a, b = _crosser_pair()
        try:
            run_screen([a, b], _crosser_meta()[:1], _iss_epoch_dt(), 6.0, 50.0)
            assert False, "should have raised ValueError"
        except ValueError:
            pass

    def test_full_pipeline_deterministic(self):
        """End-to-end anchor (the 6.10 deliverable): run the full cascade over a
        fixed window and confirm it reproduces the encounter that 6.6's
        independent 0.01 s brute force validated — TCA ≈122.72 min from the ISS
        epoch, miss ≈6.60 km — within tolerance. The crossing repeats, so we
        locate that specific window rather than the global-closest one.
        Fixed TLEs + fixed window ⇒ reproducible."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        ev = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0)
        assert ev, "no events from the full pipeline"

        def tca_min(e):
            jw, jf = utc_to_jd(datetime.fromisoformat(e["tca"]))
            return ((jw + jf) - jd0) * 1440.0

        near = min(ev, key=lambda e: abs(tca_min(e) - 122.72))
        # The full pipeline finds the brute-force-validated encounter...
        assert abs(tca_min(near) - 122.72) < 0.1
        # ...with the brute-force-validated miss distance.
        assert abs(near["miss_distance_km"] - 6.60) < 0.1

        # Every event is geometry self-consistent end to end (RTN ⇒ miss).
        for e in ev:
            norm = math.sqrt(e["r_km"]**2 + e["t_km"]**2 + e["n_km"]**2)
            assert abs(norm - e["miss_distance_km"]) < 1e-9

    def test_empty_catalog_returns_no_events(self):
        """No satellites → no pairs → clean empty result, not an error."""
        assert run_screen([], [], _iss_epoch_dt(), 6.0, 50.0) == []

    def test_decayed_window_is_isolated(self):
        """A window whose pair can't be propagated (decayed) comes back as None
        from the batched fine stage; run_screen drops exactly that one and the
        rest of the screen survives, with no exception escaping."""
        from unittest.mock import patch

        import core.conjunctions as conj

        a, b = _crosser_pair()
        meta = _crosser_meta()
        full = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)
        assert len(full) >= 2  # crossing repeats → multiple windows

        real_batch = conj.fine_filter_batch

        def one_decayed(*args, **kwargs):
            out = real_batch(*args, **kwargs)
            # Null the closest approach (guaranteed to clear the report cut), as
            # if that pair had decayed across its window — the real None path.
            best = min((k for k, v in enumerate(out) if v is not None),
                       key=lambda k: out[k]["miss_km"])
            out[best] = None
            return out

        with patch.object(conj, "fine_filter_batch", side_effect=one_decayed):
            out = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)

        assert len(out) == len(full) - 1  # exactly the one nulled window dropped


class TestRunScreenTimings:
    """7.1 profiling hook: run_screen(timings=dict) fills per-stage wall times
    and counts for scripts/profile_screening.py, and is a provable no-op on the
    screening result itself (the production endpoint passes timings=None)."""

    _KEYS = {"n_sats", "n_pairs", "n_windows", "n_events", "n_suppressed",
             "t_coarse", "t_medium", "t_fine"}

    def test_populates_every_key(self):
        a, b = _crosser_pair()
        t = {}
        run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0, timings=t)
        assert set(t) == self._KEYS
        assert t["n_sats"] == 2
        for k in ("t_coarse", "t_medium", "t_fine"):
            assert isinstance(t[k], float) and t[k] >= 0.0

    def test_timings_do_not_change_result(self):
        """The byte-identical guarantee — timings is a passive side channel, so
        the events with it on must equal the events with it off (default None)."""
        a, b = _crosser_pair()
        args = ([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0)
        off = run_screen(*args)
        on = run_screen(*args, timings={})
        assert off == on
        assert len(off) > 0  # the crosser really is found (not a vacuous equality)

    def test_counts_match_result(self):
        a, b = _crosser_pair()
        t = {}
        ev = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0, 50.0, timings=t)
        assert t["n_pairs"] == 1                       # the one co-altitude pair
        assert t["n_events"] == len(ev)
        assert t["n_windows"] >= t["n_events"] > 0     # a window may refine out

    def test_no_pairs_early_return_still_populates_zeros(self):
        """Disjoint altitude bands → coarse cut → early return before medium.
        The dict must still carry every key, with the un-run stages at 0."""
        a, b = _crosser_pair()
        meta = _crosser_meta()
        meta[1] = {**meta[1], "periapsis_km": 20000.0, "apoapsis_km": 20200.0}
        t = {}
        ev = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0, timings=t)
        assert ev == []
        assert set(t) == self._KEYS
        assert t["n_pairs"] == 0
        assert t["n_windows"] == 0 and t["n_events"] == 0
        assert t["t_medium"] == 0.0 and t["t_fine"] == 0.0


class TestProfileHarness:
    """scripts/profile_screening.py is the 7.1 runner. One smoke test so it
    can't silently rot if run_screen's signature changes (they are coupled)."""

    def test_profile_one_synth_returns_wellformed_row(self):
        import tempfile

        # scripts/ lives at the project root (one up from tests/).
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from scripts.profile_screening import _profile_one

        with tempfile.TemporaryDirectory() as tmp:
            t_load, tm = _profile_one("synth", 50, 2.0, 25.0, 60.0, tmp)
        assert t_load >= 0.0
        assert tm["n_sats"] == 50
        assert TestRunScreenTimings._KEYS <= set(tm)


class TestScreeningVolumesPath:
    """7.2 SFS path: run_screen(volumes=…) applies the per-pair RTN ellipsoid,
    suppresses co-located pairs, and de-dupes to unique pairs."""

    def test_requires_threshold_or_volumes(self):
        a, b = _crosser_pair()
        try:
            run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0)
            assert False, "expected ValueError (neither threshold nor volumes)"
        except ValueError:
            pass

    def test_volumes_length_mismatch_raises(self):
        a, b = _crosser_pair()
        try:
            run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0,
                       volumes=[_GENEROUS])
            assert False, "expected ValueError (volumes not index-aligned)"
        except ValueError:
            pass

    def test_ellipsoid_excludes_radial_dominated_miss(self):
        """The headline 7.2 behavior. The crosser's ~6.6 km miss is mostly
        *radial* (~3.6 km); the SFS LEO-1 ellipsoid (radial semi-axis 0.4 km)
        excludes it, though a 50 km Euclidean cut flags it. Radial separation is
        well-determined — a 3.6 km radial gap is not a collision risk."""
        a, b = _crosser_pair()
        meta = _crosser_meta()
        assert len(run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)) > 0
        sfs = run_screen([a, b], meta, _iss_epoch_dt(), 6.0,
                         volumes=[LEO_1, LEO_1])
        assert sfs == []

    def test_generous_volume_includes_dedupes_and_tags_regime(self):
        """A volume that contains the miss flags the pair; the many node-pass
        windows collapse to ONE event, tagged with the regime name."""
        a, b = _crosser_pair()
        meta = _crosser_meta()
        windows = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)
        assert len(windows) >= 2          # crossing repeats over 6 h
        sfs = run_screen([a, b], meta, _iss_epoch_dt(), 6.0,
                         volumes=[_GENEROUS, _GENEROUS])
        assert len(sfs) == 1              # de-duped to the unique pair
        assert sfs[0]["screening_regime"] == "TEST"
        assert sfs[0]["relative_speed_km_s"] > 1.0   # a real crossing, not co-located

    def test_dedupe_keeps_closest_per_unordered_pair(self):
        evs = [
            {"sat1_norad_id": 1, "sat2_norad_id": 2, "miss_distance_km": 9.0},
            {"sat1_norad_id": 2, "sat2_norad_id": 1, "miss_distance_km": 3.0},
            {"sat1_norad_id": 1, "sat2_norad_id": 3, "miss_distance_km": 5.0},
        ]
        out = _dedupe_to_unique_pairs(evs)
        assert len(out) == 2  # {1,2} and {1,3}
        pair12 = next(e for e in out
                      if {e["sat1_norad_id"], e["sat2_norad_id"]} == {1, 2})
        assert pair12["miss_distance_km"] == 3.0  # closest, order-independent

    def test_co_located_pair_suppressed(self):
        """Two identical objects (miss ~0, v_rel ~0) are co-located, not
        crossing — Conservative-drop suppresses them and reports the count."""
        meta = [_crosser_meta()[0],
                {**_crosser_meta()[0], "norad_id": 25545}]
        t = {}
        sfs = run_screen([_make_variant(), _make_variant()], meta,
                         _iss_epoch_dt(), 6.0,
                         volumes=[_GENEROUS, _GENEROUS], timings=t)
        assert sfs == []
        assert t["n_suppressed"] >= 1

    def test_suppress_false_keeps_co_located(self):
        meta = [_crosser_meta()[0],
                {**_crosser_meta()[0], "norad_id": 25545}]
        sfs = run_screen([_make_variant(), _make_variant()], meta,
                         _iss_epoch_dt(), 6.0,
                         volumes=[_GENEROUS, _GENEROUS], suppress=False)
        assert len(sfs) == 1   # kept (and de-duped)

    def test_same_launch_low_v_rel_suppressed(self):
        """Same-launch designator + low relative speed (a parked formation) is
        suppressed even when the miss is above the docked floor."""
        # A slow co-orbital neighbour: small mean-anomaly offset -> a few km
        # in-track, low v_rel, same launch id.
        a = _make_variant()
        b = _make_variant(dmo_deg=0.03)        # ~few km along-track, co-altitude
        same = {"norad_id": 25544, "name": "A", "object_type": "PAYLOAD",
                "epoch_age_days": 1.0, "periapsis_km": 410.0, "apoapsis_km": 420.0,
                "object_id": "1998-067A", "eccentricity": 0.0004, "period_min": 92.9}
        meta = [same, {**same, "norad_id": 25545, "object_id": "1998-067Z"}]
        t = {}
        out = run_screen([a, b], meta, _iss_epoch_dt(), 6.0,
                         volumes=[_GENEROUS, _GENEROUS], timings=t)
        # low v_rel + shared 1998-067 launch -> suppressed
        assert out == [] and t["n_suppressed"] >= 1

    def test_screener_sfs_default_builds_volumes_from_meta(self):
        """Through the ConjunctionScreener seam: screen() with no threshold
        builds each object's SFS volume from meta (regime_for) and runs the
        ellipsoid path. The radial-dominated crosser is excluded (LEO 1) while
        the legacy override still flags it — and SFS stats thread through."""
        a, b = _crosser_pair()
        meta = _crosser_meta()

        class _FakeProp:
            def get_all_satrecs(self):
                return [a, b], meta

        t = {}
        sfs = ConjunctionScreener(_FakeProp()).screen(
            _iss_epoch_dt(), 6.0, timings=t)
        assert sfs == []              # excluded by the 0.4 km radial semi-axis
        assert "n_suppressed" in t    # SFS stats surfaced via timings
        leg = ConjunctionScreener(_FakeProp()).screen(_iss_epoch_dt(), 6.0, 50.0)
        assert len(leg) > 0           # legacy Euclidean override still flags it

    def test_screener_sfs_suppresses_co_located(self):
        """End-to-end through the screener: two identical orbits (miss ~0,
        v_rel ~0) are co-located and suppressed under the SFS default."""
        meta = _crosser_meta()

        class _FakeProp:
            def get_all_satrecs(self):
                return [_make_variant(), _make_variant()], meta

        t = {}
        out = ConjunctionScreener(_FakeProp()).screen(
            _iss_epoch_dt(), 6.0, timings=t)
        assert out == []
        assert t["n_suppressed"] >= 1


class TestDenseShellScale:
    """6.9 success criteria at constellation scale: the full pipeline (load →
    coarse → medium → fine → RTN) runs error-free on a ~300-sat dense shell and
    a conjunction flows end-to-end. Uses a deterministic synthetic shell so it
    needs no network. The shell's natural plane-crossings provide the events
    (7.5 measured ~257 windows at this size/pad — no seeded crosser needed)."""

    def test_screen_on_synthetic_dense_shell(self):
        import tempfile
        import time
        from datetime import datetime, timezone
        from pathlib import Path

        from core.demo_seed import build_synthetic_shell
        from core.propagator import SatellitePropagator
        from core.tle_fetcher import GPFetcher

        df = build_synthetic_shell(n=300)
        with tempfile.TemporaryDirectory() as d:
            df.to_parquet(Path(d) / "synthshell.parquet", index=False)
            prop = SatellitePropagator(
                group="synthshell",
                fetcher=GPFetcher(cache_dir=Path(d)),
            )
            sats, meta = prop.get_all_satrecs()
            assert len(sats) == 300

            start = datetime(2026, 6, 1, tzinfo=timezone.utc)  # the shell epoch
            t0 = time.perf_counter()
            events = ConjunctionScreener(prop).screen(start, 6.0, 100.0)
            elapsed = time.perf_counter() - t0

            # 7.2: the SFS default (no threshold) also runs at scale through the
            # propagator seam — builds per-object volumes, suppresses, de-dupes.
            sfs_stats = {}
            sfs_events = ConjunctionScreener(prop).screen(
                start, 6.0, timings=sfs_stats)

        # Criterion 2: conjunctions flow through end-to-end — the shell's
        # natural plane-crossings. Gate the geometry check on a genuine crossing
        # (rel_speed > 1 km/s, the 7.5 lesson), then RTN norm must equal miss.
        assert events, "no conjunctions on the dense shell"
        crossings = [e for e in events if e["relative_speed_km_s"] > 1.0]
        assert crossings, "no genuine plane-crossing among the events"
        e = min(crossings, key=lambda ev: ev["miss_distance_km"])
        norm = (e["r_km"]**2 + e["t_km"]**2 + e["n_km"]**2) ** 0.5
        assert abs(norm - e["miss_distance_km"]) < 1e-9

        # Criterion 1: completes at scale without errors, in reasonable time.
        # Loose bound (real ~0.2–2 s) — a guard against pathological regressions,
        # not a precise benchmark.
        assert elapsed < 30.0

        # Criterion 3 (7.2): the SFS result is de-duped to unique pairs and every
        # event carries a regime label.
        assert isinstance(sfs_stats["n_suppressed"], int)
        seen = set()
        for e in sfs_events:
            key = frozenset((e["sat1_norad_id"], e["sat2_norad_id"]))
            assert key not in seen, "SFS result not de-duped to unique pairs"
            seen.add(key)
            assert e["screening_regime"], "SFS event missing regime label"


def _shell_satrecs(n):
    """Build a deterministic dense single shell (no network) and return its
    (satrecs, meta, start_utc). The satrecs are in-memory C++ objects and meta
    is a plain list, so both outlive the temp parquet the propagator loads from.
    """
    import tempfile
    from pathlib import Path

    from core.demo_seed import build_synthetic_shell
    from core.propagator import SatellitePropagator
    from core.tle_fetcher import GPFetcher

    df = build_synthetic_shell(n=n)
    with tempfile.TemporaryDirectory() as d:
        df.to_parquet(Path(d) / "synthshell.parquet", index=False)
        prop = SatellitePropagator(
            group="synthshell", fetcher=GPFetcher(cache_dir=Path(d)))
        sats, meta = prop.get_all_satrecs()
    return sats, meta, datetime(2026, 6, 1, tzinfo=timezone.utc)  # the shell epoch


class TestScaleRegression:
    """7.5 cross-task regression locks — the invariants 7.2/7.3 introduced,
    enforced at constellation scale (not just on the handful of crosser
    windows). These would fail loudly if a future 'optimization' silently
    changed screening results, so they protect the Phase-7 refactors durably.
    Deterministic + offline (synthetic dense shell)."""

    def test_batch_matches_oracle_at_shell_scale(self):
        """The keystone 7.3 lock: on a dense shell's *real* medium-filter
        windows, the batched Newton fine stage (fine_filter_batch) reproduces
        the per-window scipy oracle (fine_filter) to <10 m / <50 ms / <1 mm/s.
        TestFineFilterBatch only proves this on ~8 crosser windows; the 7.3
        headline ('byte-identical event counts on dense catalogs') is otherwise
        unenforced. We compare only genuine crossings (rel speed > 1 km/s): a
        co-moving same-plane pair has an ambiguous TCA that *both* methods place
        arbitrarily, so it isn't a meaningful equivalence case (and the SFS path
        suppresses it anyway)."""
        sats, meta, start = _shell_satrecs(300)
        jd_w, jd_f = utc_to_jd(start)   # same JD seam run_screen uses internally
        jd0 = jd_w + jd_f

        # A 100 km pad over 3 h yields thousands of real cross-plane windows on
        # the 300-sat shell (a tighter pad leaves a sparse shell with none — only
        # the seeded crosser would close). The pad sizes the *window set*; the
        # equivalence we lock is independent of miss magnitude.
        pad = 100.0
        step_sec = 30.0
        periapsis = [m["periapsis_km"] for m in meta]
        apoapsis = [m["apoapsis_km"] for m in meta]
        pairs = orbitcore.coarse_filter(periapsis, apoapsis, pad)
        rows = orbitcore.medium_filter(
            sats, pairs, jd0, jd0 + 3.0 / 24.0, step_sec, pad)
        assert len(rows) > 50, "shell didn't produce enough windows to be a scale test"

        # Sample to bound the oracle's per-window scipy cost; the batch still
        # refines the sampled set in one pass, so this exercises chunked,
        # many-window batching against the trusted reference.
        stride = max(1, len(rows) // 250)
        sampled = rows[::stride]
        batch = fine_filter_batch(sats, sampled, step_sec)
        assert len(batch) == len(sampled)

        step_day = step_sec / 86400.0
        compared = 0
        for (i, j, jd_flag, _d), got in zip(sampled, batch):
            if got is None or got["rel_speed_km_s"] < 1.0:
                continue   # decayed, or an ambiguous-TCA co-mover (see docstring)
            ref = fine_filter(sats[i], sats[j],
                              jd_flag - step_day, jd_flag + step_day)
            assert abs(got["miss_km"] - ref["miss_km"]) < 0.01          # 10 m
            assert abs(got["jd_tca"] - ref["jd_tca"]) * 86400.0 < 0.05  # 50 ms
            assert abs(got["rel_speed_km_s"] - ref["rel_speed_km_s"]) < 1e-3
            compared += 1
        assert compared > 30, "too few genuine crossings to lock the equivalence"

    def test_screen_is_deterministic_at_scale(self):
        """A full screen over a dense shell is reproducible run-to-run: the
        batched fine stage (NaN masking, einsum reductions, chunk boundaries)
        and the sort must introduce no nondeterminism. Locks event identity,
        order, and every float — the count-based regression signal is only
        meaningful if the screen is deterministic."""
        sats, meta, start = _shell_satrecs(300)
        first = run_screen(sats, meta, start, 3.0, 100.0)
        second = run_screen(sats, meta, start, 3.0, 100.0)
        assert len(first) > 5      # the shell really does produce events
        assert first == second     # identical dicts, order included

    def test_medium_gross_threshold_is_largest_semi_axis(self, monkeypatch):
        """The 7.2 no-skip wiring, locked at the integration boundary: in the
        SFS path the Euclidean threshold handed to the C++ medium filter must be
        the *largest semi-axis* present — not the tight radial axis (would skip
        in-track-loose events) and not the box corner sqrt(R²+T²+N²) (would
        over-screen). Unit tests prove circumscribing_radius() in isolation; this
        proves run_screen actually passes it down."""
        import core.conjunctions as conj

        a, b = _crosser_pair()
        asym = ScreeningVolume("ASYM", 0.4, 44.0, 51.0)   # LEO-1 shape
        real_medium = orbitcore.medium_filter
        seen = {}

        def spy(satrecs, pairs, jd_start, jd_end, step_sec, gross_km):
            seen["gross_km"] = gross_km
            return real_medium(
                satrecs, pairs, jd_start, jd_end, step_sec, gross_km)

        monkeypatch.setattr(conj.orbitcore, "medium_filter", spy)
        run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0,
                   volumes=[asym, asym])

        assert seen["gross_km"] == 51.0                       # largest semi-axis
        assert seen["gross_km"] != asym.r_km                  # not the radial axis
        corner = math.sqrt(0.4 ** 2 + 44 ** 2 + 51 ** 2)      # ~67.4 km
        assert seen["gross_km"] < corner                      # not the box corner


class TestFusedStage:
    """Phase 10.1a — the fused C++ stage (screen_pairs) and run_screen(fused=True).

    screen_pairs computes the coarse cut and the medium scan in one call so the
    survivor pairs never materialize as Python objects (the memory lever for the
    full active catalog: ~48 M pairs = ~8.7 GB of tuples that won't fit CI). The
    contract is BYTE-IDENTICAL results to coarse_filter() + medium_filter(), so
    these tests lock exactly that equivalence — at scale, not just a toy pair."""

    def test_exposed(self):
        assert hasattr(orbitcore, "screen_pairs")

    def test_rows_identical_to_coarse_plus_medium(self):
        """screen_pairs' rows == medium_filter(coarse_filter(...)) byte-for-byte,
        and its returned survivor count == len(coarse_filter(...)). This is the
        whole correctness claim of 1a: same screen, the pairs just never cross
        into Python. Run on a dense shell so thousands of real windows exercise
        the fused path (not a lone pair)."""
        sats, meta, start = _shell_satrecs(300)
        jd_w, jd_f = utc_to_jd(start)
        jd0 = jd_w + jd_f
        periapsis = [m["periapsis_km"] for m in meta]
        apoapsis = [m["apoapsis_km"] for m in meta]
        pad, step = 100.0, 30.0
        jd_end = jd0 + 3.0 / 24.0

        pairs = orbitcore.coarse_filter(periapsis, apoapsis, pad)
        rows_ref = orbitcore.medium_filter(sats, pairs, jd0, jd_end, step, pad)
        n_pairs, rows_fused = orbitcore.screen_pairs(
            sats, periapsis, apoapsis, pad, jd0, jd_end, step, pad)

        assert len(rows_ref) > 50, "shell too sparse to be a scale test"
        assert n_pairs == len(pairs)             # same survivor count
        assert rows_fused == rows_ref            # byte-identical rows

    def test_coarse_cut_identical_on_partial_overlap(self):
        """The inlined coarse cut must match coarse_filter() when the cut drops
        SOME pairs — not all-survive (the co-altitude shell above) nor all-drop
        (the empty case below), but the realistic partial cut where the two
        copies of the coarse logic could silently drift. screen_pairs takes the
        altitude bands directly, so feed a two-band spread (1 km apart) that
        leaves within-band pairs overlapping and cross-band pairs disjoint."""
        sats, meta, start = _shell_satrecs(120)
        jd_w, jd_f = utc_to_jd(start)
        jd0 = jd_w + jd_f
        n = len(sats)
        peri = [400.0 if k % 2 == 0 else 1400.0 for k in range(n)]
        apo = [450.0 if k % 2 == 0 else 1450.0 for k in range(n)]
        pad, step = 50.0, 30.0
        jd_end = jd0 + 2.0 / 24.0

        pairs = orbitcore.coarse_filter(peri, apo, pad)
        assert 0 < len(pairs) < n * (n - 1) // 2   # a genuine partial cut
        rows_ref = orbitcore.medium_filter(sats, pairs, jd0, jd_end, step, pad)
        n_pairs, rows_fused = orbitcore.screen_pairs(
            sats, peri, apo, pad, jd0, jd_end, step, pad)
        assert n_pairs == len(pairs)
        assert rows_fused == rows_ref

    def test_run_screen_events_identical_both_modes(self):
        """run_screen(fused=True) reproduces run_screen(fused=False) event-for-
        event (every dict, order included) — the fused flag is a pure perf
        switch, never a results switch.

        The Euclidean path is the non-vacuous proof: >5 real events on the dense
        shell, byte-identical through the full pipeline (fused rows → fine →
        report cut → sort). The SFS path is exercised too, but yields 0 events on
        the synthetic shell by construction (its crossings are radially dominated
        — 7.2), so its equivalence is structural: the SFS report cut / suppression
        / de-dupe are shared code operating on the SAME rows screen_pairs already
        reproduced byte-for-byte (test_rows_identical_to_coarse_plus_medium)."""
        sats, meta, start = _shell_satrecs(300)

        euc_ref = run_screen(sats, meta, start, 3.0, threshold_km=100.0, fused=False)
        euc_fused = run_screen(sats, meta, start, 3.0, threshold_km=100.0, fused=True)
        assert len(euc_ref) > 5
        assert euc_ref == euc_fused

        from core.screening_volumes import regime_for
        vols = [regime_for(m["periapsis_km"], m["eccentricity"], m["period_min"])
                for m in meta]
        sfs_ref = run_screen(sats, meta, start, 3.0, volumes=vols, fused=False)
        sfs_fused = run_screen(sats, meta, start, 3.0, volumes=vols, fused=True)
        assert sfs_ref == sfs_fused

    def test_timings_shape_when_fused(self):
        """Fused populates n_pairs (the survivor count comes back from C++) and
        folds t_coarse into t_medium (t_coarse == 0.0)."""
        sats, meta, start = _shell_satrecs(300)
        tm: dict = {}
        run_screen(sats, meta, start, 3.0, threshold_km=100.0,
                   fused=True, timings=tm)
        assert tm["n_pairs"] > 0
        assert tm["t_coarse"] == 0.0
        assert tm["n_windows"] >= tm["n_events"]

    def test_empty_survivors_early_return(self):
        """No coarse survivors (disjoint altitude bands) → screen_pairs returns
        (0, []) and run_screen(fused=True) early-returns [] with zeroed timings,
        mirroring the non-fused no-pairs path."""
        a, b = _crosser_pair()
        meta = [
            {"norad_id": 1, "name": "LOW", "object_type": "PAYLOAD",
             "epoch_age_days": 1.0, "periapsis_km": 300.0, "apoapsis_km": 320.0,
             "object_id": "2020-001A", "eccentricity": 0.001, "period_min": 90.0},
            {"norad_id": 2, "name": "HIGH", "object_type": "PAYLOAD",
             "epoch_age_days": 1.0, "periapsis_km": 1500.0, "apoapsis_km": 1520.0,
             "object_id": "2020-002A", "eccentricity": 0.001, "period_min": 116.0},
        ]
        tm: dict = {}
        events = run_screen([a, b], meta, _iss_epoch_dt(), 3.0,
                            threshold_km=5.0, pad_km=5.0, fused=True, timings=tm)
        assert events == []
        assert tm["n_pairs"] == 0
        assert tm["n_events"] == 0

    def test_validation(self):
        """screen_pairs fails loudly on bad inputs (same discipline as
        medium_filter): length mismatch, negative pad, bad window/step, and a
        non-Satrec item."""
        a, b = _crosser_pair()
        jd0 = _iss_epoch_jd()
        peri, apo = [410.0, 410.0], [420.0, 420.0]
        cases = [
            (([a, b], [410.0], apo, 5.0, jd0, jd0 + 1.0, 60.0, 5.0), ValueError),   # len mismatch
            (([a, b], peri, apo, -1.0, jd0, jd0 + 1.0, 60.0, 5.0), ValueError),     # pad < 0
            (([a, b], peri, apo, 5.0, jd0, jd0 - 1.0, 60.0, 5.0), ValueError),      # end <= start
            (([a, b], peri, apo, 5.0, jd0, jd0 + 1.0, 0.0, 5.0), ValueError),       # step <= 0
            (([a, b], peri, apo, 5.0, jd0, jd0 + 30 / 86400, 60.0, 5.0), ValueError),  # window < step
            (([a, None], peri, apo, 5.0, jd0, jd0 + 1.0, 60.0, 5.0), TypeError),    # None satrec
        ]
        for args, exc in cases:
            try:
                orbitcore.screen_pairs(*args)
                assert False, f"should have raised {exc.__name__}"
            except exc:
                pass


class TestTimeSieve:
    """Phase 10.2 — the C++ time sieve inside screen_pairs.

    The sieve precomputes per-pair scan intervals around the mutual-node
    crossings (the 10.1b construction, validated no-skip on 1.4M real events)
    and evaluates each pair only inside them. The contract is IDENTICAL EVENTS
    to the unsieved fused path — the sieve skips only the medium bound's
    spurious far-distance windows, which the fine stage discards anyway."""

    def test_sieve_requires_fused(self):
        a, b = _crosser_pair()
        try:
            run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 3.0,
                       threshold_km=5.0, fused=False, sieve=True)
            assert False, "should have raised ValueError"
        except ValueError:
            pass

    def test_cpp_anchors_match_python_oracle(self):
        """The C++ anchor construction (_sieve_anchors) reproduces the 10.1b
        Python oracle element-by-element on the synthetic shell — same 3
        propagations, same rv2coe, same equinoctial chord rates + curvature
        margins. This is the permanent lock that the C++ sieve geometry stays
        the validated geometry (measured agreement ~1e-11 on the real CI
        catalog; 1e-8 here leaves float-association headroom)."""
        import numpy as np
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "progress", "week10_planning"))
        from time_filter_gate import anchor_at

        sats, meta, start = _shell_satrecs(120)
        jd_w, jd_f = utc_to_jd(start)
        jd0 = jd_w + jd_f
        cpp = orbitcore._sieve_anchors(sats, jd0, jd0 + 1.0)
        ora = anchor_at(sats, jd0, jd0 + 1.0)

        assert (np.asarray(cpp["ok"], bool) == np.asarray(ora["ok"], bool)).all()
        ok = np.asarray(cpp["ok"], bool)
        for field, wrap in (("inc", False), ("raan0", True), ("raan_dot", False),
                            ("lam0", True), ("lam_rate", False),
                            ("m_rate", False), ("ecc", False), ("rp", False),
                            ("curv", False)):
            d = np.asarray(cpp[field])[ok] - np.asarray(ora[field])[ok]
            if wrap:
                d = np.mod(d + np.pi, 2 * np.pi) - np.pi
            assert np.abs(d).max() < 1e-8, f"{field}: {np.abs(d).max()}"

    def test_sieve_events_identical_at_shell_scale(self):
        """The whole point: run_screen(sieve=True) == run_screen(sieve=False)
        event-for-event on the dense shell (>1000 real Euclidean events — a
        non-vacuous lock). On a SINGLE shell every medium flag genuinely sits
        near a node crossing (flag-run width and window width both scale with
        1/sin I_R), so the row set itself comes out identical — the sieve's
        win there is scan work, not dropped rows (see the two-shell test for
        the spurious-window drop)."""
        sats, meta, start = _shell_satrecs(300)
        tm_off: dict = {}
        tm_on: dict = {}
        off = run_screen(sats, meta, start, 3.0, threshold_km=100.0,
                         fused=True, sieve=False, timings=tm_off)
        on = run_screen(sats, meta, start, 3.0, threshold_km=100.0,
                        fused=True, sieve=True, timings=tm_on)
        assert len(off) > 1000
        assert off == on                                   # identical events
        assert tm_on["n_windows"] <= tm_off["n_windows"]
        assert tm_on["n_pairs"] == tm_off["n_pairs"]       # coarse unchanged

    def test_sieve_skips_spurious_windows_across_shells(self):
        """Two shells 90 km apart: cross-shell pairs still coarse-survive
        (gap < pad) and their node crossings are real approaches (~90 km <
        D_eff), but the medium bound ALSO flags them far from the node line
        (a fast pair within threshold + v_rel*dt/2 — hundreds of km — of a
        sample). Those spurious windows lie outside the sieve's intervals and
        are skipped: fewer windows, identical events — the sieve provably
        bites, at the row level."""
        import tempfile
        from pathlib import Path

        import pandas as pd

        from core.demo_seed import build_synthetic_shell
        from core.propagator import SatellitePropagator
        from core.tle_fetcher import GPFetcher

        lo = build_synthetic_shell(n=150, mean_motion=15.05)
        hi = build_synthetic_shell(n=150, base_norad=8100000,
                                   mean_motion=15.35, inclination_deg=97.0)
        df = pd.concat([lo, hi], ignore_index=True)
        with tempfile.TemporaryDirectory() as d:
            df.to_parquet(Path(d) / "twoshell.parquet", index=False)
            prop = SatellitePropagator(
                group="twoshell", fetcher=GPFetcher(cache_dir=Path(d)))
            sats, meta = prop.get_all_satrecs()
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)

        tm_off: dict = {}
        tm_on: dict = {}
        off = run_screen(sats, meta, start, 3.0, threshold_km=100.0,
                         fused=True, sieve=False, timings=tm_off)
        on = run_screen(sats, meta, start, 3.0, threshold_km=100.0,
                        fused=True, sieve=True, timings=tm_on)
        assert off == on                                   # identical events
        assert tm_on["n_windows"] < tm_off["n_windows"]    # spurious skipped

        # SFS path wiring (0 events on the synthetic shell by construction —
        # 7.2; the equality is structural, the Euclidean lock above is the
        # non-vacuous one).
        from core.screening_volumes import regime_for
        vols = [regime_for(m["periapsis_km"], m["eccentricity"],
                           m["period_min"]) for m in meta]
        assert (run_screen(sats, meta, start, 3.0, volumes=vols,
                           fused=True, sieve=True)
                == run_screen(sats, meta, start, 3.0, volumes=vols,
                              fused=True, sieve=False))

    def test_crosser_event_survives_sieve(self):
        """The deterministic ISS/crosser conjunction (the Phase-6 anchor case)
        is found identically with the sieve on — a fast, targeted no-skip
        smoke on a genuine crossing geometry."""
        a, b = _crosser_pair()
        off = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0,
                         threshold_km=25.0, fused=True, sieve=False)
        on = run_screen([a, b], _crosser_meta(), _iss_epoch_dt(), 6.0,
                        threshold_km=25.0, fused=True, sieve=True)
        assert len(off) >= 1
        assert off == on
