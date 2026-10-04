# Change: 537-config-check

**SDD change**: `537-config-check`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md` (#537)

## Summary

`hermes config check` loads the config the way `hermes serve` does and runs
`check_startup`, without wiring or starting anything and without a network
call. Before: the VPS restart runbook told the operator to run it, but it did
not exist, and config problems showed only when serve refused to start. After:
the operator gets each problem by name, unset secrets by environment variable
and invalid fields by config path, with no value ever printed.

## Scope

### In

- `entrypoints/config_check.py`: the check, argument handling, text and `--json` output.
- `entrypoints/commands/config.py`: the `config` command, dispatching `check`.

### Out

- A `--db` flag: the certification store is read from the default ledger directory.
- Reporting every startup problem at once: `check_startup` raises on the first.

## Decisions

### D-1: Reuse load_config and check_startup unchanged

- **Decision**: the check calls the same two functions serve calls, so it cannot drift from serve.
- **Alternatives**: a parallel validator (drifts; rejected).
- **Cost of reversal**: LOW.

### D-2: Name fields from pydantic error locations only

- **Decision**: an invalid field is reported from `ValidationError.errors(include_input=False)` as path plus error type, so a rejected value is never echoed. Missing or unparseable files are exit 3 with the first line of the error.
- **Cost of reversal**: LOW.

### D-3: Secrets are named from the environment, never read into output

- **Decision**: when `check_startup` refuses a prod config, every unset channel variable is listed by name. Set variables are not named at all.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Exit codes

- **A-1.1**: 0 clean, 1 findings, 2 usage (including bare `hermes config`), 3 config unreadable. `uv run pytest tests/unit/entrypoints/test_config_check.py`

### R-2: No secret value printed

- **A-2.1**: a test sets a secret value in the environment and asserts it is absent from text and JSON output.

### R-3: Read-only, offline

- **A-3.1**: the command only reads YAML, `.env.local` and the certification file; it opens no socket.

## Design

```text
src/hermes/entrypoints/config_check.py     main(argv, stream, env)
src/hermes/entrypoints/commands/config.py  COMMAND = Command("config", ...)
```

## Test plan

1. Unit tests for each exit code, text and JSON output, and the secret-value invariant.
2. `bash scripts/dev/pr-preflight.sh` and the full suite pass.

## Risks

- **Only the first startup problem shows**: `check_startup` raises once; the operator re-runs after a fix.
- **A finding names which secret variables are unset**: names are not secrets.

## Dependencies

- #457 command registry.
