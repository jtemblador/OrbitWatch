"""
SOCRATES validation report — figures (Phase 8.3, Stage A).

Renders the four plots that carry the validation story to PNG via matplotlib's
headless Agg backend (no display required). Each plotter returns True if it wrote
a figure and False if there was nothing to plot (e.g. zero matched events), so a
sparse slice silently drops the empty figure rather than emitting a blank axes.

`render_all` is the entry point the runner calls; it returns the Markdown-relative
paths of the figures it actually wrote.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # headless — must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402

_DSE_LABELS = ("<1d", "1-3d", ">3d")


def plot_reproduction_by_dse(summary: dict, path: str) -> bool:
    """Bar chart: reproduction rate per element-age bucket (the headline finding)."""
    by_dse = summary.get("by_dse", {})
    counts = [by_dse.get(l, {}).get("n_socrates", 0) for l in _DSE_LABELS]
    if sum(counts) == 0:
        return False
    rates = [100.0 * by_dse.get(l, {}).get("reproduction_rate", 0.0) for l in _DSE_LABELS]
    matched = [by_dse.get(l, {}).get("n_matched", 0) for l in _DSE_LABELS]

    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.bar(_DSE_LABELS, rates, color="#3b7dd8")
    for bar, m, n in zip(bars, matched, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5,
                f"{m}/{n}", ha="center", va="bottom", fontsize=9)
    ax.set_ylim(0, 105)
    ax.set_ylabel("reproduction rate (%)")
    ax.set_xlabel("element age at TCA (DSE)")
    ax.set_title("Reproduction vs. element age")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def plot_reproduction_compare(current: dict, matched: dict, path: str) -> bool:
    """Grouped bars: current-GP vs epoch-matched reproduction per DSE bucket — the
    headline Phase-8 visual (current degrades with age; the gp_history lever
    flattens it back to ~full reproduction)."""
    cb, mb = current.get("by_dse", {}), matched.get("by_dse", {})
    counts = [cb.get(l, {}).get("n_socrates", 0) + mb.get(l, {}).get("n_socrates", 0)
              for l in _DSE_LABELS]
    if sum(counts) == 0:
        return False
    cur = [100.0 * cb.get(l, {}).get("reproduction_rate", 0.0) for l in _DSE_LABELS]
    mat = [100.0 * mb.get(l, {}).get("reproduction_rate", 0.0) for l in _DSE_LABELS]
    x = list(range(len(_DSE_LABELS)))
    w = 0.38

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar([i - w / 2 for i in x], cur, w, label="current GP", color="#d8893b")
    ax.bar([i + w / 2 for i in x], mat, w, label="epoch-matched", color="#3b7dd8")
    ax.set_xticks(x)
    ax.set_xticklabels(_DSE_LABELS)
    ax.set_ylim(0, 105)
    ax.set_ylabel("reproduction rate (%)")
    ax.set_xlabel("element age at TCA (DSE)")
    ax.set_title("Reproduction by element age: current vs. epoch-matched")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def plot_tca_delta_hist(results: list[dict], path: str) -> bool:
    """Histogram of |ΔTCA| over matched events — how tightly TCA agrees."""
    vals = [abs(r["tca_delta_s"]) for r in results
            if r.get("matched") and r.get("tca_delta_s") is not None]
    if not vals:
        return False
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(vals, bins=min(20, len(vals)), color="#2a9d5c", edgecolor="white")
    ax.set_xlabel("|TCA difference| (s)")
    ax.set_ylabel("matched conjunctions")
    ax.set_title("TCA agreement (matched events)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def plot_miss_delta_hist(results: list[dict], path: str) -> bool:
    """Histogram of signed Δmiss (ours − SOCRATES) — agreement and any bias."""
    vals = [r["miss_delta_km"] for r in results
            if r.get("matched") and r.get("miss_delta_km") is not None]
    if not vals:
        return False
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(vals, bins=min(20, len(vals)), color="#d8893b", edgecolor="white")
    ax.axvline(0.0, color="#444", linestyle="--", linewidth=1)
    ax.set_xlabel("miss distance difference, ours − SOCRATES (km)")
    ax.set_ylabel("matched conjunctions")
    ax.set_title("Miss-distance agreement (matched events)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def plot_miss_scatter(results: list[dict], path: str) -> bool:
    """Scatter ours vs. SOCRATES miss distance with the y=x agreement line."""
    pts = [(r["socrates_miss_km"], r["ours_miss_km"]) for r in results
           if r.get("matched") and r.get("ours_miss_km") is not None]
    if not pts:
        return False
    xs, ys = zip(*pts)
    hi = max(max(xs), max(ys)) * 1.05
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, hi], [0, hi], color="#999", linestyle="--", linewidth=1, label="y = x")
    ax.scatter(xs, ys, color="#3b7dd8", s=28, alpha=0.8)
    ax.set_xlim(0, hi)
    ax.set_ylim(0, hi)
    ax.set_xlabel("SOCRATES miss distance (km)")
    ax.set_ylabel("OrbitWatch miss distance (km)")
    ax.set_title("Miss distance: ours vs. SOCRATES")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def render_all(
    summary: dict,
    results: list[dict],
    figures_dir: str,
    slug: str,
    rel_prefix: str = "figures",
    compare_to: dict | None = None,
) -> list[str]:
    """Write all figures for one slice; return their report-relative paths.

    Args:
        figures_dir: absolute dir to write PNGs into (created if missing).
        slug: filename-safe slice identifier (e.g. "top25_closest").
        rel_prefix: path prefix the Markdown uses to reach `figures_dir`.
        compare_to: a baseline (current-GP) summary. When given, the lead figure
            becomes the current-vs-epoch-matched reproduction-by-DSE comparison
            (Stage B) instead of `summary`'s standalone by-DSE bar (Stage A).
    """
    os.makedirs(figures_dir, exist_ok=True)
    if compare_to is not None:
        dse_plot = lambda p: plot_reproduction_compare(compare_to, summary, p)  # noqa: E731
    else:
        dse_plot = lambda p: plot_reproduction_by_dse(summary, p)  # noqa: E731
    plotters = (
        ("dse", dse_plot),
        ("tca", lambda p: plot_tca_delta_hist(results, p)),
        ("miss", lambda p: plot_miss_delta_hist(results, p)),
        ("scatter", lambda p: plot_miss_scatter(results, p)),
    )
    written: list[str] = []
    for tag, fn in plotters:
        name = f"{slug}_{tag}.png"
        if fn(os.path.join(figures_dir, name)):
            written.append(f"{rel_prefix}/{name}")
    return written
