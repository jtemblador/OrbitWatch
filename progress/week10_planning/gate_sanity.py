#!/usr/bin/env python3
"""Hand-constructed geometry cases for the path-bound prototype."""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from path_gate import RE_KM, load_elements, pair_block  # noqa: E402


def make_df(rows):
    base = {"epoch": pd.Timestamp("2026-07-01", tz="UTC")}
    out = []
    for r in rows:
        a = r["a"]
        e = r.get("e", 0.0)
        out.append({
            "inclination": r["inc"], "ra_of_asc_node": r["raan"],
            "arg_of_pericenter": r.get("argp", 0.0), "eccentricity": e,
            "semimajor_axis": a,
            "periapsis": a * (1 - e) - RE_KM, "apoapsis": a * (1 + e) - RE_KM,
            "mean_motion": 15.0, **base,
        })
    return pd.DataFrame(out)


def run(df, label, expect_drop, realistic=False):
    d = load_elements(df)
    d["adv_err_u"] = np.zeros(len(df))
    m = pair_block(d, np.array([0]), np.array([1]), 51.0, 1.0, realistic)
    ok = bool(m["drop"][0]) == expect_drop
    print(f"{'PASS' if ok else 'FAIL'}  {label}: coarse={bool(m['coarse'][0])} "
          f"drop={bool(m['drop'][0])} (expected drop={expect_drop}) "
          f"sinIR={float(m['sinIR'][0]):.3f} frac={float(m['frac'][0]):.4f}")
    return ok


allok = True

# 1. Circular co-altitude, crossing planes: paths intersect at node -> KEEP
df = make_df([{"inc": 53, "raan": 0, "a": 6928},
              {"inc": 53, "raan": 40, "a": 6928}])
allok &= run(df, "co-altitude circular crossing", expect_drop=False)

# 2. Circular 550 km vs eccentric (peri 500 / apo 1500 alt) with apogee-side
#    geometry at BOTH nodes (argp 90 deg off the node line) -> DROP
a_ecc = (6878.0 + 7878.0) / 2.0
e_ecc = (7878.0 - 6878.0) / (7878.0 + 6878.0)
# find B's node angle u2 first with argp=0, then re-run with argp = u2 - 90
probe = make_df([{"inc": 53, "raan": 0, "a": 6928},
                 {"inc": 74, "raan": 40, "a": a_ecc, "e": e_ecc, "argp": 0}])
dp = load_elements(probe)
dp["adv_err_u"] = np.zeros(2)
# recompute u2 exactly as pair_block does
i1, o1 = dp["inc"][0], dp["raan"][0]
i2, o2 = dp["inc"][1], dp["raan"][1]
h1 = np.array([math.sin(i1) * math.sin(o1), -math.sin(i1) * math.cos(o1), math.cos(i1)])
h2 = np.array([math.sin(i2) * math.sin(o2), -math.sin(i2) * math.cos(o2), math.cos(i2)])
k = np.cross(h1, h2)
n2 = np.array([math.cos(o2), math.sin(o2), 0.0])
t2 = np.cross(h2, n2)
u2 = math.atan2(np.dot(k, t2), np.dot(k, n2))
df = make_df([{"inc": 53, "raan": 0, "a": 6928},
              {"inc": 74, "raan": 40, "a": a_ecc, "e": e_ecc,
               "argp": math.degrees(u2) - 90.0}])
allok &= run(df, "eccentric apogee-side at both nodes", expect_drop=True)

# 3. Same geometry but near-coplanar planes -> windows blow up -> KEEP
df = make_df([{"inc": 53, "raan": 0, "a": 6928},
              {"inc": 53.05, "raan": 0.05, "a": a_ecc, "e": e_ecc,
               "argp": 137.0}])
allok &= run(df, "near-coplanar eccentric (conservative keep)", expect_drop=False)

# 4. Eccentric with PERIGEE at node+ (radius matches there) -> KEEP
df = make_df([{"inc": 53, "raan": 0, "a": 6928},
              {"inc": 74, "raan": 40, "a": a_ecc, "e": e_ecc,
               "argp": math.degrees(u2)}])
allok &= run(df, "eccentric perigee-at-node (one node close)", expect_drop=False)

# 5. Case 2 under realistic margins: gap ~450 km >> margins -> still DROP
df = make_df([{"inc": 53, "raan": 0, "a": 6928},
              {"inc": 74, "raan": 40, "a": a_ecc, "e": e_ecc,
               "argp": math.degrees(u2) - 90.0}])
allok &= run(df, "case 2 with margins on", expect_drop=True, realistic=True)

# 6. Coarse-disjoint bands (400 vs 800 km circular): coarse already false
df = make_df([{"inc": 53, "raan": 0, "a": 6778},
              {"inc": 74, "raan": 40, "a": 7178}])
d6 = load_elements(df)
d6["adv_err_u"] = np.zeros(2)
m6 = pair_block(d6, np.array([0]), np.array([1]), 51.0, 1.0, False)
ok6 = not bool(m6["coarse"][0]) and not bool(m6["drop"][0])
print(f"{'PASS' if ok6 else 'FAIL'}  coarse-disjoint bands: coarse="
      f"{bool(m6['coarse'][0])} drop={bool(m6['drop'][0])}")
allok &= ok6

print("\nALL PASS" if allok else "\nFAILURES PRESENT")
sys.exit(0 if allok else 1)
