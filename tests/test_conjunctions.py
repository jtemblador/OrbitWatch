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
from datetime import timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import orbitcore
from core.conjunctions import fine_filter
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
