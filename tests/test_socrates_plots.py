#!/usr/bin/env python3
"""Tests for backend/core/socrates_plots.py — the report figures (Task 8.3/8.5).
Headless Agg backend, so figures render to PNG files under tmp_path with no
display. We assert each plotter writes a non-empty PNG when it has data and
skips (returns False, writes nothing) when it doesn't.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from core.socrates_plots import (  # noqa: E402
    plot_miss_delta_hist,
    plot_miss_scatter,
    plot_reproduction_by_dse,
    plot_reproduction_compare,
    plot_tca_delta_hist,
    render_all,
)


def _matched(n=5):
    return [{"matched": True, "tca_delta_s": 0.1 * i, "miss_delta_km": 0.01 * i,
             "ours_miss_km": 1.0 + i, "socrates_miss_km": 1.0 + i} for i in range(n)]


def _summary(rates=(0.0, 1.0, 0.25), counts=(0, 1, 8)):
    labels = ("<1d", "1-3d", ">3d")
    return {"by_dse": {l: {"n_socrates": c, "n_matched": int(round(c * r)),
                           "reproduction_rate": r}
                       for l, c, r in zip(labels, counts, rates)}}


def _nonempty(path):
    return os.path.exists(path) and os.path.getsize(path) > 0


class TestSinglePlotters:
    def test_reproduction_by_dse(self, tmp_path):
        p = str(tmp_path / "dse.png")
        assert plot_reproduction_by_dse(_summary(), p) is True and _nonempty(p)

    def test_reproduction_by_dse_empty_skips(self, tmp_path):
        p = str(tmp_path / "dse.png")
        assert plot_reproduction_by_dse(_summary(counts=(0, 0, 0)), p) is False
        assert not os.path.exists(p)

    def test_tca_hist(self, tmp_path):
        p = str(tmp_path / "tca.png")
        assert plot_tca_delta_hist(_matched(), p) is True and _nonempty(p)

    def test_tca_hist_no_matched_skips(self, tmp_path):
        p = str(tmp_path / "tca.png")
        assert plot_tca_delta_hist([{"matched": False}], p) is False

    def test_miss_hist(self, tmp_path):
        p = str(tmp_path / "miss.png")
        assert plot_miss_delta_hist(_matched(), p) is True and _nonempty(p)

    def test_scatter(self, tmp_path):
        p = str(tmp_path / "sc.png")
        assert plot_miss_scatter(_matched(), p) is True and _nonempty(p)

    def test_scatter_empty_skips(self, tmp_path):
        p = str(tmp_path / "sc.png")
        assert plot_miss_scatter([], p) is False


class TestCompare:
    def test_compare_renders(self, tmp_path):
        p = str(tmp_path / "cmp.png")
        cur = _summary(rates=(0.0, 1.0, 0.25))
        mat = _summary(rates=(0.0, 1.0, 1.0))
        assert plot_reproduction_compare(cur, mat, p) is True and _nonempty(p)

    def test_compare_empty_skips(self, tmp_path):
        p = str(tmp_path / "cmp.png")
        empty = {"by_dse": {}}
        assert plot_reproduction_compare(empty, empty, p) is False


class TestRenderAll:
    # render_all writes PNGs into figures_dir and returns paths prefixed with
    # rel_prefix ("figures/..."), relative to figures_dir's parent — mirroring
    # the runner where figures_dir = <out>/figures and the report sits in <out>.
    def test_returns_relative_paths(self, tmp_path):
        figs = render_all(_summary(), _matched(), str(tmp_path / "figures"), "iss")
        assert figs and all(f.startswith("figures/iss_") for f in figs)
        for f in figs:
            assert _nonempty(os.path.join(str(tmp_path), f))

    def test_compare_to_switches_lead_figure(self, tmp_path):
        # With compare_to, the lead 'dse' figure is the current-vs-matched chart;
        # both runs still produce a figures/<slug>_dse.png path.
        figs = render_all(_summary(rates=(0, 1.0, 1.0)), _matched(), str(tmp_path / "figures"),
                          "iss_matched", compare_to=_summary(rates=(0, 1.0, 0.25)))
        assert any(f.endswith("iss_matched_dse.png") for f in figs)

    def test_sparse_data_drops_empty_figures(self, tmp_path):
        # no matched events → tca/miss/scatter skip, only the dse bar remains
        figs = render_all(_summary(), [], str(tmp_path / "figures"), "x")
        assert figs == ["figures/x_dse.png"]
