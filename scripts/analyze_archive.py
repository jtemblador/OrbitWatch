#!/usr/bin/env python3
"""
Trend analysis over the snapshot archive — every snapshot the 9.5 robot job
published, read from the orphan `data` branch. No network, no re-screen: every
number is one the live site showed. Per snapshot: (a) Starlink count by 25 km
altitude band (mean-motion semi-major axis − R⊕, WGS-72 as in tle_fetcher);
(b) conjunctions by pair family (Starlink–Starlink / Starlink–other /
other–other); (c) conjunctions by the pair's mean-altitude band. Reads both
archive shapes (full `satellites[]` and columnar `slim-v1`). Snapshots under
--min-sats (the capped 5000-object runs of Jul 8–14) are skipped and reported.

Writes <out>/archive_trends.csv (tidy: generated_at, measure, group, value) +
three figures under <out>/img/, and prints the summary that
docs/findings/starlink-shell-lowering.md quotes. Run from the project root
(needs the branch locally: `git fetch origin data`):
    python scripts/analyze_archive.py
    python scripts/analyze_archive.py --ref origin/data --out docs/findings --no-figures
"""
import argparse
import gzip
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GM_EARTH = 398600.8   # km³/s² — WGS-72, same as tle_fetcher / SGP4
R_EARTH = 6378.135    # km equatorial
BAND_KM = 25
FAMILIES = ("Starlink–Starlink", "Starlink–other", "other–other")
WEEK = pd.Timedelta(days=7)


def list_snapshots(ref: str) -> list[str]:
    """Archive paths on `ref`, oldest first (ISO names sort chronologically)."""
    out = subprocess.run(["git", "-C", _ROOT, "ls-tree", "-r", "--name-only", ref],
                         capture_output=True, check=True, text=True).stdout.split()
    return sorted(p for p in out if p.startswith("snapshots/") and p.endswith(".json.gz"))


def load_snapshot(ref: str, path: str) -> dict:
    blob = subprocess.run(["git", "-C", _ROOT, "cat-file", "-p", f"{ref}:{path}"],
                          capture_output=True, check=True).stdout
    return json.loads(gzip.decompress(blob))


