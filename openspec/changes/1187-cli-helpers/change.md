# Change: 1187-cli-helpers

**SDD change**: `1187-cli-helpers`
**Date**: 2026-10-19
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1187-cli-helpers/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 2 of issue #1187 (repo audit 18 Oct, item 18, finding A-13; part 1, the
Telegram transport and the error re-parenting, is PR #1327). Every
command added `--db` and `--config-dir` by hand and handled a missing ledger
and a bad config its own way, so "no ledger" exited 3 in `risk-inputs` and 1
in `tax`. A new `hermes.entrypoints._cli` module holds the flags, the ledger
and config boot helpers and the documented exit codes, and every command
that adds those flags now uses it. Table row counting and the package version
lookup each move to one shared place. The main risk is the four commands
whose "no ledger" exit code changes from 1 to 3.

## Scope

### In

- `src/hermes/entrypoints/_cli.py`: `add_db_arg`, `add_config_dir_arg`,
  `require_ledger`, `open_ledger_or_exit`, `config_or_exit`,
  `load_config_or_exit` and the `EXIT_*` codes, documented in the module.
- Adopted by the 21 entrypoint modules that add `--db` or `--config-dir`:
  activity, anomalies, archive_check, bot, certify, commands/serve,
  config_check, failover_check, fx, ledger, migrate, parity, reconcile,
  risk_inputs, shadow_start, status, supervisor, tax, tax_filing, tax_pack,
  version.
- `src/hermes/adapters/ledger/row_counts.py`: `table_row_counts`, used by
  `migration_runner.dry_run` and `shadow_start._archive`.
- `hermes.entrypoints._release.package_version`, used by `hermes version`
  and `hermes shadow-start`.
- Guard test `tests/unit/entrypoints/test_cli_exit_codes.py`.

### Out

