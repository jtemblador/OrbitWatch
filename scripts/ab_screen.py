#!/usr/bin/env python3
"""
A/B one catalog load through two screening paths and diff the EVENT lists
(Phase 10.3 formal Stage-1 validation; the harness the Phase 10.6 CI flip
runs before raising the snapshot cap).

The point: both Stage-1 levers (10.1a fused C++ coarse+medium, 10.2 time
sieve) claim *byte-identical events* — speed and memory change, results
never do. This script makes that claim checkable on any catalog in one
command: load once, screen twice (baseline vs candidate), compare the two
sorted event lists with ==. Exit code 0 iff identical, 1 otherwise, so a
CI job can gate on it.

    # The production CI operating point (what build_snapshot.py screens):
    python scripts/ab_screen.py --source active --mode sfs --max-sats 5000 \
        --hours 24 --step 30

    # Full catalogs: use --baseline fused. The classic path materializes the
    # coarse survivor pairs as Python tuples (~5.4 GB at 16k sats) — the very
    # thing 10.1a removed — so classic-vs-sieve at full scale measures memory
    # pressure, not the sieve. fused-vs-sieve isolates the 10.2 lever, and
    # fused==classic is already locked by tests + the 10.1a A/B.
    python scripts/ab_screen.py --source active --mode sfs --hours 6 \
        --step 30 --baseline fused

Baselines:
    classic  run_screen(fused=False)             (production today)
    fused    run_screen(fused=True, sieve=False) (10.1a, proven == classic)
Candidate is always fused=True, sieve=True — the Stage-1 path 10.6 flips on.

--start defaults to now (UTC): a freshly fetched catalog should be screened
near its epochs. Pass an explicit ISO timestamp to reproduce a prior run.
"""
import argparse
import os
import resource
import sys
import time
from datetime import datetime, timezone

# Project root for backend.*, backend/ for the orbitcore .so (which the
# orbitcore/ source dir would otherwise shadow).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.core.conjunctions import run_screen                    # noqa: E402
from backend.core.propagator import build_satrecs_and_meta          # noqa: E402
from backend.core.screening_volumes import is_screenable, regime_for  # noqa: E402
from backend.core.tle_fetcher import GPFetcher                      # noqa: E402


def _load(source: str, n: int | None, mode: str):
    """Load the cached catalog exactly the way build_snapshot.py screens it:
    HEAD-slice to n, then (SFS mode) keep only handbook-screenable orbits."""
    df = GPFetcher().load_cached(source)
    if n and len(df) > n:
        df = df.head(n).reset_index(drop=True)
    if mode == "sfs":
        keep = [is_screenable(p, e, pr) for p, e, pr in
                zip(df["periapsis"], df["eccentricity"], df["period"])]
        df = df[keep].reset_index(drop=True)
    return build_satrecs_and_meta(df)


def _screen(satrecs, meta, args, start, fused: bool, sieve: bool):
    timings: dict = {}
    if args.mode == "sfs":
        volumes = [regime_for(m["periapsis_km"], m["eccentricity"],
                              m["period_min"]) for m in meta]
        events = run_screen(satrecs, meta, start, args.hours,
                            step_sec=args.step, volumes=volumes,
                            timings=timings, fused=fused, sieve=sieve)
    else:
        events = run_screen(satrecs, meta, start, args.hours,
                            args.threshold, args.step,
                            timings=timings, fused=fused, sieve=sieve)
    return events, timings


def main() -> None:
    ap = argparse.ArgumentParser(
        description="A/B the Stage-1 screening path: byte-identical events "
                    "or exit 1 (Phase 10.3 / 10.6).")
    ap.add_argument("--source", choices=("active", "starlink"), default="active")
    ap.add_argument("--max-sats", type=int, default=None,
                    help="HEAD-slice the catalog (default: whole file)")
    ap.add_argument("--mode", choices=("sfs", "euclidean"), default="sfs")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--step", type=float, default=30.0)
    ap.add_argument("--threshold", type=float, default=25.0,
                    help="report/medium threshold, km (euclidean mode only)")
    ap.add_argument("--start", default=None,
                    help="screening start, ISO 8601 UTC (default: now)")
    ap.add_argument("--baseline", choices=("classic", "fused"), default="classic")
    args = ap.parse_args()

    start = (datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
             if args.start else datetime.now(timezone.utc).replace(microsecond=0))

    satrecs, meta = _load(args.source, args.max_sats, args.mode)
    cut = "SFS ellipsoids" if args.mode == "sfs" else f"{args.threshold} km"
    print(f"\nStage-1 A/B -- source={args.source} n={len(satrecs)} "
          f"mode={args.mode} cut={cut} window={args.hours} h "
          f"step={args.step} s start={start.isoformat()}\n"
          f"baseline={args.baseline}  candidate=fused+sieve", flush=True)

    t0 = time.perf_counter()
    ev_a, tm_a = _screen(satrecs, meta, args, start,
                         fused=(args.baseline == "fused"), sieve=False)
    t_a = time.perf_counter() - t0
    print(f"  A ({args.baseline}):     {len(ev_a):>7,} events  "
          f"medium {tm_a['t_medium']:>7.1f} s  fine {tm_a['t_fine']:>6.1f} s  "
          f"windows {tm_a['n_windows']:>9,}  total {t_a:>6.1f} s", flush=True)

    t0 = time.perf_counter()
    ev_b, tm_b = _screen(satrecs, meta, args, start, fused=True, sieve=True)
    t_b = time.perf_counter() - t0
    print(f"  B (fused+sieve): {len(ev_b):>7,} events  "
          f"medium {tm_b['t_medium']:>7.1f} s  fine {tm_b['t_fine']:>6.1f} s  "
          f"windows {tm_b['n_windows']:>9,}  total {t_b:>6.1f} s", flush=True)

    peak_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
    speedup = tm_a["t_medium"] / max(tm_b["t_medium"], 1e-9)
    identical = ev_a == ev_b
    print(f"  medium speedup {speedup:.1f}x   process peak RSS {peak_gb:.2f} GB "
          f"(dominated by the baseline run)")

    if identical:
        print(f"\nPASS: {len(ev_a):,} events byte-identical.\n")
        sys.exit(0)

    # Diagnosable failure: show set differences, not just a boolean.
    key = lambda e: (e["sat1_norad_id"], e["sat2_norad_id"], e["tca"])  # noqa: E731
    ka, kb = {key(e) for e in ev_a}, {key(e) for e in ev_b}
    print(f"\nFAIL: event lists differ "
          f"(A={len(ev_a):,}, B={len(ev_b):,}, "
          f"A-only={len(ka - kb)}, B-only={len(kb - ka)}).")
    for tag, only in (("A-only", ka - kb), ("B-only", kb - ka)):
        for k in sorted(only)[:5]:
            print(f"  {tag}: sats {k[0]}/{k[1]} tca {k[2]}")
    if not (ka - kb) and not (kb - ka):
        print("  Same event set — per-event fields or ordering differ "
              "(compare full dicts).")
    sys.exit(1)


if __name__ == "__main__":
    main()
