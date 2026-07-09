#!/usr/bin/env python3
"""
Profile the conjunction screening cascade as the catalog scales toward the
full Starlink set (Phase 7.1).

Phase 7.1 is a *measurement* task: run the real cascade (coarse -> medium ->
fine -> RTN) at increasing satellite counts, record per-stage wall time and
survivor / window / event counts, and find where the cost concentrates -- so
the Phase 7.3 optimizations (fuse the coarse cut inside medium_filter; move the
screen off the event loop) are driven by numbers, not guesses.

This script changes NO screening behavior. Its only hook into the pipeline is
the `timings` dict that run_screen() populates when asked (a no-op otherwise).

Run from the project root:
    python scripts/profile_screening.py                 # default sweep, offline
    python scripts/profile_screening.py --full          # + the full 10,544-sat cross-shell run (slow)
    python scripts/profile_screening.py --source synth   # deterministic, no data file needed
    python scripts/profile_screening.py --hours 24 --threshold 50 --sizes 300,1000,2000

Sources:
    starlink  (default) -- slice the densest shell of the on-disk
                           backend/data/tle/starlink.parquet to each size
                           (intra-shell: ~all pairs coarse-survive, the worst
                           case for the medium filter). --full adds the whole
                           multi-shell catalog (the realistic cross-shell case).
    synth                -- build_synthetic_shell(n): deterministic, no network
                           or data file. One shell, like starlink intra-shell.
    active               -- the on-disk active.parquet, HEAD-sliced to each
                           size and (in --mode sfs) filtered to screenable
                           orbits -- exactly build_snapshot.py's production
                           path, so this profiles the CI operating point
                           (Phase 10.0/10.4): --source active --mode sfs
                           --sizes 5000 --hours 24 --step 30.

Modes (Phase 10.0):
    euclidean (default) -- legacy threshold_km report cut (the 7.1 baseline).
    sfs                  -- per-object SFS RTN ellipsoids (regime_for), gross
                           threshold = largest semi-axis; --threshold ignored.
                           This is what the CI snapshot job runs.

Columns: t_coarse / t_medium include the C++<->Python survivor/row
materialization (the scaling_tracker #3 boundary cost); t_load is the one-time
sgp4init() of every satellite; peakRSS is the process high-water mark.
"""
import argparse
import os
import resource
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Resolve imports whether run as `python scripts/profile_screening.py` or from
# elsewhere: project root for `backend.*`, backend/ for the orbitcore .so
# (which the orbitcore/ source dir would otherwise shadow).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.core.conjunctions import run_screen          # noqa: E402
from backend.core.demo_seed import build_synthetic_shell   # noqa: E402
from backend.core.propagator import SatellitePropagator, build_satrecs_and_meta  # noqa: E402
from backend.core.screening_volumes import is_screenable, regime_for  # noqa: E402
from backend.core.tle_fetcher import GPFetcher             # noqa: E402

