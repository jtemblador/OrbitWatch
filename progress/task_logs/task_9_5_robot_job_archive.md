# Task 9.5 — Robot job (scheduled cron) + snapshot archive

**Date:** Jul 7, 2026
**Status:** DONE (code) — cron *activates* once the workflow is on `main`; real-CI
firing is confirmed post-merge (see Validation).
**Tests:** no pytest surface (a CI workflow file). Verified by local git simulation
of the exact archive logic + two adversarial review rounds. 563 passing unchanged.

---

## Goal

Make the deployed site **refresh itself** and **keep a record of what it published**.
A scheduled GitHub Actions job rebuilds the snapshot a few×/day (so upstream —
CelesTrak — is hit on a schedule, never per visit), and every rebuild archives the
fresh snapshot to a durable, append-only history. Closes `scaling_tracker #2` for
prod; backs the 9.10 prediction-evolution view.

---

## Approach + key decisions

9.5 is pure CI/CD plumbing on the seam 9.4 already built — **no screening code, no
`.so` change, no snapshot-format change.** Everything lives in
`.github/workflows/deploy.yml`.

- **Scheduled REBUILD, 3×/day.** A `schedule:` cron forces the existing REBUILD
  path (`github.event_name == 'schedule'` ⇒ `rebuild=true` in the *Decide* step).
  The times **trail CelesTrak's SOCRATES runs** — SOCRATES screens the *same* GP
  catalog we do, 3×/day, so its upload is a proxy for "the elements just
  refreshed." Slots at **05:17 / 13:17 / 21:17 UTC**; 13:17 trails the historically
  documented ~12:10 midday run by ~1 h. **`:17`, not `:00`** — GitHub's docs flag
  the top-of-hour as the most-congested/most-delayed minute for scheduled jobs.
  A **"Log SOCRATES run time" step** curls the SOCRATES CSV's `Last-Modified`
  each rebuild so the slots can be tuned against SOCRATES's *actual* observed
  upload times (its current exact times aren't publicly documented and CelesTrak
  was VPN-blocked locally — but the runner can read them).
- **Append-only archive on an ORPHAN `data` branch.** Each rebuild gzips
  `frontend/snapshot.json` → `snapshots/<ISO-ts>.json.gz` and pushes it to a
  `data` branch that shares **no history with `main`** — so the growing archive
  never bloats the code branch. The `data` branch is never served (Pages serves
  `frontend/` from the artifact) and never merged to `main`. Timestamp comes from
  the snapshot's own `meta.generated_at`, colons → dashes (Windows-safe filename).
- **Worktree isolation.** The archive runs in a linked `git worktree` under
  `$RUNNER_TEMP`, so the main checkout's `frontend/` (uploaded to Pages in the
  same job) is untouched.
- **The archive must NOT block the deploy.** The archive is a *secondary* record;
  the *primary* goal is refreshing the live site. So the step is
  **`continue-on-error: true`** + a follow-up step that emits a visible
  `::warning::` on failure. A transient `data`-push hiccup lets the good snapshot
  ship anyway and retries next run — instead of freezing the site.
- **Least privilege.** Top-level `permissions: contents: read`; only the `build`
  job overrides to `contents: write` (for the archive push). The `deploy` job
  inherits read-only (it only needs `pages`/`id-token`).

---

## Implementation

Single file: `.github/workflows/deploy.yml` (extends the 9.4 deploy).

| Addition | Detail |
|----------|--------|
| `schedule:` trigger | 3 crons (05:17/13:17/21:17 UTC) |
| *Decide* step | `event_name == 'schedule'` ⇒ force `rebuild=true` |
| `permissions` | top-level `contents: read`; `build`-job override `contents: write` |
| "Log SOCRATES run time" step | curls SOCRATES `Last-Modified` vs. run start (rebuild-gated, non-blocking) |
| "Archive snapshot to the data branch" step | orphan-or-extend `data`, gzip → `snapshots/<ts>.json.gz`, commit, push; `continue-on-error` |
| "Warn if the snapshot archive failed" step | `::warning::` on `steps.archive.outcome == 'failure'` |

**Archive branch logic (the careful part):**
```
err=$(git ls-remote --exit-code --heads origin data 2>&1 >/dev/null); rc=$?
rc == 0  → git fetch --depth=1 origin data; git worktree add -B data "$WT" FETCH_HEAD   # extend
rc == 2  → git worktree add --detach "$WT"; git -C "$WT" checkout --orphan data; rm -rf  # first run
else     → ::error:: + exit 1                                                            # transient error: abort
```

