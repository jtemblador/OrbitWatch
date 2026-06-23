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
from backend.core.propagator import SatellitePropagator    # noqa: E402
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


def _profile_one(source, n, hours, threshold, step, tmpdir):
    """One (load + full screen) measurement. Returns (t_load, timings dict)."""
    prop = _make_propagator(source, n, tmpdir)

    t0 = time.perf_counter()
    satrecs, meta = prop.get_all_satrecs()   # sgp4init() for every satellite
    t_load = time.perf_counter() - t0

    timings: dict = {}
    run_screen(satrecs, meta, _START, hours, threshold, step, timings=timings)
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
    ap.add_argument("--source", choices=("starlink", "synth"), default="starlink")
    ap.add_argument("--sizes", default="300,800,1500",
                    help="comma-separated satellite counts (intra-shell slices)")
    ap.add_argument("--full", action="store_true",
                    help="also run the full multi-shell catalog (starlink only; slow)")
    ap.add_argument("--hours", type=float, default=3.0, help="screening window, h")
    ap.add_argument("--threshold", type=float, default=25.0,
                    help="report/medium threshold, km")
    ap.add_argument("--step", type=float, default=60.0, help="medium step, s")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    # The 'starlink' source reads the gitignored backend/data/tle/starlink.parquet
    # (a fresh clone won't have it). Fail with a clear pointer instead of a raw
    # FileNotFoundError from deep in load_cached().
    if args.source == "starlink":
        cache = GPFetcher().cache_dir / "starlink.parquet"
        if not cache.exists():
            sys.exit(
                f"\nNo Starlink cache at {cache} (it's gitignored).\n"
                f"  Offline, representative run:  "
                f"python scripts/profile_screening.py --source synth\n"
                f"  Or populate the real catalog first (a live CelesTrak fetch "
                f"of the 'starlink' group).\n")

    print(f"\nConjunction screening profile -- source={args.source}, "
          f"window={args.hours} h, threshold={args.threshold} km, "
          f"step={args.step} s  (start={_START.date()})\n")
    print(_HDR)
    print("-" * len(_HDR))

    with tempfile.TemporaryDirectory() as tmp:
        for n in sizes:
            t_load, tm = _profile_one(
                args.source, n, args.hours, args.threshold, args.step, tmp)
            _print_row(t_load, tm)

        if args.full and args.source == "starlink":
            print("  ... full multi-shell catalog (can take minutes) ...")
            t_load, tm = _profile_one(
                "starlink", None, args.hours, args.threshold, args.step, tmp)
            _print_row(t_load, tm)
        elif args.full:
            print("  (--full ignored: only meaningful for --source starlink)")

    print()


if __name__ == "__main__":
    main()