# Fixed screening instant so runs are comparable. Equal to build_synthetic_shell's
# default epoch, so the synthetic geometry is well-conditioned (near epoch).
_START = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _peak_rss_mb() -> float:
    """Process peak resident set, MB. ru_maxrss is kB on Linux (the target)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _make_propagator(source: str, n: int | None, tmpdir: str) -> SatellitePropagator:
    if source == "starlink":
        # Offline: a non-live propagator calls load_cached("starlink"), reading
        # the on-disk parquet (no network). max_sats=None screens the whole
        # multi-shell catalog; max_sats=N slices the densest shell to N rows
        # (intra-shell -- the coarse-survival worst case).
        return SatellitePropagator(group="starlink", max_sats=n)
    # synth: deterministic single shell, needs no data file.
    df = build_synthetic_shell(n=n or 300)
    df.to_parquet(Path(tmpdir) / "profshell.parquet", index=False)
    return SatellitePropagator(
        group="profshell", fetcher=GPFetcher(cache_dir=Path(tmpdir)))


def _load_satrecs(source, n, mode, tmpdir):
    """(satrecs, meta, t_load) for one run. The 'active' source reproduces
    build_snapshot.py's production path: HEAD-slice to n, then (SFS mode only)
    keep screenable orbits -- NOT the densest-shell slice the propagator does."""
    if source == "active":
        df = GPFetcher().load_cached("active")
        if n and len(df) > n:
            df = df.head(n).reset_index(drop=True)
        if mode == "sfs":
            keep = [is_screenable(p, e, pr) for p, e, pr in
                    zip(df["periapsis"], df["eccentricity"], df["period"])]
            df = df[keep].reset_index(drop=True)
        t0 = time.perf_counter()
        satrecs, meta = build_satrecs_and_meta(df)
        return satrecs, meta, time.perf_counter() - t0

    prop = _make_propagator(source, n, tmpdir)
    t0 = time.perf_counter()
    satrecs, meta = prop.get_all_satrecs()   # sgp4init() for every satellite
    return satrecs, meta, time.perf_counter() - t0


def _profile_one(source, n, hours, threshold, step, tmpdir,
                 mode="euclidean", start=_START, fused=False, sieve=False,
                 refine=False):
    """One (load + full screen) measurement. Returns (t_load, timings dict)."""
    satrecs, meta, t_load = _load_satrecs(source, n, mode, tmpdir)

    timings: dict = {}
    if mode == "sfs":
        volumes = [regime_for(m["periapsis_km"], m["eccentricity"],
                              m["period_min"]) for m in meta]
        run_screen(satrecs, meta, start, hours, step_sec=step,
                   volumes=volumes, timings=timings, fused=fused, sieve=sieve,
                   refine=refine)
    else:
        run_screen(satrecs, meta, start, hours, threshold, step,
                   timings=timings, fused=fused, sieve=sieve, refine=refine)
    return t_load, timings


_HDR = (f"{'N sats':>7} {'pairs':>12} {'windows':>8} {'events':>7} "
        f"{'t_load':>8} {'t_coarse':>9} {'t_medium':>9} {'t_fine':>8} "
        f"{'t_total':>8} {'peakRSS':>8}")


def _print_row(t_load: float, tm: dict) -> None:
    t_total = tm["t_coarse"] + tm["t_medium"] + tm["t_fine"]
    print(f"{tm['n_sats']:>7,} {tm['n_pairs']:>12,} {tm['n_windows']:>8,} "
          f"{tm['n_events']:>7,} {t_load:>8.2f} {tm['t_coarse']:>9.2f} "
          f"{tm['t_medium']:>9.2f} {tm['t_fine']:>8.2f} {t_total:>8.2f} "
          f"{_peak_rss_mb():>6.0f}M")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Profile the conjunction cascade at scale (Phase 7.1).")
    ap.add_argument("--source", choices=("starlink", "synth", "active"),
                    default="starlink")
    ap.add_argument("--sizes", default="300,800,1500",
                    help="comma-separated satellite counts (intra-shell "
                         "slices; head-slices for --source active)")
    ap.add_argument("--full", action="store_true",
                    help="also run the full catalog (starlink/active; slow)")
    ap.add_argument("--hours", type=float, default=3.0, help="screening window, h")
    ap.add_argument("--threshold", type=float, default=25.0,
                    help="report/medium threshold, km (euclidean mode only)")
    ap.add_argument("--step", type=float, default=60.0, help="medium step, s")
    ap.add_argument("--mode", choices=("euclidean", "sfs"), default="euclidean",
                    help="report cut: legacy Euclidean threshold, or the SFS "
                         "RTN ellipsoids the CI snapshot job runs")
    ap.add_argument("--start", default=None,
                    help="screening start (ISO 8601 UTC). Default: the fixed "
                         "2026-06-01 profiling instant; pass a current date "
                         "when profiling a freshly fetched catalog")
    ap.add_argument("--fused", action="store_true",
                    help="use the fused C++ stage (screen_pairs) — the coarse "
                         "survivor pairs never materialize in Python (Phase "
                         "10.1a memory lever); byte-identical events")
    ap.add_argument("--sieve", action="store_true",
                    help="with --fused: enable the Phase-10.2 time sieve "
                         "(per-pair node-crossing scan intervals; ~40x less "
                         "medium pair-step work, identical events)")
    ap.add_argument("--refine", action="store_true",
                    help="with --fused: enable the Phase-10.4 C++ fine "
                         "pre-cut (OpenMP TCA refinement in-engine; only "
                         "report-cut survivors reach Python — identical "
                         "events, the full-catalog memory lever)")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    start = (datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
             if args.start else _START)

    # The starlink/active sources read gitignored backend/data/tle parquets
    # (a fresh clone won't have them). Fail with a clear pointer instead of a
    # raw FileNotFoundError from deep in load_cached().
    if args.source in ("starlink", "active"):
        cache = GPFetcher().cache_dir / f"{args.source}.parquet"
        if not cache.exists():
            sys.exit(
                f"\nNo {args.source} cache at {cache} (it's gitignored).\n"
                f"  Offline, representative run:  "
                f"python scripts/profile_screening.py --source synth\n"
                f"  Or populate the real catalog first (a live CelesTrak fetch "
                f"of the '{args.source}' group).\n")

    cut = "SFS ellipsoids" if args.mode == "sfs" else f"{args.threshold} km"
    print(f"\nConjunction screening profile -- source={args.source}, "
          f"mode={args.mode}, window={args.hours} h, cut={cut}, "
          f"step={args.step} s  (start={start.date()})\n")
    print(_HDR)
    print("-" * len(_HDR))

    with tempfile.TemporaryDirectory() as tmp:
        for n in sizes:
            t_load, tm = _profile_one(
                args.source, n, args.hours, args.threshold, args.step, tmp,
                mode=args.mode, start=start, fused=args.fused,
                sieve=args.sieve, refine=args.refine)
            _print_row(t_load, tm)

        if args.full and args.source in ("starlink", "active"):
            print("  ... full catalog (can take minutes) ...")
            t_load, tm = _profile_one(
                args.source, None, args.hours, args.threshold, args.step, tmp,
                mode=args.mode, start=start, fused=args.fused,
                sieve=args.sieve, refine=args.refine)
            _print_row(t_load, tm)
        elif args.full:
            print("  (--full ignored: not meaningful for --source synth)")

    print()


if __name__ == "__main__":
    main()
