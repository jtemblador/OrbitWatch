"""
SOCRATES validation report — Markdown rendering (Phase 8.3, Stage A + B).

PURE formatting only: dict-in, string-out. Takes the `summary`/`results` produced
by `socrates_compare` and renders the committed `validation/socrates_report.md` —
the per-slice reproduction/Δ tables (Stage A) and, when an epoch-matched run is
present, the current-vs-`gp_history` lift table (Stage B). No network, no
matplotlib, no I/O — so the formatters are unit-testable offline (8.5) and the
runner (`scripts/validate_socrates.py`) is the only live glue.

The report answers one question per slice: of the conjunctions SOCRATES flags,
how many does our SGP4 screener reproduce, how closely (TCA / miss-distance), and
how does that agreement degrade with element age (DSE)?
"""

from __future__ import annotations

# Stated once at the top of the report so a reader interprets the numbers
# correctly — these are the honest limits of a same-method geometric check.
CAVEATS = [
    "**Same method, on purpose.** SOCRATES (CelesTrak/STK) and OrbitWatch both "
    "propagate public SGP4 elements, so close agreement validates *our* "
    "implementation of a standard pipeline — it is not an independent-physics check.",
    "**Geometric screening, not collision avoidance.** We report TCA, miss "
    "distance and relative speed. No probability of collision (Pc) — that needs "
    "covariance data we do not have.",
    "**Epoch drift is the dominant error.** A current-GP feed (CelesTrak `gp.php` "
    "or Space-Track `gp`) serves only the *newest* element set, which has often "
    "rolled past the epoch SOCRATES screened from. `epoch_ok` flags each event "
    "where our element age at TCA still "
    "matches SOCRATES's reported `DSE`; where it does not, expect TCA/miss to drift. "
    "Reproduction therefore degrades with `DSE` (see the by-age tables).",
    "**5 km legacy-Euclidean screen** (matching SOCRATES's spherical 5 km gate), "
    "WGS-72 gravity + AFSPC mode — sub-km parity differences vs STK are expected.",
]


def _f(x, nd: int = 3) -> str:
    """Format a float, or an em-dash for None. Normalizes -0.0 → 0.0 so a tiny
    negative delta doesn't render as an ugly '-0.000'."""
    if x is None:
        return "—"
    v = round(float(x), nd) + 0.0
    return f"{v:.{nd}f}"


def _rate(x) -> str:
    return "—" if x is None else f"{100.0 * x:.0f}%"


def _stat(stats: dict | None, key: str, nd: int = 3) -> str:
    """Pull one field from a `_stats` dict ({n, median_abs, p95_abs, max_abs})."""
    if not stats or stats.get("n", 0) == 0:
        return "—"
    return _f(stats.get(key), nd)


def _pair_label(rec: dict) -> str:
    a = str(rec.get("name_1", rec.get("norad_id_1", "?"))).strip()
    b = str(rec.get("name_2", rec.get("norad_id_2", "?"))).strip()
    return f"{a} × {b}"


def summary_to_markdown(summary: dict, slice_name: str) -> str:
    """Headline block + the by-DSE degradation table for one slice."""
    n = summary.get("n_socrates", 0)
    matched = summary.get("n_matched", 0)
    lines = [
        f"### {slice_name}",
        "",
        f"- **{matched} / {n} reproduced** ({_rate(summary.get('reproduction_rate'))})",
        f"- epoch-matched events: {summary.get('n_epoch_ok', '—')} "
        f"· other crossings found (not in SOCRATES): {summary.get('n_extra', 0)} "
        f"· objects unavailable: {summary.get('n_missing_objects', 0)}",
        "",
        "| metric | median \\|Δ\\| | p95 \\|Δ\\| | max \\|Δ\\| |",
        "|---|---|---|---|",
        f"| TCA (s) | {_stat(summary.get('tca_delta_s'), 'median_abs')} "
        f"| {_stat(summary.get('tca_delta_s'), 'p95_abs')} "
        f"| {_stat(summary.get('tca_delta_s'), 'max_abs')} |",
        f"| miss distance (km) | {_stat(summary.get('miss_delta_km'), 'median_abs')} "
        f"| {_stat(summary.get('miss_delta_km'), 'p95_abs')} "
        f"| {_stat(summary.get('miss_delta_km'), 'max_abs')} |",
        "",
        "**Agreement by element age (DSE):**",
        "",
        "| DSE | conjunctions | reproduced | rate | median \\|ΔTCA\\| (s) "
        "| median \\|Δmiss\\| (km) |",
        "|---|---|---|---|---|---|",
    ]
    by_dse = summary.get("by_dse", {})
    for label in ("<1d", "1-3d", ">3d"):
        b = by_dse.get(label, {})
        lines.append(
            f"| {label} | {b.get('n_socrates', 0)} | {b.get('n_matched', 0)} "
            f"| {_rate(b.get('reproduction_rate'))} "
            f"| {_stat(b.get('tca_delta_s'), 'median_abs')} "
            f"| {_stat(b.get('miss_delta_km'), 'median_abs')} |"
        )
    return "\n".join(lines)


