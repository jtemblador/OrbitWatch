"""Flatten per-object element sets (n, e, i, epoch [+raan, bstar, ndot when full]) from the archive."""
import gzip, json, glob, sys, os
from multiprocessing import Pool
import pandas as pd, numpy as np
S = sys.argv[1]; OUT = os.path.dirname(os.path.abspath(__file__))
COLS = ["NORAD_CAT_ID", "EPOCH", "OBJECT_NAME", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION"]
def one(f):
    d = json.load(gzip.open(f))
    if "elements" in d:
        el = pd.DataFrame(d["elements"])[COLS]
        for c in ["RA_OF_ASC_NODE", "BSTAR", "MEAN_MOTION_DOT"]: el[c] = np.nan
    else:
        el = pd.DataFrame(d["satellites"])[COLS + ["RA_OF_ASC_NODE", "BSTAR", "MEAN_MOTION_DOT"]]
    el["gen"] = pd.Timestamp(d["meta"]["generated_at"]).tz_convert(None) if pd.Timestamp(d["meta"]["generated_at"]).tzinfo else pd.Timestamp(d["meta"]["generated_at"])
    return el
if __name__ == "__main__":
    fs = sorted(glob.glob(f"{S}/*.json.gz"))
    with Pool(4) as p: res = p.map(one, fs, chunksize=4)
    el = pd.concat(res, ignore_index=True)
    el["EPOCH"] = pd.to_datetime(el["EPOCH"], format="ISO8601", utc=True).dt.tz_convert(None)
    # first run in which each (object, epoch) appeared = when we'd have learned of it
    el = el.sort_values("gen").drop_duplicates(["NORAD_CAT_ID", "EPOCH"], keep="first")
    for c in ["MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE", "BSTAR", "MEAN_MOTION_DOT"]:
        el[c] = el[c].astype("float64")
    el = el.sort_values(["NORAD_CAT_ID", "EPOCH"]).reset_index(drop=True)
    el.to_parquet(f"{OUT}/elements.parquet")
    print(len(fs), len(el), el.NORAD_CAT_ID.nunique())