def normalize(snap: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Either archive shape → (objects[norad, name, mean_motion, inc],
    conjunctions with a_name/b_name — looked up by NORAD id when slim-v1 drops them)."""
    if (snap["meta"].get("archive") or {}).get("format") == "slim-v1":
        el = pd.DataFrame(snap["elements"])                        # columnar lists
    else:
        el = pd.DataFrame([s.get("omm", s) for s in snap["satellites"]])
    sats = pd.DataFrame({"norad": el["NORAD_CAT_ID"].astype(int),
                         "name": el["OBJECT_NAME"].astype(str),
                         "mean_motion": el["MEAN_MOTION"].astype(float),
                         "inc": el["INCLINATION"].astype(float)}).drop_duplicates("norad")
    conj = pd.DataFrame(snap["conjunctions"]) if snap["conjunctions"] else \
        pd.DataFrame(columns=["a", "b"])
    names = sats.set_index("norad")["name"]
    for side in "ab":
        col, looked_up = f"{side}_name", conj[side].map(names)
        conj[col] = conj[col].fillna(looked_up) if col in conj else looked_up
    return sats.reset_index(drop=True), conj


def band_counts(alt_km) -> pd.Series:
    lo = (np.floor(np.asarray(alt_km, dtype=float) / BAND_KM) * BAND_KM).astype(int)
    return pd.Series([f"{x}-{x + BAND_KM}" for x in lo], dtype=object).value_counts()


def measure(ts: str, sats: pd.DataFrame, conj: pd.DataFrame) -> list[tuple]:
    """Tidy (generated_at, measure, group, value) rows for one snapshot."""
    starlink = lambda s: s.fillna("").str.upper().str.startswith("STARLINK")  # noqa: E731
    n = sats["mean_motion"].to_numpy() * 2.0 * np.pi / 86400.0     # rev/day → rad/s
    sats["alt"] = np.cbrt(GM_EARTH / n**2) - R_EARTH                # Kepler's 3rd law
    sats["starlink"] = starlink(sats["name"])
    rows = [(ts, "catalog", "all", len(sats)),
            (ts, "catalog", "starlink", int(sats["starlink"].sum())),
            (ts, "conj_family", "total", len(conj))]
    rows += [(ts, "starlink_by_band", b, int(v))
             for b, v in band_counts(sats.loc[sats["starlink"], "alt"]).items()]
    k = (starlink(conj["a_name"]).astype(int) + starlink(conj["b_name"]).astype(int)).to_numpy()
    rows += [(ts, "conj_family", f, int((k == 2 - i).sum())) for i, f in enumerate(FAMILIES)]
    alt = sats.set_index("norad")["alt"]
    mean_alt = ((conj["a"].map(alt) + conj["b"].map(alt)) / 2.0).dropna()
    rows += [(ts, "conj_by_band", b, int(v)) for b, v in band_counts(mean_alt).items()]
    return rows


def track_objects(first: pd.DataFrame, last: pd.DataFrame) -> None:
    """Per-object view of the shift — lowering vs replacement (decay + new launches)."""
    f = first[first["starlink"]].set_index("norad")
    l = last[last["starlink"]].set_index("norad")                  # noqa: E741
    now = f.loc[f["alt"].between(525, 575, inclusive="left")].index.map(l["alt"]).to_series()
    print(f"  {len(now)} Starlinks at 525–575 km on the first snapshot, now: "
          f"{now.between(450, 500, inclusive='left').sum()} at 450–500, "
          f"{now.between(500, 525, inclusive='left').sum()} at 500–525, "
          f"{(now < 450).sum()} below 450, {now.between(525, 575, inclusive='left').sum()} "
          f"still at 525–575, {now.isna().sum()} gone from the catalog")
    band = l[l["alt"].between(450, 475, inclusive="left")]
    then = band.index.map(f["alt"]).to_series()
    print(f"  {len(band)} Starlinks at 450–475 km on the last snapshot were: "
          f"{then.between(450, 475, inclusive='left').sum()} already there, "
          f"{(then >= 475).sum()} higher (came down), {(then < 450).sum()} lower (raising), "
          f"{then.isna().sum()} not yet in the catalog")
    print(f"  450–475 km inclination mix, first → last: "
          f"{f.loc[f['alt'].between(450, 475, inclusive='left'), 'inc'].round().value_counts().to_dict()}"
          f" → {band['inc'].round().value_counts().to_dict()}")


def summarize(df: pd.DataFrame, n_paths: int, skipped: list) -> pd.DataFrame:
    p = df.pivot_table(index="t", columns=["measure", "group"], values="value", aggfunc="sum")
    p[("starlink_by_band", "525-575")] = p["starlink_by_band"][["525-550", "550-575"]].sum(axis=1)
    print(f"archive: {n_paths} snapshots; analysed {len(p)} ({p.index[0]:%Y-%m-%d %H:%M} → "
          f"{p.index[-1]:%Y-%m-%d %H:%M} UTC); skipped {len(skipped)} under --min-sats"
          + (f" ({skipped[0]} … {skipped[-1]})" if skipped else ""))
    slot = (p.index - pd.Timedelta(hours=5, minutes=17)).floor("8h")  # 05:17/13:17/21:17 cron
    want = len(pd.date_range(slot.min(), slot.max(), freq="8h"))
    print(f"cron coverage: {slot.nunique()}/{want} 8 h slots have a snapshot "
          f"({100 * (1 - slot.nunique() / want):.1f}% empty)")
    a, z = p[p.index < p.index[0] + WEEK], p[p.index > p.index[-1] - WEEK]
    print(f"first 7 days ({len(a)} runs) → last 7 days ({len(z)} runs), medians:")
    for m, g in [("catalog", "all"), ("catalog", "starlink"), *[("starlink_by_band", b) for b in
                 ("450-475", "475-500", "525-575")], ("conj_family", "total"),
                 *[("conj_family", f) for f in FAMILIES], ("conj_by_band", "450-475"),
                 ("conj_by_band", "475-500")]:
        lo, hi = a[(m, g)].median(), z[(m, g)].median()
        print(f"  {m:17s} {g:18s} {lo:8,.0f} → {hi:8,.0f}  ×{hi / lo:.2f}")
    share = lambda d: d[("conj_family", FAMILIES[0])].median() / d[("conj_family", "total")].median()  # noqa: E731
    fam = p["conj_family"].fillna(0)
    any_sl = (fam["total"] - fam["other–other"]) / fam["total"]
    print(f"  Starlink–Starlink share {share(a):.0%} → {share(z):.0%}; "
          f"≥1 Starlink in {any_sl.min():.0%}–{any_sl.max():.0%} of each run's events")
    n, c = np.log(p[("starlink_by_band", "450-475")]), np.log(p[("conj_by_band", "450-475")])
    print(f"crowding: conj(450–475) ∝ Starlinks(450–475)^{np.polyfit(n, c, 1)[0]:.2f} "
          f"(log-log fit, {len(n)} runs, r = {np.corrcoef(n, c)[0, 1]:.2f})")
    return p


# ── figures (dataviz reference palette, light surface) ────────────────────────
INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")          # categorical slots 1–3, validator-checked
_K = lambda v, _=None: f"{v / 1000:g}K" if v >= 1000 else f"{v:g}"  # noqa: E731


def _style(ax):
    ax.set_facecolor(SURF)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelcolor=INK2, length=0)
    ax.yaxis.set_major_formatter(_K)


def _header(fig, title, subtitle, legend=()):
    from matplotlib.lines import Line2D
    fig.text(0.035, 0.955, title, fontsize=19, weight="semibold", color=INK, va="top")
    fig.text(0.035, 0.895, subtitle, fontsize=13.5, color=INK2, va="top")
    if legend:
        fig.legend([Line2D([], [], color=c, lw=3) for _, c in legend], [t for t, _ in legend],
                   loc="upper left", bbox_to_anchor=(0.025, 0.86), ncol=len(legend), fontsize=13,
                   frameon=False, labelcolor=INK2, handlelength=1.4, columnspacing=1.4)


def _line(ax, s, text, color, lw=2.2):
    ax.plot(s.index, s, color=color, lw=lw, solid_capstyle="round")
    ax.plot(s.index[-1], s.iloc[-1], "o", ms=8, color=color, mec=SURF, mew=2, zorder=5)
    ax.annotate(f"{text}  {s.iloc[-1]:,.0f}", (s.index[-1], s.iloc[-1]), xytext=(10, 0),
                textcoords="offset points", va="center", fontsize=13.5, color=INK)


def render(p: pd.DataFrame, img_dir: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 14,
                         "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"]})
    os.makedirs(img_dir, exist_ok=True)
    fmt = mdates.DateFormatter("%b %-d")
    early, late = p.index < p.index[0] + WEEK, p.index > p.index[-1] - WEEK
    grew = lambda s: s[late].median() / s[early].median()  # noqa: E731
    save = lambda fig, name: fig.savefig(os.path.join(img_dir, name), facecolor=SURF)  # noqa: E731

    # 1 — Starlink population by altitude band: the two bands that moved, one for context
    bands = [("450-475", SERIES[0]), ("525-575", SERIES[1]), ("475-500", MUTED)]
    fig, ax = plt.subplots(figsize=(8, 5), dpi=200, facecolor=SURF)
    fig.subplots_adjust(left=0.08, right=0.76, top=0.76, bottom=0.08)
    _header(fig, "Starlinks are leaving the ~550 km shells",
            f"Starlink satellites by mean altitude, every snapshot, "
            f"{p.index[0]:%b %-d} – {p.index[-1]:%b %-d, %Y}",
            [(f"{b.replace('-', '–')} km", c) for b, c in bands])
    for b, col in bands:
        _line(ax, p[("starlink_by_band", b)].fillna(0), f"{b.replace('-', '–')} km", col)
    _style(ax)
    ax.set_ylim(0, 7000)
    ax.xaxis.set_major_formatter(fmt)
    save(fig, "starlink-by-altitude.png")

    # 2 — conjunctions by pair family; catalog size on its own axis beneath (no dual axis)
    fams = list(zip(FAMILIES, SERIES))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(8, 6.4), dpi=200, facecolor=SURF, sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1], "hspace": 0.38})
    fig.subplots_adjust(left=0.08, right=0.74, top=0.78, bottom=0.06)
    _header(fig, f"Starlink–Starlink conjunctions rose {grew(p[('conj_family', FAMILIES[0])]):.1f}×",
            "Conjunctions per 24 h screen (thin: each run; bold: 7-day median)", fams)
    for f, col in fams:
        s = p[("conj_family", f)]
        ax.plot(s.index, s, color=col, lw=0.9, alpha=0.35)
        _line(ax, s.rolling("7D", center=True).median(), f, col)
    _style(ax)
    ax.set_ylim(0, 16500)
    ax.xaxis.set_major_formatter(fmt)                              # shared with ax2
    cat = p[("catalog", "all")]
    ax2.plot(cat.index, cat, color=INK2, lw=2.2)
    _style(ax2)
    ax2.set_ylim(0, 20000)
    ax2.set_yticks([0, 10000, 20000])
    ax2.set_title(f"Objects in the catalog, first vs last week: {cat[early].median():,.0f} to "
                  f"{cat[late].median():,.0f} (+{100 * (grew(cat) - 1):.1f}%)",
                  loc="left", fontsize=13.5, color=INK2, pad=8)
    save(fig, "conjunctions-by-family.png")

    # 3 — crowding in the band that filled: conjunctions vs population, darker = later
    n, c = p[("starlink_by_band", "450-475")], p[("conj_by_band", "450-475")]
    k = float((c * n**2).sum() / (n**4).sum())                     # least squares c = k·n²
    fig, ax = plt.subplots(figsize=(8, 5.4), dpi=200, facecolor=SURF)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.82, bottom=0.14)
    _header(fig, f"{grew(n):.1f}× the satellites, {grew(c):.1f}× the conjunctions",
            "450–475 km: one dot per snapshot (lighter = July, darker = October)")
    _style(ax)
    xs = np.linspace(0, n.max() * 1.04, 200)
    ax.plot(xs, k * xs**2, color=INK2, lw=1.6, zorder=2)
    ax.annotate("best fit of k·n²", (xs[95], k * xs[95] ** 2), xytext=(-6, 10),
                textcoords="offset points", ha="right", fontsize=13.5, color=INK2)
    ramp = LinearSegmentedColormap.from_list("seq", ["#86b6ef", "#2a78d6", "#104281"])
    ax.scatter(n, c, s=46, c=(n.index - n.index[0]) / (n.index[-1] - n.index[0]), cmap=ramp,
               edgecolor=SURF, linewidth=1.2, zorder=3)
    ax.set_xlim(0, n.max() * 1.06)
    ax.set_ylim(0, c.max() * 1.1)
    ax.xaxis.set_major_formatter(_K)
    ax.set_xlabel("Starlink satellites at 450–475 km", color=INK2, fontsize=13.5)
    ax.set_ylabel("Conjunctions at 450–475 km", color=INK2, fontsize=13.5)
    save(fig, "band-crowding.png")
    plt.close("all")


def main() -> None:
    ap = argparse.ArgumentParser(description="Trend analysis over the snapshot archive.")
    ap.add_argument("--ref", default="origin/data", help="git ref holding snapshots/")
    ap.add_argument("--out", default=os.path.join(_ROOT, "docs", "findings"),
                    help="output dir (archive_trends.csv here, figures under img/)")
    ap.add_argument("--min-sats", type=int, default=15000,
                    help="skip snapshots with fewer objects (the capped Jul 8–14 runs)")
    ap.add_argument("--no-figures", action="store_true", help="CSV + summary only")
    args = ap.parse_args()

    paths = list_snapshots(args.ref)
    rows, skipped, ends = [], [], {}
    for i, path in enumerate(paths, 1):
        print(f"\rreading {i}/{len(paths)}", end="", file=sys.stderr)
        snap = load_snapshot(args.ref, path)
        sats, conj = normalize(snap)
        ts = snap["meta"]["generated_at"]
        if len(sats) < args.min_sats:
            skipped.append(ts[:10])
            continue
        rows += measure(ts, sats, conj)
        ends.setdefault("first", sats)
        ends["last"] = sats
    print(file=sys.stderr)

    df = pd.DataFrame(rows, columns=["generated_at", "measure", "group", "value"])
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "archive_trends.csv")
    df.to_csv(csv_path, index=False)
    df["t"] = pd.to_datetime(df["generated_at"], utc=True, format="ISO8601")
    p = summarize(df, len(paths), skipped)
    track_objects(ends["first"], ends["last"])
    if not args.no_figures:
        render(p, os.path.join(args.out, "img"))
    print(f"wrote {csv_path} ({len(df):,} rows)"
          + ("" if args.no_figures else " + 3 figures under img/"))


if __name__ == "__main__":
    main()
