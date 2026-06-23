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
from core.conjunctions import ConjunctionScreener, fine_filter, run_screen
from core.coordinate_transforms import teme_to_rtn, utc_to_jd

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


# --- ConjunctionScreener / run_screen (Task 6.7) --------------------------

def _crosser_meta():
    """Index-aligned metadata for _crosser_pair(): two co-altitude ISS-band
    objects (so coarse_filter always pairs them)."""
    return [
        {"norad_id": 25544, "name": "ISS (ZARYA)", "object_type": "PAYLOAD",
         "epoch_age_days": 1.0, "periapsis_km": 410.0, "apoapsis_km": 420.0},
        {"norad_id": 90000, "name": "ISS CROSSER", "object_type": "DEBRIS",
         "epoch_age_days": 1.0, "periapsis_km": 410.0, "apoapsis_km": 420.0},
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

    def test_fine_filter_failure_is_isolated(self):
        """One window that fails fine_filter (e.g. a sat un-propagatable there)
        is dropped; the rest of the screen survives and no exception escapes."""
        from unittest.mock import patch

        import core.conjunctions as conj

        a, b = _crosser_pair()
        meta = _crosser_meta()
        full = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)
        assert len(full) >= 2  # crossing repeats → multiple windows

        real_fine = conj.fine_filter
        state = {"n": 0}

        def flaky(*args, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("simulated propagation failure")
            return real_fine(*args, **kwargs)

        with patch.object(conj, "fine_filter", side_effect=flaky):
            out = run_screen([a, b], meta, _iss_epoch_dt(), 6.0, 50.0)

        assert len(out) == len(full) - 1  # exactly the one failed window dropped


class TestRunScreenTimings:
    """7.1 profiling hook: run_screen(timings=dict) fills per-stage wall times
    and counts for scripts/profile_screening.py, and is a provable no-op on the
    screening result itself (the production endpoint passes timings=None)."""

    _KEYS = {"n_sats", "n_pairs", "n_windows", "n_events",
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


class TestSeededScreenIntegration:
    """Demo seed → screener: the synthetic crosser must produce a flagged
    conjunction against the live stations catalog. Invariant-based (the crosser
    clones row 0 and crosses it), so it survives catalog churn."""

    def test_seeded_catalog_yields_crosser_event(self):
        from datetime import datetime, timezone
        from core.demo_seed import DEMO_CROSSER_ID
        from core.propagator import SatellitePropagator

        prop = SatellitePropagator(seed_demo=True)
        events = ConjunctionScreener(prop).screen(
            datetime.now(timezone.utc), 6.0, 100.0)
        crosser = [e for e in events
                   if DEMO_CROSSER_ID in (e["sat1_norad_id"], e["sat2_norad_id"])]
        assert crosser, "seeded crosser produced no conjunction"
        # A real crossing: small but non-zero miss, high relative speed.
        best = min(crosser, key=lambda e: e["miss_distance_km"])
        assert best["miss_distance_km"] < 100.0
        assert best["relative_speed_km_s"] > 5.0


class TestDenseShellScale:
    """6.9 success criteria at constellation scale: the full pipeline (load →
    coarse → medium → fine → RTN) runs error-free on a ~300-sat dense shell and
    a conjunction flows end-to-end. Uses a deterministic synthetic shell so it
    needs no network; the live demo swaps in a real Starlink shell."""

    def test_screen_on_synthetic_dense_shell(self):
        import tempfile
        import time
        from datetime import datetime, timezone
        from pathlib import Path

        from core.demo_seed import DEMO_CROSSER_ID, build_synthetic_shell
        from core.propagator import SatellitePropagator
        from core.tle_fetcher import GPFetcher

        df = build_synthetic_shell(n=300)
        with tempfile.TemporaryDirectory() as d:
            df.to_parquet(Path(d) / "synthshell.parquet", index=False)
            prop = SatellitePropagator(
                group="synthshell",
                fetcher=GPFetcher(cache_dir=Path(d)),
                seed_demo=True,
            )
            sats, meta = prop.get_all_satrecs()
            assert len(sats) == 301  # 300 shell + crosser

            start = datetime(2026, 6, 1, tzinfo=timezone.utc)  # the shell epoch
            t0 = time.perf_counter()
            events = ConjunctionScreener(prop).screen(start, 6.0, 100.0)
            elapsed = time.perf_counter() - t0

        # Criterion 2: a conjunction flows through (the crosser at minimum).
        crosser = [e for e in events
                   if DEMO_CROSSER_ID in (e["sat1_norad_id"], e["sat2_norad_id"])]
        assert crosser, "no crosser conjunction on the dense shell"
        e = crosser[0]
        norm = (e["r_km"]**2 + e["t_km"]**2 + e["n_km"]**2) ** 0.5
        assert abs(norm - e["miss_distance_km"]) < 1e-9

        # Criterion 1: completes at scale without errors, in reasonable time.
        # Loose bound (real ~0.2–2 s) — a guard against pathological regressions,
        # not a precise benchmark.
        assert elapsed < 30.0
