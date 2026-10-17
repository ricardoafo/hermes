# Change: research-batch-format

**SDD change**: `research-batch-format`
**Date**: 2026-10-17
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#1057)

## Summary

Adds the research batch document of ADR-026 §1 and the code that checks it: a
template for the batch and for a hypothesis card, a parser
(`hermes.application.calibration.research_batch`), `hermes batch check`, and
the backtest driver's `--batch FILE --candidate ID`. The format, the check
names, the CLI flags and the exit codes follow the research funnel interface
contract v2 that the Backtesting thread owns
(`/mnt/project-files/hermes/research-funnel/interface.md` §1 and §2). Main
risk: a check that passes a batch it should refuse would let an unsigned,
edited or loosened batch start a run, so every refusal has its own test.

## Scope

### In

- `ports/git_history.py` and `adapters/git_history/subprocess_git.py`:
  read-only git access (file at a commit, object id, ancestry).
- `application/calibration/research_batch.py`: `parse_batch`, `load_batch`,
  `check_batch`, `ResearchBatch`, `Candidate.funnel_replays()`.
- `application/backtest/funnel_replay.py`: `FunnelReplay` moved out of
  `scripts/backtest/funnel_evidence.py` so the package can build a candidate's
  grid; the script re-exports it and v2's `FUNNEL_REPLAYS` is unchanged.
- `application/calibration/backtest_funnel.py`: `parse_funnel_block`, split
  out of `parse_funnel_front_matter` so a batch's block is range-checked by the
  same code.
- `entrypoints/batch.py`: `hermes batch check`.
- `scripts/backtest/run_portfolio_backtest.py`: `--batch`, `--candidate`.
- `docs/experiments/templates/`: batch and hypothesis card templates.

### Out

- The generic gate and `certify --batch` (issue #1058).
- The batch judge and verdict records (issue #1059).
- Workflow trigger and jobs (issues #1060, #1061).
- Candidate strategy code (issues #1063 to #1066).

## Decisions

### D-1: The signing hash is checked the way pre-registration v2 was signed

`content_sha256` is the hash of the file at `signing_commit_precursor`, and
`content_sha256_scope` must be exactly `git show <precursor>:<path> | sha256sum`.
The check recomputes it with git, so the file cannot carry its own hash and a
scope cannot point at another file.

### D-2: Code binding compares named paths, not the whole tree

Each candidate lists `code_paths`; each must have the same git object id at
`code_commit` as at HEAD. Comparing all of `src/` would fail every batch the
moment an unrelated PR merges.

### D-3: Only the ADR-021 §5 numbers are floors

`dsr_min >= 0.95`, `pbo_max <= 0.05`, `t_stat_above >= 3`,
`trade_count_min >= 100`. The other funnel keys keep the range checks
pre-registrations already have (contract v2).

### D-4: The driver refuses a signed base the strategy does not start from

If a candidate's signed base value differs from the strategy's own default, its
trials would test something other than what was signed, so the run stops before
logging anything.

## Requirements

- R-1: A batch with fewer than 2 or more than 6 candidates is refused.
- R-2: Two candidates in one idea family are refused.
- R-3: A candidate without a summary (or with more than 6 lines) is refused.
- R-4: A `backtest_funnel` looser than a floor is refused.
- R-5: A missing or mismatched `content_sha256` fails.
- R-6: A card whose bytes do not match `card_sha256` fails.
- R-7: A code path whose object id at HEAD differs from `code_commit` fails.
- R-8: `hermes batch check` prints one `PASS|FAIL <check>` line per check in
  contract order and exits 0, 1, 2 or 3.
- R-9: `--batch --candidate` runs the candidate's grid and adds `batch.id` to
  every trial line; without them the driver is unchanged.

## Design

See the contract §1 and §2 for the format and flags. `check_batch` parses the
header once; a check that needs the whole batch parsed reports
`not checkable: <first error>` when parsing stops earlier, so one fault shows
as one cause.

## Test plan

- Unit: every refusal above, the draft and signed paths, the template.
- Property (hypothesis): candidate count accepted iff 2..6; each floor value
  accepted iff at least as strict as the floor.
- Adapter: the git adapter against a throwaway repository.
- CLI: `hermes batch check` against a throwaway repository signed as v2 was.
- Driver: a synthetic candidate run, its trial lines and its evidence.

## Risks

- A contract change after merge needs a follow-up PR; the check names and
  flags are pinned by tests so a change is visible.
