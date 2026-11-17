#!/usr/bin/env python3
"""
Phase 11.1c — the screening-box confounder, by re-screening.

Question: Starlink–Starlink conjunctions per 24 h screen rose ~2× between July
and November 2026 while SpaceX lowered its ~550 km shells. A Starlink whose
perigee drops to ≤ 500 km moves from the SFS LEO 2 ellipsoid (0.4×25×25 km) to
LEO 1 (0.4×44×51 km), and a pair takes the larger of its two objects' boxes
(conjunctions.run_screen). How much of the rise is the bigger box rather than
more crowding?

Method: take one archived FULL snapshot (pre-10.10 shape — `satellites[]`
carries complete OMM), rebuild the exact screen input the cron used
(GPFetcher._parse_json → is_screenable → build_satrecs_and_meta), restrict it
to Starlinks (the frontend's constellationOf rule, ported in
archive_analysis.constellation_of), and screen from the snapshot's own
`generated_at` with the production engine (fused+sieve+refine, 30 s grid)
under three volume policies:

    actual     — regime_for per object, as production
    held_leo2  — every Starlink that would be LEO 1 is screened as LEO 2
    forced_leo1 — every Starlink is screened as LEO 1

Starlink–Starlink events depend only on the two Starlinks (cascade, suppression
and the one-event-per-pair de-dupe are all per pair), so the Starlink-only
screen reproduces the archived run's Starlink–Starlink events exactly — the
`actual` policy is checked against the archive as a reproduction test.

Needs the compiled orbitcore module in backend/ (gitignored; see CLAUDE.md).
    python scripts/box_confounder.py DIR/2026-07-16T15-16-18.435678Z.json.gz \
        DIR/2026-10-15T17-48-25.123895Z.json.gz --hours 24
"""
import argparse
import gzip
import json
import os
import sys
import time

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from archive_analysis import constellation_of  # noqa: E402
from backend.core.conjunctions import run_screen  # noqa: E402
from backend.core.propagator import build_satrecs_and_meta  # noqa: E402
from backend.core.screening_volumes import LEO_1, LEO_2, is_screenable, regime_for  # noqa: E402
from backend.core.tle_fetcher import GPFetcher  # noqa: E402

POLICIES = ("actual", "held_leo2", "forced_leo1")


def volumes_for(meta, policy):
    vols = [regime_for(m["periapsis_km"], m["eccentricity"], m["period_min"]) for m in meta]
    if policy == "held_leo2":
        return [LEO_2 if v is LEO_1 else v for v in vols]
    if policy == "forced_leo1":
        return [LEO_1] * len(vols)
    return vols


def screen_snapshot(path, hours, policies):
    with gzip.open(path) as f:
        snap = json.load(f)
    if "satellites" not in snap:
        sys.exit(f"{path}: slim-v1 record has no OMM — use a pre-Oct-29 full snapshot")
    start = pd.Timestamp(snap["meta"]["generated_at"]).to_pydatetime()
    df = GPFetcher()._parse_json([s.get("omm", s) for s in snap["satellites"]])
    df = df[[is_screenable(p, e, t) for p, e, t in
             zip(df["periapsis"], df["eccentricity"], df["period"])]]
    sl = df[df["object_name"].map(lambda n: constellation_of(n) == "STARLINK")].reset_index(drop=True)
    satrecs, meta = build_satrecs_and_meta(sl)
    in_leo1 = sum(regime_for(m["periapsis_km"], m["eccentricity"], m["period_min"]) is LEO_1
                  for m in meta)
    ids = set(sl["norad_cat_id"])
    archived = {frozenset((c["a"], c["b"])) for c in snap["conjunctions"]
                if c["a"] in ids and c["b"] in ids}
    res = {"snapshot": os.path.basename(path), "generated_at": snap["meta"]["generated_at"],
           "hours": hours, "starlinks_screened": len(sl), "starlinks_in_leo1": in_leo1}
    for pol in policies:
        t0 = time.perf_counter()
        ev = run_screen(satrecs, meta, start, hours, volumes=volumes_for(meta, pol),
                        step_sec=30.0, fused=True, sieve=True, refine=True)
        pairs = {frozenset((e["sat1_norad_id"], e["sat2_norad_id"])) for e in ev}
        res[pol] = {"events": len(ev), "seconds": round(time.perf_counter() - t0, 1)}
        if pol == "actual" and hours == float(snap["meta"]["screen"]["window_hours"]):
            res[pol]["archived_events"] = len(archived)
            res[pol]["identical_pair_set"] = pairs == archived
        print(json.dumps({pol: res[pol]}), flush=True)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("snapshots", nargs="+", help="archived full snapshots (*.json.gz)")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--policies", default=",".join(POLICIES))
    ap.add_argument("--out", default="", help="write results JSON here")
    args = ap.parse_args()
    out = [screen_snapshot(p, args.hours, args.policies.split(",")) for p in args.snapshots]
    if len(out) == 2 and all(p in out[0] for p in POLICIES):
        a, b = out
        r = {p: round(b[p]["events"] / a[p]["events"], 3) for p in POLICIES}
        out.append({"growth_ratio": r})
    print(json.dumps(out, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
            f.write("\n")


if __name__ == "__main__":
    main()
