# ADR-018: CI/CD infrastructure and resource budget — where runners live, what each may use, what runs when

**Date**: 2026-10-05
**Status**: Accepted (merged in PR #764)
**Deciders**: Ricardo Afonso (product owner), Claude (proposer)
**Supersedes**: none. It refines the runner sections of `docs/guides/ci-runners-and-merge-queue.md` and builds on
[ADR-017](adr-017-test-tiers-as-enforced.md) (which tiers exist) by deciding where and when they run.

---

## Context

Every Hermes CI and CD job runs on self-hosted runners in Docker Desktop on the Code Owner's own PC. Over the last
two days the setup was changed piecemeal: ops runner, review runners, a concurrency cap, timeout bumps and runner
moves. On 2026-10-05 the Code Owner said the load is "frying my PC" and asked for one decision instead of more patches.

Measured on 2026-10-05, over the 24 hours to 21:00 UTC: 34 PRs and 42 merges, 1,013 runner minutes in total.

| Job | Runner min/day | Runs | Avg | CPU profile |
| --- | --- | --- | --- | --- |
| `CI success` (PR and main) | 368 | 84 | 4.4 min | Heavy: unit 2.5 min, integration 0.9 min, about 4 cores |
| AI review | 301 | 49 | 6.1 min | Light: mostly waiting on the model, on two 1-CPU runners |
| Merge-queue Revalidate | 104 | 24 | 4.3 min | Heavy: the same suite on the batch tree |
| CD release checks | 64 | 15 | 4.3 min | Heavy: e2e 2.6 min |
| Housekeeping, queue Select, other CD jobs | ~175 | ~300 | <1 min | Light, but each start-up costs a container wake |

Further facts:

- **Peak load is the problem, not average load.** Idle runners use about 50 MB each, about 1.5 GB across all
  containers. One CI run uses about 390% CPU and about 0.5 GB. Four at once take all 16 threads of the Docker VM, which
  is the whole PC.
- **Limits are overcommitted.** The Docker VM has 16 CPUs and 14.6 GB. The Hermes runners' limits add up to about
  24 CPUs and 27 GB. Two runners for another project share the VM and take a further 12 GB of limits.
- **The suite runs three times per merge.** It runs on the PR, again on the merge-queue batch, and again on `main`.
  `main` re-ran it in 21 of 27 pushes, because `scripts/dev/main-tree-validated.sh` only recognises a single-PR tree,
  not a batch tree (#760).
- **No-op runs.** 26 of 37 CD runs and 99 of 128 merge-queue runs did nothing (#694).
- **The ops runner is a single point of failure.** It is shared by the merge queue, CD and, after #694, the release
  gate. It was offline when this ADR was written.
- **Contention caused a red deploy.** CPU starvation failed CD run 37304489094: serve throughput fell from about 25
  to 1–5 bars/s during the e2e run.
- **GitHub concurrency groups hold only one pending run**, and a newer run cancels it. A shared group cannot be used
  to queue work that must not be dropped.
- **GitHub-hosted runners were rejected** by the Code Owner on 2026-10-05 (#754, about $100–150 a month).

## Decision

Each item lists its options. The chosen option is marked **(rec)** and listed below. On 2026-10-05 the Code Owner
chose to build on the PC without a home server for now (D1-A) and to keep the hardware question open (D7). The other
choices are the recommended options. Merging this ADR accepts them.

| Item | Chosen |
| --- | --- |
| D1 Where runners live | A: PC only, tuned. B (home server) stays the target if hardware is bought later |
| D2 Resource budget | A: 2 CI runners × 4 CPU, VM capped at 10 threads / 12 GB |
| D3 What runs when | B: test each tree once |
| D4 AI review trigger | B: after `CI success` passes on the PR head |
| D5 PR concurrency | B: capacity-based |
| D6 Ops resilience | B: standby ops runner, until D9's reserved slot replaces it |
| D7 Home server spec | Open: no purchase yet |
| D8 Adaptive capacity | B: runner scaler |
| D9 Job ranking | B: ranked queue with per-run runners |
| D10 Container engine | A: Docker |
| D11 Isolation | B: separate caches by trust, no Docker socket on PR runners |

### D1. Where the runners live

- **A. PC only, tuned (chosen for now).** Cap the VM and cut runners as in D2. No purchase.
- **B. Home server (rec, deferred).** All Hermes runners move to a small always-on Linux box running Docker and the same
  compose file. The PC keeps a stopped copy of the compose stack as a manual fallback.
- **C. Mix.** CI and ops on a home server; AI review and Dependabot stay on the PC.

Why B: the PC is a workstation that sleeps, gets rebooted and is needed for other work. CD and the merge queue need a
host that is always on. Until the box arrives, A applies.

### D2. Resource budget

Rule: **the memory limits of all containers sum to at most the VM's or host's memory, and the CPU limits of heavy
runners (CI and ops) sum to at most its CPUs.** Light runners (review, Dependabot) may overcommit CPU.

- **A. 2 CI runners × 4 CPU (rec).** Each run stays as fast as today, two run in parallel, and the rest queue.
- **B. 3 CI runners × 2.5 CPU.** More runs in parallel, each about 1.6× slower. The release gate's timing tests are
  more fragile.
- **C. 4 CI runners × 4 CPU.** Today's setup, overcommitted.

Budget under A, on the PC (VM capped at 10 threads and 12 GB with `.wslconfig`) or on a home server:

| Runner | Count | CPU each | Memory each | Notes |
| --- | --- | --- | --- | --- |
| CI | 2 | 4 | 2 GB | measured 0.5 GB at load |
| ops (merge queue, CD, release gate) | 1 | 2 | 2 GB | heavy, but runs one job at a time |
| review | 2 | 1 | 1 GB | light |
| Dependabot runner + DinD | 1 + 1 | 1 + 1 | 0.5 + 1.5 GB | light, weekly |
| **Total** | | **10 heavy** | **10.5 GB** | fits 10 threads / 12 GB |

On a home server with more cores, CI can grow to 3 runners × 4 CPU without changing the rule.

### D3. What runs when: test each tree once

- **A. Today.** The full suite runs on PR, merge queue and `main`; e2e and contract run in CD on source; scheduled
  jobs at a fixed UTC time.
- **B. Once per tree (rec).**
  - PR CI runs the full `CI success` set, plus e2e for code changes (#663).
  - Merge-queue Revalidate runs the same full set on the batch tree and records the tree as validated.
  - CI on `main` skips any tree already validated on a PR head or a batch (#760).
  - CD tests the built wheel once (#670) instead of re-testing source. The CD gate's source e2e stays until the
    wheel e2e is live.
  - Staging, prod and the release record reuse that wheel by checksum (#671–#673).
  - Mutation testing runs only on changed code, with results cached (#675).
  - Scheduled checks run daily at 11:30 Europe/Madrid, plus the Sunday mutation run (#694).
- **C. B, plus nightly-only integration.** Rejected in advance: it moves failures away from the PR that caused them.

### D4. AI review trigger

- **A. Every push (today).** 13 of 49 reviews in the measured day judged code that was then changed.
- **B. After `CI success` passes on the PR head (rec).** A `workflow_run` on CI completes the review. No AI time is
  spent on red code, and reviews stop competing with CI at the same moment.
- **C. B, and skip docs-only PRs.** Saves a little more, but docs can carry rule changes (AGENTS.md, ADRs).

### D5. Governing PR concurrency

- **A. Ad-hoc caps (today).** At most 10 open PRs, at most 2 per writer, and the 2-run CI cap. Each is tuned by hand.
- **B. Capacity-based (rec).** Concurrency follows the runner budget instead of fixed numbers:
  - CI concurrency equals what the runner budget allows: the CI runner count under D2, or the scaler's live
    room under D8-B. Extra runs wait in GitHub's queue instead of starting containers.
  - Open PRs are capped at 4 × CI runners (8 under D2-A). The PR watcher hands out slots in merge-plan order.
  - A daily heavy-minute budget, about 400 runner minutes at today's pace, is reported by the weekly main-health
    routine. Exceeding it two days running triggers a review, not an automatic block.
- **C. Fixed time windows.** Writers push only in set windows. Rejected: it adds waiting without reducing work.

### D6. Ops runner resilience

- **A. Single ops runner (today).**
- **B. Two ops runners, one active (rec).** The second has the same label but its service is stopped. The runbook
  starts it when the first is offline. Merge queue and CD never run two at once, because each job keeps its own
  workflow-level lock (`deploy-vps`, `revalidate-and-merge`).
- **C. Ops jobs fall back to the CI pool when ops is offline.** Rejected: it brings back the contention that failed
  CD run 37304489094.

Under D9-B, the reserved P0/P1 slot replaces the dedicated ops runner. D6-B is the interim until the scaler lands.

### D7. Home server spec, if D1-B

A kit is the server plus a UPS, a switch, Cat6 cables and an install stick. Prices were seen on amazon.es on
2026-10-05.

- **A. Mid: 8-core/16-thread, 32 GB, kit around €675 (rec if bought).** For example a Ryzen 7 5825U mini PC. It carries 2 CI
  runners plus every other Hermes runner, and has memory left for the other project's runners.
- **B. Upper: 8-core/16-thread Zen 4, 32 GB DDR5, kit around €1,110.** The same runner count, with each job faster
  (estimated, not measured).
- **C. Top: 12-core/24-thread, 64 GB, kit around €1,940.** 3 CI runners and room to grow.
- **D. Entry: 4–6 cores, 32 GB, kit around €530–580.** Only 1 CI runner at a time.
- **E. No purchase.** Stay on D1-A.

Memory prices are very high in 2026, so buy a complete unit with its RAM, not a barebone. Check prices before buying.

### D8. Adaptive capacity: runners follow the free resources

The Code Owner asked for one mechanism that adjusts to the resources available, wherever the runners live.

- **A. Fixed pool (D2 only).** A set number of always-on runner containers. Simple, but it ignores what else
  the host is doing.
- **B. Runner scaler (rec).** A small service on the runner host. Every few seconds it reads two things: the jobs
  waiting in GitHub's queue for Hermes, and the host's free CPU and memory. It starts an ephemeral just-in-time
  runner for a waiting job only when the host has room for it. That runner takes one job and is removed afterwards.
  - The D2 budget becomes a ceiling, not a fixed count.
  - A reserve is kept free for the host's owner: 6 threads and 4 GB on the PC, 1 thread and 1 GB on a server. When
    the PC is busy with other work, the scaler stops starting runners. Running jobs are never killed.
  - The same scaler runs on the PC and on a home server. Only the reserve setting changes.
  - Every job starts in a clean container, so no state leaks between runs.
- **C. A Kubernetes runner controller.** Rejected: it needs a cluster, which is too heavy for one box.

### D9. Job ranking and finish-what-you-started

Today GitHub hands each job to any free runner with matching labels. Between two jobs of one workflow, another
workflow can take the runner, which leaves the first run waiting half done.

- **A. Lanes by label only (today).** Ops, CI and review runners are separate, but there is no order inside a lane
  and no protection for a half-finished run.
- **B. Ranked queue with per-run runners (rec).** Requires D8-B.
  - **Per-run label.** Each job asks for a label unique to its workflow run: `runs-on: [self-hosted, hermes,
    run-${{ github.run_id }}]`. Only a runner the scaler started for that run can take it, so no other workflow can
    steal it.
  - **Finish what you started.** A run that has already started a job outranks every run that has not started.
    The run's next job gets the next free slot.
  - **Rank** for new work, highest first:

    | Rank | Work |
    | --- | --- |
    | P0 | Deploy, rollback, prod smoke and bake |
    | P1 | Merge-queue Revalidate |
    | P2 | CI on `main` and the release gate |
    | P3 | PR CI |
    | P4 | AI review |
    | P5 | Scheduled checks and mutation |
    | P6 | Dependabot |

  - **Reserve.** One slot is always kept for P0 and P1, which replaces the dedicated ops runner of D6.
  - **Ageing.** A job that has waited 30 minutes moves up one rank, so low ranks never starve.
  - **No pre-emption.** A higher rank takes the next free slot and never kills a running job.
- **C. Fewer, bigger jobs.** Merge each workflow into one job, so there is no gap between jobs. This helps, but it
  loses parallel jobs and clear check names. It is used only where a workflow's jobs are short.

If the scaler is down, jobs with a per-run label wait in the queue. They do not go to the wrong runner. The weekly
health routine and a scaler health check report this. The runbook's fallback is to start the D2 fixed pool, with
the per-run label turned off by one repository variable.

### D10. Container engine

- **A. Docker (rec).** Docker Desktop on the PC. Plain Docker Engine on a Linux home server, which is free and has
  no desktop VM.
- **B. Podman, rootless.** Its gain is security: no root and no background service. It does not save CPU or memory:
  on Windows it runs in the same kind of WSL VM as Docker Desktop. Running containers inside a container, which
  the Dependabot runner needs, is harder under Podman. Worth revisiting on the home server if rootless isolation
  becomes a requirement.

### D11. Isolation between runners that run PR code and runners that hold secrets

`scripts/dev/gh-runner/compose.yml` on `main` (2026-10-05) gives the ops runner, which holds the deploy SSH key,
Tailscale and the merge-app key, the same named cache volumes as the CI pool: `uv-cache`, `npm-cache` and
`pip-cache` (lines 82–86 and 129–131). The review runner, which holds the review token, shares them too (153–155).
The CI pool, which runs PR code, also mounts the host Docker socket (line 75). That socket lets a job control every
container on the host, the ops runner included. A path from PR code to the deploy job through a poisoned cache is
inferred from the configuration, not proven. Raised by the security review.

- **A. Keep shared caches and the socket (today).**
- **B. Separate by trust (rec).**
  - Three cache sets: one for PR code (CI), one for the ops runner and one for the review runner. No volume is shared
    across them.
  - No Docker socket on any runner that runs PR code. Jobs that need containers use a separate DinD sidecar, as the
    Dependabot runner already does.
  - The D8 scaler applies the same rule to every runner it starts.
- **C. B, plus caches that PR jobs can only read.** Only `main` CI writes the CI cache. Stronger, but runs slower when
  dependencies change.

## Rollout (one CI/runner PR at a time, through the CI line)

| Step | What | Who | Status |
| --- | --- | --- | --- |
| 0 | Cap the Docker VM (`.wslconfig`: 10 threads, 12 GB), scale CI to D2's count | Code Owner, PowerShell | stopgap; the 2-run CI cap stays until step 4 |
| 1 | This ADR, docs-only PR | Claude | after the decisions |
| 2 | #694: no-op runs, release gate on the ops runner, daily checks at 11:30 Madrid. It already resolves the `cd.yml` conflict with merged PR #737 | CI writer | built, held |
| 3 | #686 and #685: bake the review tools into the image, pin the review model. Runner rebuild | CI writer, then Code Owner rebuilds | built, held |
| 4 | Isolation per D11 (first), compose budget per D2, AI review on `CI success` per D4, PR concurrency per D5 | CI writer | new |
| 4b | Runner scaler per D8, then ranked queue and per-run labels per D9, one workflow at a time; fixed pool stays as fallback | CI writer, then Code Owner installs the scaler | new, after this ADR |
| 5 | #760: Revalidate runs the full set and `main` skips validated batch trees | CI writer | new |
| 6 | #663, then #670: e2e in PR CI, the wheel-based release gate, then drop the CD source e2e | GitHub config, then CI writer | approved |
| 7 | #671, #672, #673: staging, bake with rollback, release record | CI writer | approved |
| 8 | #756: wire the weak-test lint into CI | CI writer | approved |
| 9 | #675, #676, #674, #677: mutation on changed code, risk labels and gate-weakening check, Python pin, daily seed run | CI writer | approved |
| 10 | Move the runners to the home server, if D1-B, and keep a stopped PC copy as fallback | Code Owner, with a runbook PR | after purchase |

Issue #759 (warnings from unclosed SQLite connections) is test-only and goes to the Certification thread, outside the CI line.

## Why

- One budget rule replaces a growing list of hand-tuned caps, and the scaler applies it to the resources that are
  actually free.
- A ranked queue with per-run runners ends half-finished workflows waiting behind newer ones.
- Testing each tree once removes about a third of the heavy minutes without weakening any gate. Every gate still
  runs on every change it judges.
- An always-on host fixes both the load on the PC and the CD and merge-queue outages when the PC sleeps.

## Consequences

### Positive

- The PC keeps at least 6 threads and about half its RAM free, even at peak, before any purchase.
- Heavy minutes per merge fall from about 13 to an estimated 8 to 9, once steps 2, 4 and 5 land.
- One place answers "why is this runner sized like this".

### Negative

- The scaler is new code on the critical path. If it fails, jobs wait, so it needs a health check and a
  documented fallback to the fixed pool.
- With 2 CI runners, bursts of PRs queue longer.
- AI review starts only after CI is green, so a PR is ready for approval a few minutes later.
- A home server is one more machine to patch and back up.

### Neutral

- No test tier, coverage floor or gate changes. ADR-017's tier table stays the source of truth for what runs.

## What would change this decision

- Sustained heavy load over the daily budget after steps 2–5: revisit D2 or D7.
- The Code Owner reverses #754 and accepts paid hosted minutes: a new ADR.
- A second always-on machine becomes available for other reasons: revisit D1 and D6.

## Cross-references

- [ADR-017](adr-017-test-tiers-as-enforced.md): which tiers exist.
- [`docs/guides/ci-runners-and-merge-queue.md`](../guides/ci-runners-and-merge-queue.md): the runner guide this ADR
  governs.
- Issues: #694, #686, #685, #760, #663, #670–#677, #756, #759, #754 (hosted runners, rejected).
