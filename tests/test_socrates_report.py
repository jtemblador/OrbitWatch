#!/usr/bin/env python3
"""Tests for backend/core/socrates_report.py — the pure Markdown formatters for
the SOCRATES validation report (Task 8.3/8.5). Dict-in / string-out, no I/O, so
fully offline/deterministic.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.socrates_report import (  # noqa: E402
    _f,
    _pair_label,
    _rate,
    _stat,
    comparison_to_markdown,
    events_to_markdown,
    format_report,
    summary_to_markdown,
)


def _summary(n=9, matched=3, epoch_ok=0, by_dse=None):
    return {
        "n_socrates": n, "n_matched": matched,
        "reproduction_rate": matched / n if n else 0.0,
        "n_extra": 1, "n_missing_objects": 0, "n_epoch_ok": epoch_ok,
        "tca_delta_s": {"n": matched, "median_abs": 0.62, "p95_abs": 4.8, "max_abs": 5.27},
        "miss_delta_km": {"n": matched, "median_abs": 0.19, "p95_abs": 0.31, "max_abs": 0.32},
        "by_dse": by_dse or {
            "<1d": {"n_socrates": 0, "n_matched": 0, "reproduction_rate": 0.0,
                    "tca_delta_s": {"n": 0}, "miss_delta_km": {"n": 0}},
            "1-3d": {"n_socrates": 1, "n_matched": 1, "reproduction_rate": 1.0,
                     "tca_delta_s": {"n": 1, "median_abs": 0.47}, "miss_delta_km": {"n": 1, "median_abs": 0.3}},
            ">3d": {"n_socrates": 8, "n_matched": 2, "reproduction_rate": 0.25,
                    "tca_delta_s": {"n": 2, "median_abs": 2.9}, "miss_delta_km": {"n": 2, "median_abs": 0.16}},
        },
    }


def _event(matched=True, miss=1.0, dtca=0.5, dmiss=0.05, dse=2.5, epoch_ok=True):
    return {
        "norad_id_1": 25544, "norad_id_2": 12345, "name_1": "ISS (ZARYA)", "name_2": "DEB",
        "socrates_tca": pd.Timestamp("2026-06-28 12:00", tz="UTC"), "socrates_miss_km": miss,
        "dse_max": dse, "matched": matched,
        "ours_miss_km": (miss + dmiss) if matched else None,
        "tca_delta_s": dtca if matched else None,
        "miss_delta_km": dmiss if matched else None, "epoch_ok": epoch_ok,
    }


class TestFormatHelpers:
    def test_f_none_and_negative_zero(self):
        assert _f(None) == "—"
        assert _f(-0.0003) == "0.000"     # the review fix: no ugly -0.000
        assert _f(-0.6) == "-0.600"       # real negatives preserved
        assert _f(1.2345) == "1.234" or _f(1.2345) == "1.235"  # rounding either way

    def test_f_precision_arg(self):
        assert _f(3.14159, 1) == "3.1"

    def test_rate(self):
        assert _rate(0.333) == "33%"
        assert _rate(1.0) == "100%"
        assert _rate(None) == "—"

    def test_stat_empty_and_value(self):
        assert _stat({"n": 0}, "median_abs") == "—"
        assert _stat(None, "median_abs") == "—"
        assert _stat({"n": 3, "median_abs": 0.5}, "median_abs") == "0.500"

    def test_pair_label_names_then_ids(self):
        assert _pair_label({"name_1": "A", "name_2": "B"}) == "A × B"
        assert _pair_label({"norad_id_1": 1, "norad_id_2": 2}) == "1 × 2"


class TestSummaryMarkdown:
    def test_headline_and_buckets(self):
        md = summary_to_markdown(_summary(), "ISS")
        assert "### ISS" in md
        assert "3 / 9 reproduced" in md and "33%" in md
        # all three DSE buckets present, with the degradation visible
        assert "<1d" in md and "1-3d" in md and ">3d" in md
        assert "25%" in md          # >3d degraded rate
        assert "0.620" in md        # median TCA delta


class TestEventsMarkdown:
    def test_empty(self):
        assert "No SOCRATES conjunctions" in events_to_markdown([])

    def test_matched_row_has_deltas_and_epoch_tick(self):
        md = events_to_markdown([_event(epoch_ok=True)])
        assert "ISS (ZARYA) × DEB" in md and "✓" in md
        assert "0.5" in md          # tca delta

    def test_missed_row_shows_missed(self):
        md = events_to_markdown([_event(matched=False, epoch_ok=False)])
        assert "missed" in md and "✗" in md

    def test_sorted_closest_first(self):
        rows = [_event(miss=3.0), _event(miss=0.5), _event(miss=1.5)]
        md = events_to_markdown(rows)
        i_small = md.index("0.500")
        i_big = md.index("3.000")
        assert i_small < i_big

    def test_limit_truncates_with_note(self):
        rows = [_event(miss=float(i)) for i in range(1, 30)]
        md = events_to_markdown(rows, limit=5)
        assert "more not shown" in md


class TestComparison:
    def test_two_rows_with_labels(self):
        cur = _summary(matched=3, epoch_ok=0)
        mat = _summary(matched=8, epoch_ok=9)
        md = comparison_to_markdown(cur, mat, label_a="Src A")
        assert "Src A" in md and "gp_history" in md
        assert "3/9" in md and "8/9" in md


class TestFormatReport:
    def test_error_section_rendered(self):
        out = format_report([{"name": "Bad", "error": "RuntimeError: x"}], "T")
        assert "did not run" in out and "RuntimeError: x" in out

    def test_stage_a_section(self):
        sec = {"name": "ISS", "summary": _summary(), "results": [_event()], "figures": ["figures/a.png"]}
        out = format_report([sec], "T")
        assert "**Closest conjunctions:**" in out
        assert "![ISS](figures/a.png)" in out

    def test_stage_b_section_has_comparison_and_label(self):
        sec = {"name": "ISS", "summary": _summary(matched=3),
               "results": [_event()], "current_label": "Space-Track current GP (latest)",
               "summary_matched": _summary(matched=8, epoch_ok=9),
               "results_matched": [_event()], "figures_matched": ["figures/m.png"]}
        out = format_report([sec], "T")
        assert "gp_history` lever" in out
        assert "Space-Track current GP (latest)" in out
        assert "Closest conjunctions (epoch-matched)" in out

    def test_matched_error_note_rendered(self):
        sec = {"name": "ISS", "summary": _summary(), "results": [_event()],
               "figures": [], "matched_error": "SpaceTrackError: nope"}
        out = format_report([sec], "T")
        assert "epoch-matched run failed" in out and "nope" in out

    def test_caveats_present(self):
        out = format_report([], "T")
        assert "Same method" in out and "not collision avoidance" in out
