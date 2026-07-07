# Task 9.4 — Deploy the static site to GitHub Pages

**Date:** Jul 7, 2026
**Status:** DONE — **live at https://jtemblador.github.io/OrbitWatch/**
**Tests:** none new (deploy infra + frontend); verified by the live run + curl checks.

---

## Goal

Publish the snapshot-driven static site (9.2/9.3) to a public URL that makes
**zero calls to our backend or CelesTrak per visit**, with the Cesium Ion token
**domain-restricted** so a public page can't leak quota. The heavy conjunction
screen runs offline in CI; the browser propagates every satellite client-side
from one cached `snapshot.json`.

---

## Approach + key decisions

- **GitHub Actions Pages deploy, not branch-serve.** Pages needs a
  `snapshot.json` to serve, and the only *real* one comes from running our C++
  screen — which is VPN-blocked from CelesTrak locally but reachable from a
  GitHub runner. So the deploy is a workflow that builds the `.so`, fetches,
  screens, and publishes. This is also the exact mechanism 9.5's cron extends.
- **Ion token injected from a repo secret**, never committed. `config.js` stays
  gitignored; the workflow writes it from `${{ secrets.CESIUM_ION_TOKEN }}` at
  build time (via env, not string-interpolation, so a token with shell
  metacharacters can't break out). A new token restricted to
  `https://jtemblador.github.io` replaced the old unrestricted one.
  **Gotcha:** the restriction must be the bare **origin**, not
  `.../OrbitWatch` — browsers send only the origin as the cross-origin
  `Referer`, so a path-scoped token 401s. (In practice Ion is never even hit:
  the base layer is CartoDB, terrain is off — the token is set but no Ion asset
  is requested.)
- **Relative asset paths** (done in 9.3) are mandatory — a project site serves
  under `/OrbitWatch/`, so absolute `/js/app.js` 404s.
- **CI capacity — the real constraint.** The full active catalog is **~16k**
  objects (15,913 fetched, ~12k screenable). The O(N²) screen over that is the
  heaviest op in the project and ran **20+ min at 24 h / 38+ min at 72 h** on a
  shared 4-vCPU runner — impractical for a job that repeats (and reruns a
  few×/day in 9.5). **Decision: cap at `--max-sats 5000 --hours 24`**, which
  keeps **display == screen** so the "fully screened" claim stays honest
  (complete over what it shows). Lifting the cap is the **Phase-10 geometric
  path filter** (free, algorithmic) or a self-hosted runner — not spending
  compute credit to brute-force an O(N²) screen.
- **Reuse-vs-rebuild split** (added after the first deploys): re-screening for a
  CSS tweak is wasteful, so the workflow now defaults to **REUSE** — download the
  live `snapshot.json` and redeploy (~1 min, no C++/screen). **REBUILD** (~8 min)
  is explicit: `workflow_dispatch` with `rebuild_snapshot=true`, or when the
  reuse curl fails (first deploy). 9.5 adds the `schedule:` cron that rebuilds.
  The push trigger is narrowed to `frontend/**` + the workflow file.

---

## Implementation

| File | Change |
|------|--------|
| `.github/workflows/deploy.yml` | **new** — build job (Decide reuse/rebuild → optional Python+`.so`+screen → inject token → configure/upload Pages) + deploy job; `workflow_dispatch` `rebuild_snapshot` input; push on `frontend/**` |
| `frontend/index.html` | + inline-SVG 🛰️ favicon (kills the 404) |
| `frontend/js/config.example.js` | doc note: CI injects the token; local dev needs a `localhost`-allowed token |
| `requirements.txt` | + `pyarrow` (the Parquet cache engine — was only present in the local venv, so the CI snapshot build failed on `df.to_parquet` until added) |
| `.gitignore` | + `frontend/snapshot.json` (generated; never committed — CI bakes it into the Pages artifact) |

---

## The debugging saga (what actually happened, in order)

1. **C++ build passed first try** — `pybind11` from pip + `cmake -Dpybind11_DIR=$(python -m pybind11 --cmakedir)` + `cp orbitcore*.so backend/`. (The biggest risk, cleared immediately.)
2. **CelesTrak fetch worked from the runner** — 15,913 active objects (confirms the 8.3 TLS block was purely the local VPN).
3. **Failure: `pyarrow` missing** → `df.to_parquet` raised. Fixed in `requirements.txt`.
4. **Too slow: 72 h screen ~13 min, then full 24 h screen 20+ min, 38+ min at 72 h** → capped at 5000 sats / 24 h.
5. **Green.** Both jobs succeeded; Pages published.
6. **Reuse optimization** added so subsequent frontend pushes deploy in ~1 min.

The snapshot path is **SPICE-kernel-free** in practice (uses `teme_to_rtn`,
pure numpy — verified by running the build with the kernels hidden), so CI needs
no SPICE kernels.

---

## Validation (on the deployed artifact, via curl — Playwright MCP was down)

- **Live run numbers:** 5,000 sats (4,821 screened LEO+GEO, 179 MEO/HEO
  display-only), **367 conjunctions in 6.3 min**, snapshot **2.42 MB raw /
  0.32 MB gzipped**.
- **Zero backend/CelesTrak calls on a visit** — the only network calls in the
  served JS are `fetch("snapshot.json")` (relative), the local
  `propagation-worker.js`, and the pinned satellite.js CDN; every `/api/` string
  is a doc-comment, confirmed line by line.
- Relative paths resolve under `/OrbitWatch/`; real 281-char Ion JWT injected;
  favicon present; `snapshot.json` valid (5000 sats, 367 conj, SFS 24 h).
- **User confirmed the globe renders** in a real browser ("it looks good").

---

## Lessons learned

- **`requirements.txt` drift bites in CI, not locally.** `pyarrow` (and the whole
  Parquet cache path) worked locally purely because the venv had it. CI is the
  honest environment.
- **Full-catalog screening is a CI-cost problem, and the roadmap already named
  the fix.** 20-38 min on a shared runner is why Phase 10's path filter exists;
  capping to 5000 is the interim, and "trim the set" is the roadmap's sanctioned
  fallback rung 1.
- **A domain-restricted client token is safe to expose but must be scoped to the
  origin.** Path-scoping silently breaks it (referrer policy).
- **Separate "deploy the site" from "rebuild the data."** Gating the C++/screen
  steps behind a rebuild flag turns a CSS change from an 8-min job into a 1-min
  one — and it's the same seam 9.5's cron plugs into.

---

## Deferred / next

- **9.5** — the `schedule:` cron (rebuild a few×/day) + the compressed snapshot
  archive to a `data` branch. The reuse/rebuild seam is already in place.
- **Full ~16k screen** — Phase 10 path filter (the real lever) or a self-hosted
  runner cron (uses the DigitalOcean $200 student credit; not as a public-repo
  Actions runner — security).
- **Real Safari/iOS check** — the `_isoToEcma` fix (9.3) follows documented
  WebKit behavior but wasn't checked on a device.
