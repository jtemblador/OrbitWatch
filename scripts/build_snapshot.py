#!/usr/bin/env python3
"""
Build the static-site snapshot (Phase 9.2) — the single data file the deployed
site reads so it makes NO API call per visit.

Fetches a satellite group (default: active payloads from CelesTrak), runs the
real C++ conjunction screen offline, and writes one compact `snapshot.json`:
per-object OMM elements (for in-browser satellite.js propagation) + the
pre-computed conjunction list + freshness/screen metadata.

The heavy work (fetch + screen) is intended to run in CI a few×/day (Phase 9.5);
this is the same script that job invokes. Reuses `tle_fetcher`, the
`build_satrecs_and_meta` → `run_screen` path, and `screening_volumes` unchanged.

Run from the project root:
    python scripts/build_snapshot.py                              # active, 72 h, SFS
    python scripts/build_snapshot.py --group starlink_shell       # cached dense shell (offline)
    python scripts/build_snapshot.py --hours 48 --mode euclidean --threshold 5
"""
import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

# Resolve imports whether run as `python scripts/build_snapshot.py` or elsewhere:
# project root for `backend.*`, backend/ for the orbitcore .so.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.core.conjunctions import run_screen                 # noqa: E402
from backend.core.propagator import build_satrecs_and_meta       # noqa: E402
from backend.core.screening_volumes import is_screenable, regime_for  # noqa: E402
from backend.core.snapshot import build_snapshot                 # noqa: E402
from backend.core.tle_fetcher import GPFetcher                   # noqa: E402

_GZ_BUDGET_BYTES = 5_000_000  # ~5 MB gzipped target (see roadmap 9.2)


# Phase 10.6: production runs the full accelerated engine — fused C++ coarse+
# medium (10.1a), the time sieve (10.2), and the C++ fine stage (10.4). All
# three are proven EVENT-byte-identical to the classic cascade (10.3 on live
# catalogs; 10.5 mutation-checked offline) and gated again on every CI rebuild
# by scripts/ab_screen.py, so the reported conjunctions are unchanged — this is
# a speed/memory lever only (it's what makes screening the full ~16k catalog fit
# the CI runner). Flip off ONLY to reproduce the pre-10 baseline for debugging.
_ACCEL = dict(fused=True, sieve=True, refine=True)