---

## Validation

**Local git simulation of the *exact* archive logic** (against a bare "remote",
using a `checkout@v4`-style narrow refspec to reproduce CI):
- **First run** → orphan `data` created; main worktree keeps `frontend/`.
- **Second + third run** → `FETCH_HEAD` path appends; end state is 3 files,
  3 commits, **1 root commit = true orphan** (no merge-base with `main`).
- **Transient error** (unreachable remote, `rc=128`) → **aborts**, does not
  fabricate a bogus orphan.
- **stderr idiom** `err=$(… 2>&1 >/dev/null)` → empty on rc 0/2, git's real
  message on error (so the abort annotation is diagnostic).
- **YAML** parses; per-job permissions resolve; `continue-on-error`/`outcome`
  wiring correct.

**Real CI (post-merge, the one thing local can't cover):** trigger a
`workflow_dispatch` with `rebuild_snapshot=true` — this runs the full
rebuild→archive→deploy, **creating the orphan `data` branch on its first run**,
and confirms the `GITHUB_TOKEN` push works. The scheduled firing itself is
confirmed by checking the Actions tab after the first 05:17/13:17/21:17 slot.

---

## Two adversarial review rounds — 6 findings

**Round 1 (4, all fixed):** (1) treating *any* `ls-remote` failure as "first run"
→ rc-branch (0/2/error); (2) **latent 2nd-run crash** — `git worktree add … origin/data`
relied on a tracking ref that `checkout@v4`'s narrow refspec never creates
(**reproduced locally**) → use `FETCH_HEAD`; (3) `contents: write` was
workflow-wide → per-job override; (4) crons on `:00` (GitHub's worst-delay
minute) → `:17`.

**Round 2 (2, both fixed):** (1) **HIGH — the archive was blocking the deploy**:
a transient push failure would fail the whole job and a *good* snapshot would
never reach the live site → `continue-on-error` + visible warning; (2) the
`ls-remote` abort path discarded git's error message → capture stderr.

Round 2 also **re-verified all 4 round-1 fixes against git/GitHub's documented
semantics with no fallout.**

---

## Lessons learned (durable — mirrored to key_information.md)

- **`git fetch origin <branch>` does NOT create `refs/remotes/origin/<branch>`
  under a narrow refspec** (what `actions/checkout@v4` leaves). Base a worktree on
  **`FETCH_HEAD`**, not `origin/<branch>`. This bug is invisible to a single smoke
  test — it only bites on the *second* run, when the branch first exists.
- **`git ls-remote --exit-code`: 0 = found, 2 = query OK but absent, else = real
  error.** Don't collapse "absent" and "error" — the error path must abort, or a
  transient blip fabricates an orphan and the no-force push is rejected.
- **A `continue-on-error` step's failure is masked** (`conclusion: success`); gate
  a follow-up on `steps.<id>.outcome == 'failure'` to surface it as a `::warning::`.
- **A secondary/archival step must never gate the primary deliverable.** Decouple
  with `continue-on-error`, not step ordering (a failed job still skips `deploy`
  regardless of order).
- **Job-level `permissions` REPLACE the top-level set** (don't merge) — re-list
  every permission the job's actions need.
- **Scheduled workflows: only fire from the *default branch*; UTC + best-effort
  (avoid `:00`); auto-disabled after 60 days of no repo commits (silent).** A
  `GITHUB_TOKEN` push to a non-default branch does not re-trigger the workflow.
- **A detached worktree + `git checkout --orphan` is the version-portable way to
  create an orphan branch** without disturbing the main checkout (`git worktree
  add --orphan` only exists on git ≥ 2.42; the runner has it, local 2.39 didn't).

---

## Deferred / open

- **`data`-branch archive growth** — each run does a full worktree checkout of the
  current archive (bounded by `--depth=1` fetch; ~1 MB/day at 3×/day → tens of MB
  over the project's active life, nowhere near GitHub's ~1 GB soft repo limit).
  Retention (prune/squash) or a sparse checkout is a trivial later add if it ever
  matters — not needed now.
- **60-day scheduled-workflow auto-disable** — genuinely silent. Fine while
  actively committing; post-graduation, accept it (the "updated X ago" freshness
  line keeps staleness honest), click "Run workflow" monthly, or add a keepalive.
- **Full ~16k screen** still capped at 5000 (Phase-10 path filter / self-hosted
  runner are the levers) — unchanged from 9.4.