- `serve`'s missing-ledger refusal keeps exit 1 (see Risks).
- Handlers that are not a command's exit path keep their own code: the
  console and TUI turn a `ConfigError` into a view (`cli.py`, `console.py`,
  `_health.py`, `tui_tax.py`, the supervisor's console briefs), and
  `_anomaly_record.py` only warns and never changes an exit code.
- Composite handlers stay where one `except` covers more than the config:
  `certify` (config with `StartupError`, evidence and pre-registration),
  `shadow-start` (config with the certification store and the notebook),
  `parity` (config with the exchange and the ledger reader) and
  `config check` (reports invalid values as findings). Their codes are
  already 3 and the guard test pins them.
- `hermes tui` and `hermes positions` read `--db` with their own small
  parsers, which pass the other flags through to the console; they are not
  argparse commands and are left as they are.
- The duplicate-block ratchet named in the issue is #1201 (already merged).

## Decisions

### D-1: One code per case, 3 for both "no ledger" and "bad config"

- **Decision**: no file at `--db` (or SQLite refusing to open it) exits 3;
  a config that cannot be loaded exits 3. 0 done, 1 a finding or a read
  failure after the ledger opened, 2 usage. Codes above 3 stay a command's
  own (4 incomplete in `tax-filing`, 4 ledger in use in `serve`, 5 frozen in
  `tax-pack`).
- **Alternatives**: 1 for no ledger (what the three tax commands and `fx`
  used; it collides with "found a problem", so a script cannot tell a
  missing ledger from a mismatch); the sysexits codes in
  `code-conventions.md` §14d (64/65; no command uses them, and the systemd
  units match on 2 and 3).
- **Rationale**: 3 is what most commands already return for both cases
  (`risk-inputs`, `activity`, `anomalies`, `ledger`, `migrate`, `parity`,
  `reconcile`, `status`, `archive-check`), and the operator guide documents
  it: `operator-usage.md` lists "3 Config error or no ledger" for
  `hermes status`, and every command's usage text already says "3 config".
- **Cost of reversal**: LOW. The codes are constants in `_cli.py`; the guard
  test names every command.

### D-2: Commands whose code changes

| Command | Case | Before | After |
| --- | --- | --- | --- |
| `hermes tax` | no ledger, ledger SQLite cannot open | 1 | 3 |
| `hermes tax-filing` | no ledger | 1 | 3 |
| `hermes tax-pack` | no ledger | 1 | 3 |
| `hermes fx backfill` | no ledger | 1 (after creating an empty file) | 3, nothing created |

Read failures after the ledger opened stay 1 ("ledger read failure") in all
four. Every other command keeps its codes; `archive-check` now checks the
ledger before downloading the archive, with the same exit 3 and a failed
dead-man ping as before.

### D-3: Helpers return the value or the exit code

- **Decision**: `open_ledger_or_exit` returns a connection or an `int`;
  `config_or_exit` returns the loaded value or an `int`.
- **Alternatives**: raise `SystemExit` (tests call `main` and compare its
  return; a raise would bypass each command's `finally` and stream
  handling); a decorator (hides the control flow).
- **Rationale**: matches the `_parse(...) -> Namespace | int` shape every
  command already uses.
- **Cost of reversal**: LOW.

### D-4: Messages are the helper's, one wording each

- **Decision**: `ERROR: no ledger at PATH`, `ERROR: could not open the
  ledger at PATH: REASON`, `ERROR: config load failed: REASON`.
- **Rationale**: the wording most commands used; the variants ("ledger not
  found at", "config:", a trailing full stop) were accidents of copying.
- **Cost of reversal**: LOW.

### D-5: Shared places for row counts and the package version

- **Decision**: `table_row_counts` in a new `adapters/ledger/row_counts.py`
  (both callers hold a `sqlite3.Connection`, and the adapter layer owns
  SQLite); `package_version` in `entrypoints/_release.py`, next to the
  release lookup both callers already import.
- **Rationale**: `shadow-start` must not import the migration runner's
  private helper, and `_release.py` exists so serve can read the release
  without importing the version command.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Shared helpers with documented codes

- **A-1.1**: `test -f src/hermes/entrypoints/_cli.py`
- **A-1.2**: `uv run pytest -q tests/unit/entrypoints/test__cli.py` passes.

### R-2: Every command with `--db` or `--config-dir` uses the helpers

- **A-2.1**: `grep -rn 'add_argument("--db"\|add_argument("--config-dir"' src/hermes/entrypoints | grep -v _cli.py`
  prints nothing.
- **A-2.2**: `uv run pytest -q tests/unit/entrypoints/test_cli_exit_codes.py -k every`
  passes (each helper user is guarded or exempted with a reason).

### R-3: No ledger and bad config exit 3 in every command

- **A-3.1**: `uv run pytest -q tests/unit/entrypoints/test_cli_exit_codes.py`
  passes (14 no-ledger and 17 bad-config cases).

### R-4: One table counter and one version lookup

- **A-4.1**: `grep -rn --include='*.py' "SELECT COUNT(\*) FROM \"" src/hermes` finds only
  `adapters/ledger/row_counts.py`.
- **A-4.2**: `grep -rn 'metadata.version("hermes")' src/hermes` finds only
  `entrypoints/_release.py`.

## Design

```python
# src/hermes/entrypoints/_cli.py
EXIT_OK = 0; EXIT_FAILURE = 1; EXIT_USAGE = 2; EXIT_CONFIG = 3; EXIT_NO_LEDGER = 3

def add_db_arg(parser, *, default: Path | None = DEFAULT_DB_PATH) -> None
def add_config_dir_arg(parser, *, default: Path | None = DEFAULT_CONFIG_DIR) -> None
def require_ledger(db: Path, err: TextIO) -> int | None
def open_ledger_or_exit(db, err, *, read_only: bool = True) -> sqlite3.Connection | int
def config_or_exit[T](load: Callable[[], T], err, *, on_error=None) -> T | int
def load_config_or_exit(config_dir, err, *, env=None, on_error=None) -> HermesConfig | int
```

A command reading its own reader (`SqliteParityReader`, `LedgerFilingReader`)
calls `require_ledger` first; a command opening the connection itself calls
`open_ledger_or_exit`. `status` passes its module-level `load_config` through
`config_or_exit` so the tests that replace it keep working; `archive-check`
passes `on_error` to send its failed dead-man ping.

Run against the tree:

```text
$ grep -rn 'add_argument("--db"\|add_argument("--config-dir"' src/hermes/entrypoints | grep -v _cli.py
(no output)
$ uv run pytest -q tests/unit/entrypoints/test_cli_exit_codes.py tests/unit/entrypoints/test__cli.py tests/unit/adapters/ledger/test_row_counts.py
52 passed
```

With the four commands of D-2 reverted, the guard fails exactly
`test_no_ledger_exits_with_the_documented_code[fx|tax|tax-filing|tax-pack]`.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.
3. All CI jobs green.

Tests changed because the code changed deliberately (D-2, D-4):
`test_tax.py::test_missing_ledger_fails_without_creating_one` (1 → 3, message),
`test_tax_filing.py::test_missing_ledger_exits_3_and_creates_nothing` (1 → 3),
`test_tax_pack.py::test_missing_ledger_exits_3` (1 → 3),
`deploy/test_systemd_units.py::test_tax_pack_flags_are_accepted_by_the_real_cli_parser`
(1 → 3), and message-only updates in `test_ledger.py`, `test_parity.py` (no
full stop) and `test_failover_check.py` (`config load failed`).
`test_version.py::test_package_version_unknown_when_not_installed` now patches
the shared lookup and checks it through `collect_info`.

Test tiers touched: unit, property

## Risks

- **A script or unit that reads exit 1 as "no ledger" from `tax`,
  `tax-filing`, `tax-pack` or `fx backfill`**: none in `deploy/` or
  `scripts/`; `hermes-tax-pack.service` is a oneshot without
  `RestartPreventExitStatus`, so 1 and 3 both mark the run failed. The
  operator's own scripts would need the new code.
- **`serve` still exits 1 with no ledger**: its refusal is a `StartupError`
  inside the composition root. The serve units set
  `RestartPreventExitStatus=2 3`, so moving it to 3 would stop systemd from
  retrying while a disk mounts. Left for the Code Owner to decide; the guard
  test lists it as exempt with this reason.
- **`fx backfill` no longer creates an empty ledger at a wrong `--db`**:
  intended (the read-write open used to leave a stray file behind).
- **`shadow-start`'s archive check no longer compares `sqlite_sequence`**:
  the shared counter skips SQLite's own tables. The backup API copies them
  with the data, and every user table is still compared.

## Dependencies on other changes

- Part 1 of #1187 is PR #1327 (Telegram transport, `InfraError`
  re-parenting); the two touch different files and merge in either order.
