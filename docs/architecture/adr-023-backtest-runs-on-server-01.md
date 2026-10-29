# ADR-023: Backtests run on server-01 — one runner, pinned data that fills itself, and a Code Owner click for every Validation touch

**Date**: 2026-10-17
**Status**: Accepted (merged in PR #943)
**Deciders**: Maintainer
**Supersedes**: none.
**Superseded in part by**: [ADR-026](adr-026-research-batch-and-signed-champion-rule.md) (§5, a Validation run waits for the Code Owner's click, and §9, `workflow_dispatch` is the only trigger, for strategies signed in a batch)
**Amends**: [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md), adding a backtest runner to its D2 runner
table. ADR-018 is not edited; this ADR is where the row lives.

---

## Context

The backtest gate of 8 October (issue #735) ran once by hand on the Code Owner's PC, following
`docs/experiments/swing-mvp1/` and the gate runbook: repo `.venv`, data on `D:/hermes-data`, window 2020-09-01 to
2025-08-31, one Validation touch the Code Owner had approved in writing. The runbook ended with "future backtest and
Validation runs move off the PC" (Code Owner, 7 October).

Running backtests by hand has three costs:

- **It loads the PC.** ADR-018 exists because CI on the PC was "frying" it. A five-year, three-pair portfolio replay
  is heavier than a CI run and holds its cores for much longer.
- **Nothing checks the steps.** Which data was read, which trial log was appended and whether the Validation touch
  was approved are all the operator's memory and a PowerShell history.
- **The trial log lives on one laptop.** Every run counts as a trial toward the Deflated Sharpe Ratio
  (pre-registration v1 §8.1, ADR-012 D-11), but the experiment log was never committed, so the trial count depends
  on one machine's disk.

PR #926 added `scripts/data/prepare_backtest_data.py`, which downloads only the missing monthly klines of a split and
verifies every pinned dataset through the backtest loader's own read path. That makes an unattended run possible.

server-01 is an always-on Linux box the Code Owner now has. It has two NVMe drives: a P310 and an SN850X. The SN850X is
reserved for a separate infrastructure project.

The Code Owner approved the plan on 2026-10-17.

## Decision

### 1. Where backtests run

Every backtest and every Validation run runs on server-01, on the self-hosted runner labelled `hermes-backtest`, from
`.github/workflows/backtest.yml`. They never run on the VPS, which serves production, and never on a laptop or the
PC. The runner is the `backtest-runner` service of `scripts/dev/gh-runner/compose.yml`, behind the compose profile
`backtest`, so the PC's `./up.sh` never starts it.

### 2. Where the data lives

The klines archive lives at `/srv/hermes-data` on server-01's P310 drive. The SN850X stays with the infrastructure
project. The runner bind-mounts the directory at the same path and sets `HERMES_DATA_DIR=/srv/hermes-data`.

### 3. Every run fills and verifies its own data

Before replaying anything, each run calls `prepare_backtest_data.py --split <split>`. It downloads only what is
missing, then checks every pinned dataset of the split against its manifest hash. A missing file that cannot be
downloaded, or any hash mismatch, fails the run before a trial is logged. Nobody pre-loads the drive by hand, and a
corrupted or tampered file cannot reach a trial.

### 4. Nothing from the sealed OOS window is ever fetched

The split input is `train` or `validation`; there is no `oos` choice. The window comes from the data manifest, not
from free inputs:

| Split | Window | Counts as |
| --- | --- | --- |
| `train` | Train start to Train end (2020-09-01 to 2024-06-30) | a Train trial |
| `validation` | Train start to Validation end (2020-09-01 to 2025-08-31), as the 8 October gate ran | a Validation touch |

The workflow refuses any window that reaches the OOS start. Below it, `prepare_backtest_data.py` refuses to build any
OOS target, and the backtest loader refuses OOS bars for an `UNTRUSTED` run. Three independent layers would each have
to fail before the first OOS bar is read.

### 5. A Validation run waits for the Code Owner

A `validation` run uses the GitHub environment `backtest-validation`. GitHub offers required reviewers on private
repositories only under Enterprise, so the Code Owner's OK is that they start the run themselves: one Run workflow click
from their account, one logged Validation touch. Agents act on GitHub as the bot account, so they cannot start one.

- The environment accepts only `main`, and the workflow refuses any run, Train or Validation, from another ref:
  every run executes that code on server-01 and logs a trial.
- Before reading data, the run stops unless both `github.actor` and `github.triggering_actor` are the Code Owner,
  so a bot dispatch, or a bot re-run of a run they started, fails closed.
- A `train` run uses the environment `backtest-train`, which needs no click but accepts only `main`.
- Each Validation touch counts against the family budget of [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) §5
  from v2 on. For v1, pre-registration §4 ("touch sparingly") applies.

### 6. Trial lines and reports leave as artifacts; a PR commits them

The workflow holds no write token: `permissions: contents: read`, and `persist-credentials: false` on checkout.

- The run copies the committed log `docs/experiments/<exp>/trial-log.jsonl` to a temporary file and the driver appends
  to that copy. A run refuses to start when the committed log is missing, because every earlier trial must count.
- The run uploads, as one artifact kept 90 days: the report JSON, the performance file, the evidence file, the full
  `hermes certify` output, the `trial_stats.py` output and the trial line or lines the run appended.
- A follow-up PR appends those lines to the committed log. The log is hash-chained, so the PR adds them byte for byte
  and in order.
- **One run at a time, end to end.** A run's lines are committed before the next run is dispatched. A run started
  from a log that lacks an earlier run's line chains onto the wrong parent, and the two lines can no longer both be
  committed. The chain makes that mistake visible; it does not prevent it.
- `hermes certify` runs against a temporary store, so a workflow run never records a certification. Recording
  `BACKTEST_CERTIFIED` stays a Code Owner step on the VPS, as the gate runbook describes. Exit 1 (gate failed) is a
  valid outcome and does not fail the job; any other non-zero exit does.

### 7. Outcomes stay out of AI inputs

The job summary shows only the split, window, commit, trial counts and each certify check as PASS or FAIL. Observed
metrics are only in the artifact. The Code Owner reads the numbers there. AI sessions never download these
artifacts, never read these runs' logs beyond pass or fail, and never put a number from them into a prompt or memory
(ADR-012 D-06 and D-11).

### 8. Resource budget

| Runner | Count | CPU | Memory | Notes |
| --- | --- | --- | --- | --- |
| backtest (server-01) | 1 | 4 | 8 GB | never concurrent with itself |

- One replica, `cpus: 4`, `mem_limit: 8g`. The ADR-018 D2 rule applies to server-01 on its own: the limits of every
  container on it sum to at most its CPUs and memory.
- The workflow's `concurrency: {group: backtest, cancel-in-progress: false}` keeps a second run waiting while one runs.
  As ADR-018 notes, a group holds only one pending run, so a third dispatch replaces the pending one. A replaced run
  never started, so it logged no trial.
- The job times out at 120 minutes.
- The runner has its own uv cache volume, shared with no other runner (ADR-018 D11). It does not mount the Docker
  socket.
- The backtest runner stays outside the ADR-018 D8 scaler and D9 per-run labels while it is the only runner on
  server-01. A fixed label is what ties a run to the host that holds the data.

### 9. Who dispatches

`workflow_dispatch` is the only trigger. A Validation run happens only when the Code Owner starts it themselves (§5). A Train run
is a trial too, so agents dispatch one only when the Code Owner asks for it.

## Security (ADR-019)

- **No secret.** The data is public Binance Vision archives, so the workflow reads no secret. Its token is read-only.
- **Branch code on the runner.** The workflow refuses any ref but `main`, but a branch could carry an edited
  workflow without that check. Both environments, `backtest-train` and `backtest-validation`, therefore limit
  deployment branches to `main`, which GitHub enforces outside the workflow file. The runner holds the same
  registration PAT as every other runner and no deploy or exchange credential. It can write `/srv/hermes-data`, but a
  poisoned file fails the next run's hash check. This matches ADR-019 threat T3's existing controls (ephemeral
  runner, least-privilege token). No threat row changes.
- **Runs from `main` only**, for both splits; Validation is also held by the environment's branch rule.

## Why

- server-01 is always on and is not the Code Owner's workstation, which is what ADR-018 D1-B wanted for heavy jobs.
- Self-filling data with hash checks removes the manual "is the drive loaded" step without trusting the drive.
- The Code Owner's own dispatch turns the written Validation OK into a per-run record GitHub keeps.
- Artifacts and a follow-up PR keep the trial log reviewed and in the repo, without giving the workflow a write token.

Alternatives considered:

- **Run on the VPS.** Rejected: it serves production, and a long replay would compete with serve.
- **Keep running on the PC.** Rejected: the load ADR-018 set out to remove, and no recorded approval.
- **Commit the trial line from the workflow.** Rejected: it needs a write token in a job that may run branch code
  (ADR-019 rule 4).
- **Free `start`/`end` inputs.** Rejected: the manifest already defines the windows, and free dates are one more way
  to touch Validation without meaning to.

## Consequences

### Positive

- Backtests stop loading the PC.
- Every run records which data, which commit and, for Validation, who started it.
- The trial count lives in the repo, not on one laptop.

### Negative

- Committing trial lines is a manual PR after each run, and runs are serial end to end.
- server-01 becomes one more runner host to patch and keep online.
- The first run waits until the experiment trial log is committed.

### Neutral

- No metric, threshold, window or pre-registration rule changes.

## What would change this decision

- A second backtest host: revisit §8, and whether the ADR-018 D8 scaler should own the label.
- A trial-log store the workflow can append to without a repo write token: revisit §6.
- A pre-registration that adds warm-up-only data before Train: the manifest, not this ADR, changes the window.

## Cross-references

- [ADR-012](adr-012-continuous-ai-evaluation.md) — D-06 blindness, D-11 trial ledger
- [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md) — runner budget and isolation, amended here
- [ADR-019](adr-019-security-baseline-and-threat-model.md) — security baseline
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — the backtest gate and the Validation budget
- [Pre-registration v1](../experiments/swing-mvp1/pre-registration-v1.md) §4, §7, §8.1
- `scripts/data/prepare_backtest_data.py`, `scripts/backtest/run_portfolio_backtest.py`, `hermes certify`
- Issues: #735 backtest gate, #925 self-filling data

## Pending

- Commit the experiment trial log to `docs/experiments/swing-mvp1/trial-log.jsonl` before the first run.
- A check that refuses a run while an earlier run's trial lines are not yet committed.
