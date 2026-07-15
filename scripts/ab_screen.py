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

    # Full catalog: use --baseline fused --refine. At 16k sats the classic path
    # can't run (it re-materializes the ~5 GB Python pair list 10.1a removed AND
    # the ~13 GB fine dicts #7), so classic-vs-flip measures memory pressure,
    # not correctness. --baseline fused --refine compares fused+refine (sieve
    # OFF) vs fused+sieve+refine: both keep the C++ fine pre-cut, so both stay
    # memory-safe, and the diff isolates exactly the sieve's event-level no-skip
    # on the full real catalog. (fused==classic is locked by tests + 10.1a.)
    python scripts/ab_screen.py --source active --mode sfs --hours 24 \
        --step 30 --baseline fused --refine

Baselines (A run):
    classic  run_screen(fused=False)                  (production today; <=5k)
    fused    run_screen(fused=True, sieve=False)       (10.1a, proven==classic)
             + refine=True when --refine (keeps A memory-safe at full scale;
             refine is event-null, so A is still a valid sieve-OFF reference)
Candidate (B) is fused=True, sieve=True, plus refine=True when --refine (the
Phase-10.4 C++ fine pre-cut). --baseline classic --refine is the complete
production flip at a bounded scale; --baseline fused --refine is the full-scale
sieve check. Both gate 10.6.

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


def _load(source: str, n: int | None, mode: str, min_objects: int = 0):
    """Load the cached catalog exactly the way build_snapshot.py screens it:
    HEAD-slice to n, then (SFS mode) keep only handbook-screenable orbits."""
    df = GPFetcher().load_cached(source)
    # Sanity floor on the RAW catalog (before the head-slice, so --max-sats can't
    # defeat it): a truncated/rate-limited fetch would let both A and B screen the
    # same tiny catalog, agree trivially, and PASS the gate on garbage. Refuse.
    if min_objects and len(df) < min_objects:
        print(f"\nERROR: catalog '{source}' has {len(df)} objects, below the "
              f"--min-objects floor of {min_objects} — the A/B gate would pass "
              f"trivially on a truncated catalog. Aborting.", file=sys.stderr)
        sys.exit(1)
    if n and len(df) > n:
        df = df.head(n).reset_index(drop=True)
    if mode == "sfs":
        keep = [is_screenable(p, e, pr) for p, e, pr in
                zip(df["periapsis"], df["eccentricity"], df["period"])]
        df = df[keep].reset_index(drop=True)
    return build_satrecs_and_meta(df)


def _screen(satrecs, meta, args, start, fused: bool, sieve: bool,
            refine: bool = False):
    timings: dict = {}
    if args.mode == "sfs":
        volumes = [regime_for(m["periapsis_km"], m["eccentricity"],
                              m["period_min"]) for m in meta]
        events = run_screen(satrecs, meta, start, args.hours,
                            step_sec=args.step, volumes=volumes,
                            timings=timings, fused=fused, sieve=sieve,
                            refine=refine)
    else:
        events = run_screen(satrecs, meta, start, args.hours,
                            args.threshold, args.step,
                            timings=timings, fused=fused, sieve=sieve,
                            refine=refine)
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
    ap.add_argument("--refine", action="store_true",
                    help="candidate adds the Phase-10.4 C++ fine pre-cut "
                         "(fused+sieve+refine — the full Stage-1+2 flip)")
    ap.add_argument("--min-objects", type=int, default=0,
                    help="abort (exit 1) if the raw catalog has fewer than this "
                         "many objects — so a truncated fetch can't PASS the gate "
                         "trivially (0 = off; CI passes a floor for --source active)")
    args = ap.parse_args()

    start = (datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
             if args.start else datetime.now(timezone.utc).replace(microsecond=0))

    satrecs, meta = _load(args.source, args.max_sats, args.mode, args.min_objects)
    cut = "SFS ellipsoids" if args.mode == "sfs" else f"{args.threshold} km"
    cand = "fused+sieve+refine" if args.refine else "fused+sieve"
    print(f"\nStage-1 A/B -- source={args.source} n={len(satrecs)} "
          f"mode={args.mode} cut={cut} window={args.hours} h "
          f"step={args.step} s start={start.isoformat()}\n"
          f"baseline={args.baseline}  candidate={cand}", flush=True)

    # The fused baseline honors --refine too, so the full-catalog A/B stays
    # memory-safe: with sieve OFF the medium scan flags ~17M windows, and only
    # the C++ fine pre-cut (refine) keeps them from materializing as ~13 GB of
    # Python dicts (the scaling_tracker #7 wall). refine is event-null by
    # construction, so A is still a valid sieve-OFF reference — the A/B isolates
    # exactly the sieve. (classic can't refine — refine requires fused.)
    fused_base = args.baseline == "fused"
    base_refine = args.refine and fused_base
    a_label = "fused+refine" if base_refine else args.baseline
    t0 = time.perf_counter()
    ev_a, tm_a = _screen(satrecs, meta, args, start,
                         fused=fused_base, sieve=False, refine=base_refine)
    t_a = time.perf_counter() - t0
    print(f"  A ({a_label}):     {len(ev_a):>7,} events  "
          f"medium {tm_a['t_medium']:>7.1f} s  fine {tm_a['t_fine']:>6.1f} s  "
          f"windows {tm_a['n_windows']:>9,}  total {t_a:>6.1f} s", flush=True)

    t0 = time.perf_counter()
    ev_b, tm_b = _screen(satrecs, meta, args, start, fused=True, sieve=True,
                         refine=args.refine)
    t_b = time.perf_counter() - t0
    surv = (f"  survivors {tm_b['n_survivors']:>7,}"
            if "n_survivors" in tm_b else "")
    print(f"  B ({cand}): {len(ev_b):>7,} events  "
          f"medium {tm_b['t_medium']:>7.1f} s  fine {tm_b['t_fine']:>6.1f} s  "
          f"windows {tm_b['n_windows']:>9,}{surv}  total {t_b:>6.1f} s",
          flush=True)

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
