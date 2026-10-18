# Change: 539-hermes-version

**SDD change**: `539-hermes-version`
**Date**: 2026-10-04
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/539-hermes-version/change`.

## Summary

`hermes version [--db PATH] [--json]` prints the package version, the live
release, the Python version and the ledger migration level. `hermes --version`
and `-V` print the same plain output. The release is the target name of the
`/opt/hermes/current` symlink that the deploy script maintains. Everything
degrades to `unknown` or `no ledger`; the command never fails.

## Scope

### In

- `src/hermes/entrypoints/version.py` and `commands/version.py`.
- A minimal `--version` / `-V` delegation in `cli.py`.

### Out

- Git sha lookup (a release directory is named after the sha12 already).

## Decisions

### D-1: Release comes from the symlink target name

- **Decision**: Resolve `/opt/hermes/current` and print the directory name.
- **Alternatives**: `git rev-parse` (no git checkout on the VPS).
- **Rationale**: `install_release.sh` names release dirs `versions/<sha12>`.
- **Cost of reversal**: LOW.

### D-2: Ledger opened read-only, level via the migrate reader

- **Decision**: Open with `mode=ro` and reuse `pending_migrations`.
- **Alternatives**: a second reader (drift risk).
- **Rationale**: one definition of "pending".
- **Cost of reversal**: LOW.

## Requirements

### R-1: Report fields

- **A-1.1**: `hermes version` prints version, release, python and ledger lines and exits 0.
- **A-1.2**: `--json` prints one JSON document with the same four fields.

### R-2: Degradation

- **A-2.1**: A missing or dangling release link prints `release: unknown`.
- **A-2.2**: A missing or unreadable ledger prints `ledger: no ledger`.

### R-3: Usage

- **A-3.1**: Bad arguments exit 2; `-h` exits 0; `--version` and `-V` print the plain output.

## Design

Pure `collect_info(db, release_link)` returns a frozen dataclass; `main`
renders it. The release link path is a parameter for tests.

## Test plan

Unit tests in `tests/unit/entrypoints/test_version.py`: symlink, dangling
link, absent link, ledger levels, JSON, usage, `--version`, `-V`.

## Risks

- `Path.resolve()` on Windows symlinks in tests; tests skip when symlinks are unavailable.

## Dependencies on other changes

None.
