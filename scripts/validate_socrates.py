#!/usr/bin/env python3
"""
Generate the SOCRATES validation report (Phase 8.3). Writes Markdown + figures
to validation/, over three slices:

    ISS           single primary  — the grounding case (eyeballed in 8.2)
    top-N closest headline         — the tightest real conjunctions in the catalog
    Starlink      breadth          — same engine over a dense constellation subset

LIVE artifact generator (the committed report + this runner make it reproducible):

  Stage A (default) — current-GP baseline. Fetches the newest elset per
    SOCRATES-flagged object and screens the same window. Reproduction degrades
    with element age (DSE) because the current feed has rolled past the epoch
    SOCRATES used. Baseline source is `--current-source` (CelesTrak `gp.php` by
    default; `spacetrack` uses Space-Track's `gp` class — handy when CelesTrak
    TLS is blocked).

  Stage B (`--epoch-matched`) — the lever. Also pulls each object's *epoch-matched*
    historical elset from Space-Track `gp_history` (epoch ≈ TCA − DSE) and screens
    again; the report leads each slice with the current-vs-epoch-matched lift.
    Needs SPACETRACK_USER / SPACETRACK_PASS (a gitignored .env, auto-loaded).

Each distinct object is one GP fetch, so keep slices small (the --max-objects cap
guards against a constellation-sized slice firing thousands of requests).

Run from the project root:
    python scripts/validate_socrates.py                         # Stage A, all slices
    python scripts/validate_socrates.py --slices iss            # one slice, fast
    python scripts/validate_socrates.py --epoch-matched         # Stage A + B
    python scripts/validate_socrates.py --epoch-matched --current-source spacetrack
"""
import argparse
import os
import sys
from datetime import datetime, timezone

# Resolve imports whether run as `python scripts/validate_socrates.py` or from
# elsewhere: project root for `backend.*`, backend/ for the orbitcore .so.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from datetime import timedelta  # noqa: E402

import pandas as pd  # noqa: E402

from backend.core.socrates_compare import (  # noqa: E402
    build_epoch_targets,
    compare_against_socrates,
)
from backend.core.socrates_fetcher import SOCRATESFetcher            # noqa: E402
from backend.core.socrates_plots import render_all                  # noqa: E402
from backend.core.socrates_report import format_report              # noqa: E402
from backend.core.spacetrack_fetcher import (  # noqa: E402
    BulkGPAdapter,
    SpaceTrackError,
    SpaceTrackFetcher,
)
from backend.core.tle_fetcher import GPFetcher                      # noqa: E402


