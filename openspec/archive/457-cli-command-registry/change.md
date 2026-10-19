# Change: 457-cli-command-registry

**SDD change**: `457-cli-command-registry`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md` (#457)

## Summary

`hermes` subcommands move to one module each under
`hermes.entrypoints.commands`. Each module declares a `COMMAND`, and
`cli.py` discovers them. Today every new command edits the same help map and
dispatch table in `cli.py`, so two PRs that each add a command always
conflict, and every merge drops the other PR's approval. After this change a
new command is one new file and touches nothing shared. There is no behaviour
change: `hermes --help` lists the same commands with the same text, and every
command runs the same code.

## Scope

### In

- `entrypoints/commands/__init__.py`: `Command`, `discover()`, `collect()`.
- One module per command on main: `archive-check`, `certify`, `health`,
  `journal`, `positions`, `preview`, `serve`, `simulate` (stub), `status`.
  `positions` sets `wants_demo` and receives the leading `--demo` scenario,
  as it did from the old table. The `health` and `serve`
  argument handling moves out of `cli.py` into its module, unchanged.
- `cli.py` keeps Sentry init, the `--demo` flag and top-level help, built
  from the registry.

### Out

- Commands added by open PRs (#435, #447, #453): each moves its own
  command into a module when it merges main.

## Decisions

### D-1: Discovery by package scan, name equals module

- **Decision**: `discover()` imports every non-private module in the package.
  A command's name must equal its module's name, with `_` read as `-`
  (`archive_check.py` declares `archive-check`), so the file system keeps
  names unique.
- **Alternatives**: entry points in `pyproject.toml` (another shared list,
  same conflicts); a decorator registry (needs a shared import list to load
  the modules).
- **Cost of reversal**: LOW.

### D-2: Thin wrappers for commands that already have a module

- **Decision**: `preview`, `certify`, `journal` and `status` keep their
  implementation modules; their command module only declares `COMMAND`.
- **Rationale**: no behaviour or import changes for code and tests that use
  those modules.
- **Cost of reversal**: LOW.

## Requirements

### R-1: No shared command list

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_command_registry.py`
  (cli.py names no command; every file is discovered; help lists every
  command; malformed modules are rejected).

### R-2: No behaviour change

- **A-2.1**: `uv run pytest tests/unit/entrypoints tests/unit/deploy`

## Design

```text
src/hermes/entrypoints/commands/__init__.py   Command, discover, collect
src/hermes/entrypoints/commands/<name>.py     COMMAND = Command(name, summary, run)
src/hermes/entrypoints/cli.py                 main(): discover, help, dispatch
```

## Test plan

1. The registry tests above.
2. Every existing CLI test passes. Two tests change only where they reach
   moved code: `serve_main` now lives in `commands.serve`, and the `--demo`
   test spies through `discover`.
3. `bash scripts/dev/pr-preflight.sh` and the full unit suite pass.

## Risks

- **An import error in one command module breaks every command**: the same
  as today, where `cli.py` imports every implementation module at startup.
- **Open PRs that edit `cli.py` conflict once more**: each resolves it by
  moving its command into a module, after which conflicts stop.
