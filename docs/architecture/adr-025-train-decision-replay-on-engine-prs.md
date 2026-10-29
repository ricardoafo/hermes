# ADR-025: Engine PRs replay Train and compare decisions, on the CI pool with the klines mounted read-only

**Date**: 2026-10-17
**Status**: Accepted (merged in PR #988)
**Deciders**: Maintainer
**Supersedes**: none.
**Amends**: [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md), adding a read-only data mount to the CI
runners, and sits beside [ADR-023](adr-023-backtest-runs-on-server-01.md), which this ADR does not change.

---

## Context

ADR-021 restarts a strategy's shadow clock when an engine change alters one of its decisions. Until now the only check
was by hand: issue #707 replayed Train on 2026-10-05 before and after a kernel fix and compared the signals. Issue #798
asks CI to do it on every engine PR, over Train only, reporting the count of signal and fill decisions that changed.

ADR-023 runs backtests on server-01's `hermes-backtest` runner, from `main` only, because every run there executes the
ref's code next to the writable klines archive and logs a trial. A PR replay runs PR code, so it cannot go there. The
Code Owner decided on 2026-10-17 that the replay reads `/srv/hermes-data` on server-01.

## Decision

### 1. What runs

`scripts/dev/train-decision-replay.sh`, a step of the single `CI success` job (ADR-018: test the tree once, no second
wave), on `pull_request` events once the unit tier has passed. It replays only when the changeset touches an engine
file: `src/hermes/domain/`, `src/hermes/application/`, `src/hermes/adapters/fills/`, `src/hermes/adapters/fees/`,
`config/strategies.yaml`, an experiment's `fee-history.csv` or `data-manifest.json`, or `uv.lock`. Any other change
cannot alter a decision and passes without a replay.

### 2. What it compares

`scripts/backtest/decision_replay.py record` runs once on the PR's base tree, from a worktree with its own synced
environment, and once on the tree CI checked out. Each tree runs its own engine and its own copy of the script. Both
replay the enabled roster strategy over the manifest's Train split, through the same bars, kernel and execution model
as `run_portfolio_backtest.py`. A decision is a signal's time, pair, kind and side, and whether it filled. `diff`
reports a signal on one side only, or one whose fill outcome differs; a different refusing rule with the same outcome
is not a change, as in `hermes parity check`.

### 3. What it never does

- It never logs a trial. Nothing is priced, sized, measured or ranked, so no strategy is evaluated (pre-registration
  v1 §8.1 counts evaluations).
- It never reads Validation or OOS. The window is Train, and the loader refuses OOS for an `UNTRUSTED` run.
- It never prints a price, size, equity or metric. The record file holds decision fields only, and the kernel's own
  logs are silenced below critical for the run.

### 4. When decisions change

The step adds the `decisions-changed` label and fails, unless the PR body holds the line
`Decision changes approved: <link to a comment on this PR>`, that comment was written by the Code Owner, and it quotes
the changes fingerprint the step printed: a hash of the full list of changed decisions. An approval therefore covers
exactly the changes it was given for. A later push that changes a different set prints a new fingerprint and needs a
new comment; a push that leaves the set alone, such as a main merge, keeps the approval. The step reads the body from
the API, so editing it and re-running the job is enough. Zero changes passes and removes the label.

### 5. Where the data comes from

The CI runners bind-mount `/srv/hermes-data` read-only at the same path. The backtest runner alone fills it
(ADR-023 §3), and every replay re-verifies each file against the manifest hashes, so a PR job can read the klines but
cannot change one. On a host without the directory the mount creates an empty one (`create_host_path`), so the
runner still starts and the step fails saying the Train data is missing.

`hermes parity check` on the VPS stays the check for the shadow days themselves: it compares serve's decisions with a
replay of the same bars, decisions only, and no AI reads its output.

## Security (ADR-019)

- **No secret.** The step uses the job token it already has (`pull-requests: write`, for the label) and reads public
  klines.
- **PR code next to the data.** A CI runner already runs PR code and reaches the host Docker socket (ADR-018), so the
  read-only mount stops accidents, not a hostile PR. A tampered kline still fails the next run's hash check, on this
  pool and on the backtest runner. No threat row changes.
- **The approval cannot be forged by the bot or go stale.** The cited comment must be the Code Owner's own, on this
  PR, and quote the fingerprint of the current changes.

## Why

- It runs inside the existing CI job, so it adds no runner, queue wave or redundant run.
- Train replays take seconds per tree, far inside the job's budget.
- Decisions, not metrics, keep the run off the trial count and keep the CI log blind.

Alternatives considered:

- **Run on `hermes-backtest`.** Rejected: ADR-023 keeps branch code off that runner and its writable archive.
- **Download the klines in each job.** Rejected: the Code Owner chose the server-01 archive, and a cold download per
  PR is slower than a mount.
- **Compare fill prices too.** Rejected: price drift is #353's job, and prices in a CI log break the blindness rule.

## Consequences

### Positive

- An engine change that alters a decision cannot merge unnoticed.
- The #707 hand check becomes a gate.

### Negative

- Engine PRs fail until the CI runners are recreated with the mount.
- A changed decision needs one comment from the Code Owner and a body edit.

### Neutral

- No metric, threshold, window or pre-registration rule changes.

## What would change this decision

- A second strategy in the roster: the replay loops the roster instead of the single enabled entry.
- A CI pool off server-01: the data mount moves with it, or the step downloads into a cache.

## Cross-references

- [ADR-018](adr-018-ci-cd-infrastructure-and-resource-budget.md) — the single CI job and runner isolation
- [ADR-019](adr-019-security-baseline-and-threat-model.md) — security baseline
- [ADR-021](adr-021-strategy-lifecycle-ready-and-done.md) — engine changes and the shadow clock
- [ADR-023](adr-023-backtest-runs-on-server-01.md) — the backtest runner and the klines archive
- `scripts/backtest/decision_replay.py`, `scripts/dev/train-decision-replay.sh`,
  `scripts/dev/decision_change_approval.py`, `hermes parity check`
- Issues: #707 the hand check, #798 this gate