def _load_dotenv(path: str) -> None:
    """Minimal KEY=VALUE .env loader so SPACETRACK_* in a gitignored .env are
    picked up with no extra dependency. Never overrides an already-set env var."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]                        # quoted → literal value
            else:
                val = val.split(" #", 1)[0].rstrip()   # unquoted → drop inline comment
            os.environ.setdefault(key.strip(), val)


def _build_slices(fetcher: SOCRATESFetcher, args) -> list[dict]:
    """Resolve the requested slices to {name, slug, df}. Fetch is Parquet-cached."""
    fetcher.fetch()  # warm the cache once; the slicers read from it
    wanted = {s.strip() for s in args.slices.split(",") if s.strip()}
    specs = []
    if "iss" in wanted:
        specs.append({
            "name": "ISS (single primary)", "slug": "iss",
            "df": fetcher.by_catnr(args.iss_catnr)})
    if "top" in wanted:
        specs.append({
            "name": f"Top {args.top_n} closest", "slug": f"top{args.top_n}_closest",
            "df": fetcher.top_n(args.top_n)})
    if "starlink" in wanted:
        star = fetcher.by_name("STARLINK")
        # Bound to the closest rows so distinct objects stay under the per-object
        # fetch cap — the full constellation is thousands of objects.
        star = star.nsmallest(args.starlink_rows, "range_km")
        specs.append({
            "name": f"Starlink ({len(star)} closest)", "slug": f"starlink_top{args.starlink_rows}",
            "df": star})
    return specs


def _distinct_ids(df) -> list[int]:
    return [int(x) for x in pd.unique(df[["norad_id_1", "norad_id_2"]].values.ravel())]


def _current_fetcher(df, args, st_fetcher):
    """The current-GP baseline source: CelesTrak per-object (default) or a
    Space-Track bulk-latest adapter (resilient when CelesTrak's TLS is blocked)."""
    if args.current_source == "spacetrack":
        return BulkGPAdapter(st_fetcher.fetch_latest(_distinct_ids(df)))
    return GPFetcher()


def _epoch_matched_fetcher(df, args, st_fetcher):
    """A gp_history adapter that serves each object its nearest-epoch elset
    (epoch SOCRATES used = TCA − DSE), bulk-fetched in one Space-Track query."""
    targets = build_epoch_targets(df)
    lo = min(targets.values()) - timedelta(days=args.epoch_pad_days)
    hi = max(targets.values()) + timedelta(days=args.epoch_pad_days)
    return BulkGPAdapter(st_fetcher.fetch_history(list(targets), lo, hi), targets)


def _run_slice(spec: dict, args, st_fetcher) -> dict:
    """Compare one slice and attach figures; never raises — records errors instead."""
    name, slug, df = spec["name"], spec["slug"], spec["df"]
    if df is None or df.empty:
        return {"name": name, "error": "no SOCRATES rows in this slice"}
    ids = _distinct_ids(df)
    if len(ids) > args.max_objects:  # guard before any (Space-Track) fetch
        return {"name": name,
                "error": f"{len(ids)} distinct objects > max_objects={args.max_objects}"}

    figures_dir = os.path.join(args.out, "figures")
    try:
        cur = compare_against_socrates(
            df, _current_fetcher(df, args, st_fetcher),
            step_sec=args.step_sec, max_objects=args.max_objects)
    except Exception as e:  # oversized slice, no GP fetched, etc. — keep going
        return {"name": name, "error": f"current-GP: {type(e).__name__}: {e}"}
    s = cur["summary"]
    print(f"  {name} [current]: {s['n_matched']}/{s['n_socrates']} "
          f"({100 * s['reproduction_rate']:.0f}%), epoch_ok={s.get('n_epoch_ok', 0)}, "
          f"missing={s.get('n_missing_objects', 0)}")

    if not args.epoch_matched:
        figures = render_all(s, cur["results"], figures_dir, slug)
        return {"name": name, "summary": s, "results": cur["results"], "figures": figures}

    # Stage B: the gp_history epoch-matched run — the reproduction lever.
    try:
        m = compare_against_socrates(
            df, _epoch_matched_fetcher(df, args, st_fetcher),
            step_sec=args.step_sec, max_objects=args.max_objects)
    except Exception as e:  # keep the current-GP result; note the matched failure
        print(f"  {name} [epoch-matched] FAILED: {type(e).__name__}: {e}")
        figures = render_all(s, cur["results"], figures_dir, slug)
        return {"name": name, "summary": s, "results": cur["results"],
                "figures": figures, "matched_error": str(e)}
    ms = m["summary"]
    print(f"  {name} [epoch-matched]: {ms['n_matched']}/{ms['n_socrates']} "
          f"({100 * ms['reproduction_rate']:.0f}%), epoch_ok={ms.get('n_epoch_ok', 0)}")
    figs_m = render_all(ms, m["results"], figures_dir, slug + "_matched", compare_to=s)
    current_label = ("Space-Track current GP (latest)"
                     if args.current_source == "spacetrack" else "CelesTrak current GP")
    return {"name": name, "summary": s, "results": cur["results"],
            "current_label": current_label,
            "summary_matched": ms, "results_matched": m["results"],
            "figures_matched": figs_m}


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the SOCRATES validation report.")
    ap.add_argument("--out", default=os.path.join(_ROOT, "validation"),
                    help="output directory for the report + figures")
    ap.add_argument("--slices", default="iss,top,starlink",
                    help="comma list of slices to run: iss,top,starlink")
    ap.add_argument("--top-n", type=int, default=25, help="N for the top-N-closest slice")
    ap.add_argument("--starlink-rows", type=int, default=40,
                    help="closest Starlink-involving conjunctions to include")
    ap.add_argument("--iss-catnr", type=int, default=25544, help="primary for the ISS slice")
    ap.add_argument("--step-sec", type=float, default=30.0, help="screening grid step")
    ap.add_argument("--max-objects", type=int, default=200,
                    help="per-slice distinct-object fetch cap (safety)")
    ap.add_argument("--epoch-matched", action="store_true",
                    help="Stage B: also run the Space-Track gp_history epoch-matched "
                         "comparison (needs SPACETRACK_USER / SPACETRACK_PASS)")
    ap.add_argument("--current-source", choices=["celestrak", "spacetrack"],
                    default="celestrak",
                    help="current-GP baseline source (spacetrack avoids CelesTrak TLS)")
    ap.add_argument("--epoch-pad-days", type=float, default=2.0,
                    help="± pad (days) around target epochs for the gp_history window")
    args = ap.parse_args()

    _load_dotenv(os.path.join(_ROOT, ".env"))  # pick up SPACETRACK_* if present
    os.makedirs(args.out, exist_ok=True)
    print(f"SOCRATES validation → {args.out}")

    # Authenticate to Space-Track up front (fail fast on bad creds) only if a
    # stage actually needs it.
    st_fetcher = None
    if args.epoch_matched or args.current_source == "spacetrack":
        st_fetcher = SpaceTrackFetcher()
        try:
            st_fetcher.login()
            print("  Space-Track: authenticated")
        except SpaceTrackError as e:
            ap.error(str(e))

    fetcher = SOCRATESFetcher()
    specs = _build_slices(fetcher, args)
    if not specs:
        ap.error(f"no valid slices in --slices={args.slices!r}")

    sections = [_run_slice(spec, args, st_fetcher) for spec in specs]

    mode_note = ("Stage B — current GP vs. gp_history epoch-matched"
                 if args.epoch_matched else "current-GP run (Stage A)")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = format_report(sections, generated_at, mode_note)
    report_path = os.path.join(args.out, "socrates_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nWrote {report_path}")


if __name__ == "__main__":
    main()