def _screen(satrecs, meta, start, args, timings):
    """SFS ellipsoid path (default) or legacy Euclidean, mirroring the app."""
    if args.mode == "sfs":
        volumes = [regime_for(m["periapsis_km"], m["eccentricity"], m["period_min"])
                   for m in meta]
        events = run_screen(satrecs, meta, start, args.hours, volumes=volumes,
                            step_sec=args.step_sec, timings=timings, **_ACCEL)
        return events, {"window_hours": args.hours, "mode": "SFS", "step_sec": args.step_sec}
    events = run_screen(satrecs, meta, start, args.hours, threshold_km=args.threshold,
                        step_sec=args.step_sec, timings=timings, **_ACCEL)
    return events, {"window_hours": args.hours, "mode": "Euclidean",
                    "threshold_km": args.threshold, "step_sec": args.step_sec}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the static-site snapshot.json.")
    ap.add_argument("--group", default="active",
                    help="CelesTrak group (or a cached group like 'starlink_shell')")
    ap.add_argument("--hours", type=float, default=72.0, help="screening window (hours)")
    ap.add_argument("--mode", choices=["sfs", "euclidean"], default="sfs",
                    help="SFS ellipsoid (default) or legacy Euclidean")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="Euclidean report threshold km (--mode euclidean only)")
    ap.add_argument("--step-sec", type=float, default=30.0, help="screening grid step")
    ap.add_argument("--max-sats", type=int, default=0,
                    help="cap the catalog (0 = all) — for a fast local run")
    ap.add_argument("--min-objects", type=int, default=0,
                    help="abort (exit 1) if the fetched catalog has fewer than "
                         "this many objects, BEFORE --max-sats — so a truncated "
                         "or rate-limited fetch never publishes a near-empty "
                         "snapshot (0 = off; CI passes a floor for --group active)")
    ap.add_argument("--out", default=os.path.join(_ROOT, "frontend", "snapshot.json"),
                    help="output path for snapshot.json")
    args = ap.parse_args()

    fetcher = GPFetcher()
    df = fetcher.fetch(args.group)
    # Sanity floor: a valid-but-truncated fetch (rate-limited / partial upstream
    # response) is non-empty JSON, so the empty-check alone won't catch it. Refuse
    # to publish a snapshot of a fraction of the real catalog.
    if args.min_objects and len(df) < args.min_objects:
        print(f"ERROR: fetched {len(df)} objects for group '{args.group}', "
              f"below the --min-objects floor of {args.min_objects} — refusing to "
              f"build a snapshot from a truncated catalog.", file=sys.stderr)
        sys.exit(1)
    if args.max_sats and len(df) > args.max_sats:
        df = df.head(args.max_sats).reset_index(drop=True)

    # DISPLAY all objects; SCREEN only where the SFS handbook has a genuine volume
    # (LEO + the GEO band). MEO/HEO get no handbook volume, so we don't screen them
    # with a wrong-size LEO fallback bubble — we show them, but don't flag their
    # conjunctions. Euclidean mode is regime-agnostic, so it screens everything.
    if args.mode == "sfs":
        keep = [is_screenable(p, e, pr) for p, e, pr
                in zip(df["periapsis"], df["eccentricity"], df["period"])]
        screen_df = df[keep].reset_index(drop=True)
    else:
        screen_df = df
    # Screened-subset floor: the raw --min-objects floor above can't catch an
    # is_screenable/regime regression that rejects almost everything — the fetch
    # is fine, but the SCREENED set collapses to ~0, so we'd publish a snapshot
    # that displays ~16k sats yet stopped finding conjunctions. Active is ~99%
    # screenable (nearly all of it is LEO + the GEO band); refuse if under 25%,
    # a floor with enormous margin that only trips on a real regression.
    if args.min_objects and args.mode == "sfs" and len(screen_df) < 0.25 * len(df):
        print(f"ERROR: only {len(screen_df)} of {len(df)} objects are screenable "
              f"(< 25%) — is_screenable/regime likely regressed; refusing to "
              f"publish a snapshot that screens almost nothing.", file=sys.stderr)
        sys.exit(1)
    n_skipped = len(df) - len(screen_df)
    print(f"catalog: {len(df)} objects (group={args.group}) — "
          f"screening {len(screen_df)}"
          + (f", displaying {n_skipped} MEO/HEO without screening" if n_skipped else ""))

    t0 = time.perf_counter()
    satrecs, meta = build_satrecs_and_meta(screen_df)
    start = datetime.now(timezone.utc)
    timings: dict = {}
    events, screen_params = _screen(satrecs, meta, start, args, timings)
    screen_params["n_screened"] = len(screen_df)
    screen_params["regime_scope"] = (
        "SFS Table 3 (LEO + GEO band); MEO/HEO displayed, not screened"
        if args.mode == "sfs" else "all regimes (Euclidean)")
    elapsed = time.perf_counter() - t0
    print(f"screen: {len(events)} conjunctions in {elapsed:.1f}s "
          f"({args.mode}, {args.hours:.0f} h window)")

    generated_at = start.isoformat().replace("+00:00", "Z")
    snap = build_snapshot(df, events, screen_params, generated_at,
                          source=f"CelesTrak {args.group}")

    # allow_nan=False: fail loudly if any NaN/inf slipped through rather than
    # emit a bare `NaN` token that the browser's JSON.parse would reject.
    payload = json.dumps(snap, separators=(",", ":"), allow_nan=False)  # compact
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(payload)

    raw = len(payload.encode("utf-8"))
    gz = len(gzip.compress(payload.encode("utf-8")))
    print(f"wrote {args.out}: {raw / 1e6:.2f} MB raw / {gz / 1e6:.2f} MB gzipped "
          f"({snap['meta']['n_satellites']} sats, {snap['meta']['n_conjunctions']} conj)")
    if gz > _GZ_BUDGET_BYTES:
        print(f"  ⚠ over the ~{_GZ_BUDGET_BYTES / 1e6:.0f} MB gzipped budget "
              f"— trim the catalog or drop fields")


if __name__ == "__main__":
    main()
