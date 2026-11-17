"""Plot trends from the orphan `data` branch snapshot archive.

Usage: git archive origin/data | tar -x -C DIR ; python scripts/plot_archive_trends.py DIR/snapshots OUT.png
"""
import glob, gzip, json, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np, pandas as pd

BLUE, GREY, RED = "#2a6fdb", "#8a8f98", "#d9483b"

def load(d):
    rows, last = [], None
    for p in sorted(glob.glob(f"{d}/*.json.gz")):
        try: s = json.load(gzip.open(p))
        except Exception: continue
        m, r = s["meta"], s["meta"].get("run") or {}
        c = pd.DataFrame(s["conjunctions"]); last = c
        rows.append(dict(t=pd.to_datetime(m["generated_at"]), n_sat=m["n_satellites"],
            n_conj=m["n_conjunctions"], age=m.get("max_epoch_age_days"),
            secs=r.get("screen_seconds"), lt1=int((c.miss_km < 1).sum())))
    return pd.DataFrame(rows), last

def style(ax, title, ylab):
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    ax.set_ylabel(ylab, color="#555"); ax.grid(axis="y", alpha=.25)
    for s in ("top", "right"): ax.spines[s].set_visible(False)

df, last = load(sys.argv[1])
fig, ax = plt.subplots(2, 3, figsize=(16, 8.5)); ax = ax.ravel()
ax[0].plot(df.t, df.n_sat, color=BLUE, lw=2); style(ax[0], "Objects tracked", "satellites")
ax[1].plot(df.t, df.n_conj, color=BLUE, lw=1.4); style(ax[1], "Conjunctions found per 24 h screen", "events")
ax[2].plot(df.t, df.lt1, color=RED, lw=1.4); style(ax[2], "Close calls (<1 km) per screen", "events")
ax[3].plot(df.t, df.age, color=GREY, lw=1.6); style(ax[3], "Oldest element set in each snapshot", "days")
d = df.dropna(subset=["secs"]); ax[4].scatter(d.t, d.secs / 60, s=18, color=BLUE)
style(ax[4], "Full-catalog screen runtime (CI)", "minutes")
ax[5].hist(last.miss_km.clip(lower=0.01), bins=np.logspace(-2, np.log10(last.miss_km.max()), 50), color=BLUE, edgecolor="white")
ax[5].set_xscale("log")
style(ax[5], f"Latest run: miss distance ({len(last):,} events)", "events"); ax[5].set_xlabel("km (log scale)", color="#555")
import matplotlib.dates as md
for a in ax[:5]:
    a.xaxis.set_major_formatter(md.ConciseDateFormatter(md.AutoDateLocator()))
fig.suptitle("OrbitWatch: 4 months of robot-job archive", x=.01, ha="left", fontsize=14, fontweight="bold")
fig.tight_layout(); fig.savefig(sys.argv[2], dpi=140)