def events_to_markdown(results: list[dict], limit: int = 15) -> str:
    """Sample table of the closest conjunctions in the slice (tightest first)."""
    if not results:
        return "_No SOCRATES conjunctions in this slice._"
    rows = sorted(results, key=lambda r: r.get("socrates_miss_km", float("inf")))[:limit]
    lines = [
        "| pair | SOCRATES TCA (UTC) | SOC miss (km) | ours miss (km) "
        "| ΔTCA (s) | Δmiss (km) | DSE | epoch |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        tca = r.get("socrates_tca")
        _sf = getattr(tca, "strftime", None)
        tca_s = _sf("%Y-%m-%d %H:%M") if _sf else str(tca)
        if not r.get("matched"):
            ours, dtca, dmiss = "missed", "—", "—"
        else:
            ours = _f(r.get("ours_miss_km"))
            dtca = _f(r.get("tca_delta_s"), 1)
            dmiss = _f(r.get("miss_delta_km"))
        _eo = r.get("epoch_ok")
        epoch = "✓" if _eo is True else "✗" if _eo is False else "—"
        lines.append(
            f"| {_pair_label(r)} | {tca_s} | {_f(r.get('socrates_miss_km'))} "
            f"| {ours} | {dtca} | {dmiss} | {_f(r.get('dse_max'), 1)} | {epoch} |"
        )
    if len(results) > limit:
        lines.append(f"\n_…{len(results) - limit} more not shown._")
    return "\n".join(lines)


def comparison_to_markdown(
    current: dict,
    matched: dict,
    label_a: str = "CelesTrak current GP",
    label_b: str = "Space-Track gp_history (epoch-matched)",
) -> str:
    """Side-by-side: how the epoch-matched run lifts reproduction (the Stage-B lever)."""
    def _row(lbl: str, s: dict) -> str:
        n, m = s.get("n_socrates", 0), s.get("n_matched", 0)
        return (f"| {lbl} | {m}/{n} ({_rate(s.get('reproduction_rate'))}) "
                f"| {s.get('n_epoch_ok', '—')} "
                f"| {_stat(s.get('tca_delta_s'), 'median_abs')} "
                f"| {_stat(s.get('miss_delta_km'), 'median_abs')} |")
    return "\n".join([
        "**Current GP vs. epoch-matched — the `gp_history` lever:**",
        "",
        "| element source | reproduced | epoch-matched | median \\|ΔTCA\\| (s) "
        "| median \\|Δmiss\\| (km) |",
        "|---|---|---|---|---|",
        _row(label_a, current),
        _row(label_b, matched),
    ])


def format_report(
    sections: list[dict],
    generated_at: str,
    mode_note: str = "current-GP run (Stage A)",
) -> str:
    """Assemble the full report from per-slice sections.

    Each section: {"name", "summary", "results", "figures"(rel paths), "error"}.
    A section with "error" set is rendered as a failure note (kept in the report
    so a partial run is honest about what did not run).
    """
    out = [
        "# OrbitWatch — SOCRATES Validation Report",
        "",
        f"_Generated {generated_at} · {mode_note}_",
        "",
        "Does OrbitWatch's SGP4 conjunction screener reproduce the close approaches "
        "CelesTrak [SOCRATES](https://celestrak.org/SOCRATES/) publishes? This report "
        "runs our pipeline on the SOCRATES-flagged objects over the same window and "
        "measures the agreement.",
        "",
        "## How to read this",
        "",
    ]
    out += [f"- {c}" for c in CAVEATS]
    out += ["", "## Results", ""]

    for sec in sections:
        if sec.get("error"):
            out += [f"### {sec['name']}", "", f"> ⚠ slice did not run: {sec['error']}", ""]
            continue
        out += [summary_to_markdown(sec["summary"], sec["name"]), ""]

        if sec.get("matched_error"):
            out += [f"> ⚠ epoch-matched run failed: {sec['matched_error']} "
                    "— showing current-GP only.", ""]

        matched = sec.get("summary_matched")
        if matched:
            # Epoch-matched run present (Stage B): lead with the lift, then show
            # the epoch-matched figures/events (the same-element result).
            label_a = sec.get("current_label", "CelesTrak current GP")
            out += [comparison_to_markdown(sec["summary"], matched, label_a=label_a), ""]
            figures = sec.get("figures_matched", [])
            results = sec.get("results_matched", sec["results"])
            events_heading = "**Closest conjunctions (epoch-matched):**"
        else:
            figures = sec.get("figures", [])
            results = sec["results"]
            events_heading = "**Closest conjunctions:**"

        for fig in figures:
            out.append(f"![{sec['name']}]({fig})")
        if figures:
            out.append("")
        out += [events_heading, "", events_to_markdown(results), ""]

    out += [
        "## Limitations",
        "",
        "How accurate is this, and why is it *screening* rather than collision "
        "avoidance? See **[sgp4_uncertainty.md](sgp4_uncertainty.md)** — the error "
        "budget, our SGP4 cross-validation, and the measured epoch-drift effect.",
        "",
    ]
    return "\n".join(out) + "\n"
