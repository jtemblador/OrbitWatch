import pandas as pd, numpy as np, json, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
M="/tmp/claude-0/-home-user-OrbitWatch/3c268d84-e8a1-5102-b3b8-d7dc9912af58/scratchpad/maneuver"
B,O,A,INK,MUTE="#2a78d6","#eb6834","#1baf7a","#0b0b0b","#8a8985"
plt.rcParams.update({"axes.spines.top":False,"axes.spines.right":False,"axes.edgecolor":MUTE,"axes.grid":True,"grid.color":"#e6e5e1","grid.linewidth":.6,"font.size":9,"axes.titlesize":10,"figure.facecolor":"#fcfcfb","axes.facecolor":"#fcfcfb"})
f=pd.read_parquet(f"{M}/flags.parquet")
# ---- fig1: example histories
s=f[f.OBJECT_NAME.str.startswith("STARLINK")]
st=s.groupby("NORAD_CAT_ID").alt.agg(["first","max","size"])
raiser=st[(st["max"]-st["first"]>150)&(st["size"]>150)].index[0]
lem=f[f.OBJECT_NAME.str.startswith("FLOCK 4BE-36")].NORAD_CAT_ID.iloc[0]
objs=[(25544,"ISS (ZARYA) — reboosts"),(raiser,None),(lem,"FLOCK 4BE-36 — no propulsion, drag only")]
fig,ax=plt.subplots(3,1,figsize=(8,7.2),sharex=True)
for a_,(o,t) in zip(ax,objs):
    d=f[f.NORAD_CAT_ID==o]
    a_.plot(d.EPOCH,d.alt,color=B,lw=1.6,label="mean altitude (from n)")
    fl=d[d.flag_c]; a_.scatter(fl.EPOCH,fl.alt,s=28,color=O,zorder=3,edgecolor="#fcfcfb",lw=1.5,label=f"confirmed maneuver step ({len(fl)})")
    a_.set_title(t or f"{d.OBJECT_NAME.iloc[-1]} — orbit raising after launch",loc="left",color=INK)
    a_.set_ylabel("altitude, km"); a_.legend(frameon=False,loc="best")
import matplotlib.dates as mdates
ax[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
fig.suptitle("Detector on three element histories (one distinct element set per archive run)",x=.01,ha="left",fontsize=11)
fig.tight_layout(); fig.savefig(f"{M}/fig1_examples.png",dpi=140)
# ---- fig2: |dmiss| CCDF tagged vs untagged
L=pd.read_parquet(f"{M}/linked.parquet"); F=L[L.fresh]
fig,ax=plt.subplots(1,2,figsize=(9,3.8))
x=np.logspace(-2,2,200)
for lab,sub,c in [("untagged",F[~F.tag48].dmiss,MUTE),("tagged: maneuver ≤48 h before run",F[F.tag48].dmiss,B),("maneuver confirmed at the next run\n(hindsight, not displayable)",F[F.mv_between].dmiss,O)]:
    ax[0].plot(x,[(sub>v).mean() for v in x],color=c,lw=2,label=f"{lab} (n={len(sub):,})")
ax[0].set_xscale("log"); ax[0].set_xlabel("|Δmiss| to next run, km"); ax[0].set_ylabel("fraction exceeding"); ax[0].legend(frameon=False,fontsize=7.5)
ax[0].set_title("Run-to-run |Δmiss|, fresh-element re-predictions",loc="left")
F=F.assign(age=np.fmax(F.a_age,F.b_age)); bins=[0,.5,1,2,100]; F["ab"]=pd.cut(F.age,bins)
g=F.groupby(["ab","tag48"],observed=True).dmiss.median().unstack()
xs=np.arange(len(g)); w=.38
ax[1].bar(xs-w/2-.01,g[False],w,color=MUTE,label="untagged"); ax[1].bar(xs+w/2+.01,g[True],w,color=B,label="tagged (≤48 h)")
for i,(u,t) in enumerate(zip(g[False],g[True])): ax[1].text(i+w/2,t,f"{t:.2f}",ha="center",va="bottom",fontsize=8); ax[1].text(i-w/2,u,f"{u:.2f}",ha="center",va="bottom",fontsize=8,color="#52514e")
ax[1].set_xticks(xs,["≤0.5 d","0.5–1 d","1–2 d",">2 d"]); ax[1].set_xlabel("older participant element age"); ax[1].set_ylabel("median |Δmiss|, km")
ax[1].set_title("Holds at every element age",loc="left"); ax[1].legend(frameon=False); ax[1].grid(axis="x",visible=False)
fig.tight_layout(); fig.savefig(f"{M}/fig2_dmiss.png",dpi=140)
# ---- fig3: flags per object per 30 d by group
def grp(nm):
    for k in ["STARLINK","ONEWEB","KUIPER","QIANFAN","FLOCK","LEMUR","IRIDIUM","GLOBALSTAR"]:
        if nm.startswith(k): return k
    return "OTHER"
PASSIVE=[900,902,1520,1361,5398,7646,16908,22824,38077,35932,20580]
f=f[f.dt_d.notna()&(f.alt<2000)].copy(); f["grp"]=f.OBJECT_NAME.map(grp); f.loc[f.NORAD_CAT_ID.isin(PASSIVE),"grp"]="PASSIVE (10)"
d=f.groupby(["grp","NORAD_CAT_ID"]).agg(fl=("flag_c","sum"),d=("EPOCH",lambda s:(s.max()-s.min()).total_seconds()/86400)); d=d[d.d>10]
r=(d.groupby("grp").fl.sum()/d.groupby("grp").d.sum()*30).sort_values()
prop={"STARLINK","ONEWEB","KUIPER","QIANFAN","IRIDIUM","GLOBALSTAR","OTHER"}
fig,ax=plt.subplots(figsize=(7,3.6))
ax.barh(r.index,r.values,color=[B if g in prop else A for g in r.index],height=.6)
for i,v in enumerate(r.values): ax.text(v+.1,i,f"{v:.2f}",va="center",fontsize=8)
ax.set_xlabel("confirmed maneuver steps per object per 30 days (sustained thrust counts every step)"); ax.grid(axis="y",visible=False)
ax.set_title("Detection rate by group — blue: propulsive fleets, green: no propulsion (false-positive check)",loc="left",fontsize=9)
fig.tight_layout(); fig.savefig(f"{M}/fig3_rates.png",dpi=140)
print(r.round(2).to_dict())
