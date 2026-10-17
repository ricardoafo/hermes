# Change: 798-train-decision-replay

**SDD change**: `798-train-decision-replay`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/798-train-decision-replay/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

ADR-021 restarts a strategy's shadow clock when an engine change alters one of its decisions.
Issue #798 turns the hand check of issue #707 into CI: every engine PR replays the roster over
Train on its base and on its head and reports the signal and fill decisions that changed. Zero
changes passes; any change fails with a label unless the PR body cites the Code Owner's OK. The
replay reads the klines from `/srv/hermes-data` on server-01 (Code Owner, 2026-10-17), mounted
read-only on the CI pool. ADR-025 records the design.

## Scope

### In

- `scripts/backtest/decision_replay.py`: `record` (Train replay, decisions only, no trial) and
  `diff` (changed decisions and their count).
- `scripts/dev/train-decision-replay.sh`: engine changeset, base worktree, both records, diff,
  label, approval check.
- `scripts/dev/decision_change_approval.py`: the PR body cites a comment of the Code Owner on the PR.
- A `Train decision replay` step in the `CI success` job, pull requests only, after the unit tier.
- The CI runners mount `/srv/hermes-data` read-only; the `decisions-changed` label in
  `setup-labels.sh`.
- ADR-025.

### Out

- More than one roster strategy (the roster holds one enabled entry today).
- Price or size comparison (#353 owns price drift).
- `hermes parity check` on the VPS, which already covers the shadow days.

## Decisions

- **D-1 Decisions, not metrics.** A decision is a signal's time, pair, kind and side, and whether
  it filled. No price, size, equity or metric is written, printed or compared, so the run
  evaluates nothing and logs no trial.
- **D-2 Same replay as the backtest.** `record` builds the kernel through
  `run_portfolio_backtest._kernel` with a recording subclass, so it cannot drift from the Train
  backtest. Each tree runs its own engine and, once this lands, its own copy of the script.
- **D-3 CI pool, not the backtest runner.** ADR-023 keeps branch code off `hermes-backtest`. The CI
  pool already runs PR code; it gets the archive read-only.
- **D-4 Engine paths.** `src/hermes/domain/`, `src/hermes/application/`, `src/hermes/adapters/fills/`
  and `fees/`, `config/strategies.yaml`, an experiment's fee history or data manifest, and `uv.lock`.
  Anything else passes without a replay.
- **D-5 Approval.** A body line `Decision changes approved: <link to a comment on this PR>`, where
  the comment's author is the Code Owner. The body is read from the API on every run.
- **D-6 Rule changes are not decision changes.** A signal declined by another rule is still
  declined, as in `hermes parity check`.

## Requirements

- R-1 The recording kernel returns the production kernel's outcome and keeps one decision per
  executed signal, with the refusing rule.
- R-2 A record file reads back as the same decisions and holds decision fields only.
- R-3 A signal on one side only, or a changed fill outcome, is a change; a changed rule alone is not.
- R-4 `diff` exits 0 with no change, 1 with any change, 3 on an unreadable file.
- R-5 The window is the manifest's Train split in whole days, as the backtest workflow derives it.
- R-6 A changeset with no engine file passes without a replay; an engine change without the Train
  data fails.
- R-7 A decision change passes only with a cited comment of the Code Owner on this PR.
- R-8 The CI step runs on pull requests after the unit tier; the CI pool mounts the data read-only.

## Design

```text
ci.yml "Train decision replay" (pull_request, unit passed)
  └ train-decision-replay.sh
      git diff base..HEAD ∩ engine paths = ∅ → exit 0
      $HERMES_DATA_DIR/klines missing → ::error, exit 1
      head: decision_replay.py record --out head.jsonl
      base: git worktree base → uv sync → (base or head) decision_replay.py record --out base.jsonl
      decision_replay.py diff base.jsonl head.jsonl
        0 → remove label, pass
        1 → add label; decision_change_approval.py → pass or ::error
```

`record` loads the pinned bars through the manifest (UNTRUSTED, so OOS is refused), replays them
with `replay()` and a `RecordingKernel(FillKernel)`, and silences structlog below critical so the
kernel's fill logs carry no price into the CI log.

## Test plan

- `tests/unit/scripts_backtest/test_decision_replay.py` (R-1..R-5).
- `tests/unit/scripts_dev/test_train_decision_replay.py` (R-6..R-8): the shell script in a
  throwaway repository, the approval checker over a faked API, and the workflow and compose wiring.
- Smoke run on real Train data (2020-09-01 to 2024-06-30, three pairs): 334 decisions per tree in
  about 6 seconds; two runs of one tree match.

## Risks

- Engine PRs fail until the CI runners are recreated with the mount (Train is already prepared in
  `/srv/hermes-data`). The step says so.
- A base tree whose engine API differs from the head's script fails the replay until this lands;
  after that each tree runs its own script.
