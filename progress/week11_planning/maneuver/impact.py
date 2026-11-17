"""Impact: are events involving a recently-maneuvered object less stable run-to-run?"""
import pandas as pd, numpy as np, json
D="/tmp/claude-0/-home-user-OrbitWatch/3c268d84-e8a1-5102-b3b8-d7dc9912af58/scratchpad"
M=f"{D}/maneuver"
f=pd.read_parquet(f"{M}/flags.parquet")
e=pd.read_parquet(f"{D}/analysis/events.parquet", columns=["a","b","tca","miss_km","rel_speed_km_s","gen","lead_h","a_age","b_age","a_nm","b_nm"])
gens=np.sort(e.gen.unique())
# --- per (object, run): hours since the latest flagged element set KNOWN at that run (epoch of the post-burn set)
FLAG=__import__("os").environ.get("FLAG","flag_c")
fl=f[f[FLAG]].copy()
if FLAG=="flag_c": fl["gen"]=fl["confirm_gen"]
fl=fl[["NORAD_CAT_ID","EPOCH","gen"]].sort_values("gen")
def hours_since(obj, g):
    q=pd.DataFrame({"NORAD_CAT_ID":obj.astype(fl.NORAD_CAT_ID.dtype),"gen":g}).reset_index()
    q=q.sort_values("gen")
    r=pd.merge_asof(q, fl.rename(columns={"EPOCH":"mep"})[["NORAD_CAT_ID","gen","mep"]].sort_values("gen"),
                    on="gen", by="NORAD_CAT_ID", direction="backward")
    # merge_asof gives the LAST-known flagged set, which is also the latest-epoch one (gen monotone w/ epoch per object)
    r=r.set_index("index").sort_index()
    return ((r.gen-r.mep).dt.total_seconds()/3600).to_numpy()
e["ha"]=hours_since(e.a.to_numpy(), e.gen.to_numpy()); e["hb"]=hours_since(e.b.to_numpy(), e.gen.to_numpy())
e["hmin"]=np.fmin(e.ha,e.hb)
e["tag24"]=e.hmin<=24; e["tag48"]=e.hmin<=48
sl=lambda s: s.fillna("").str.startswith("STARLINK")
e["cls"]=np.select([sl(e.a_nm)&sl(e.b_nm), sl(e.a_nm)|sl(e.b_nm)],["SL-SL","SL-other"],"other-other")
# --- relink to next run on pair + TCA +-900 s
gi={g:i for i,g in enumerate(gens)}; e["k"]=e.gen.map(gi)
e["p1"]=e[["a","b"]].min(axis=1); e["p2"]=e[["a","b"]].max(axis=1)
e["ep_a"]=e.gen-pd.to_timedelta(e.a_age,unit="D"); e["ep_b"]=e.gen-pd.to_timedelta(e.b_age,unit="D")
e["epa1"]=np.where(e.a==e.p1,e.ep_a,e.ep_b); e["epa2"]=np.where(e.a==e.p1,e.ep_b,e.ep_a)
nxt=e[["k","p1","p2","tca","miss_km","epa1","epa2"]].copy(); nxt["k"]-=1
L=e.merge(nxt,on=["k","p1","p2"],suffixes=("","_n"))
L=L[(L.tca-L.tca_n).abs().dt.total_seconds()<=900]
L["dt_tca"]=(L.tca-L.tca_n).abs()
L=L.sort_values("dt_tca").drop_duplicates(["k","p1","p2","tca"])
L["dmiss"]=(L.miss_km_n-L.miss_km).abs()
L["fresh"]=((L.epa1_n-L.epa1).abs().dt.total_seconds()>1)|((L.epa2_n-L.epa2).abs().dt.total_seconds()>1)
# maneuver detected BETWEEN run k and k+1 (a flagged set first seen at run k+1) - explanatory, not available at display time
fset=fl[["NORAD_CAT_ID","gen"]].copy(); fset["k1"]=fset.gen.map(gi)
mv=set(zip(fset.NORAD_CAT_ID,fset.k1))
L["mv_between"]=[((x,k+1) in mv) or ((y,k+1) in mv) for x,y,k in zip(L.p1,L.p2,L.k)]
out={}
out["events_total"]=len(e); out["linked"]=len(L)
full=e[e.gen>=pd.Timestamp("2026-07-16")]
out["tag24_pct_events"]=round(100*full.tag24.mean(),1); out["tag48_pct_events"]=round(100*full.tag48.mean(),1)
out["tag48_by_cls"]=full.groupby("cls").tag48.mean().mul(100).round(1).to_dict()
last=e[e.k==e.k.max()]; out["latest_tag24_pct"]=round(100*last.tag24.mean(),1); out["latest_tag48_pct"]=round(100*last.tag48.mean(),1)
def st(s): return dict(n=int(len(s)),med=round(float(s.median()),3),p90=round(float(s.quantile(.9)),2),gt1=round(100*float((s>1).mean()),1),gt5=round(100*float((s>5).mean()),1))
F=L[L.fresh]
out["fresh_share"]=round(100*L.fresh.mean(),1)
res={}
for tag in ["tag24","tag48"]:
    for cls in ["all","SL-SL","SL-other","other-other"]:
        sub=F if cls=="all" else F[F.cls==cls]
        res[f"{tag}|{cls}|tagged"]=st(sub[sub[tag]].dmiss); res[f"{tag}|{cls}|untagged"]=st(sub[~sub[tag]].dmiss)
res["mv_between|all|yes"]=st(F[F.mv_between].dmiss); res["mv_between|all|no"]=st(F[~F.mv_between].dmiss)
big=F[F.dmiss>5]; out["share_of_dmiss_gt5_with_mv_between"]=round(100*big.mv_between.mean(),1)
out["share_of_dmiss_gt5_tagged48"]=round(100*big.tag48.mean(),1)
out["share_of_all_fresh_tagged48"]=round(100*F.tag48.mean(),1)
# age control: compare within element-age bins
F=F.assign(age=np.fmax(F.a_age,F.b_age)); F["agebin"]=pd.cut(F.age,[0,.5,1,2,100])
res["by_age"]={str(b):{"tagged":st(g[g.tag48].dmiss),"untagged":st(g[~g.tag48].dmiss)} for b,g in F.groupby("agebin",observed=True)}
out["dmiss"]=res
json.dump(out,open(f"{M}/impact_{FLAG}.json","w"),indent=1,default=str)
print(json.dumps(out,indent=1,default=str))
L[["k","p1","p2","cls","dmiss","fresh","tag24","tag48","mv_between","hmin","lead_h","a_age","b_age","miss_km"]].to_parquet(f"{M}/linked.parquet")
