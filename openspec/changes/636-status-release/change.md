# Change: 636-status-release

**SDD change**: `636-status-release`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/636-status-release/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes status` shows the running release and when it went live, in the text
header and in `--json`, so the operator does not need SSH and `readlink` before
or after a deploy.

## Scope

### In

- `read_release` and `ReleaseInfo` in `entrypoints/version.py`: the one reader
  of the `/opt/hermes/current` symlink, shared by `hermes version` and
  `hermes status`.
- A `release:` line under the status header; `release` and `live_since` JSON
  fields; an injectable `release_link` on `status.main`.

### Out

- The console home screen line (Console area, follows PR #635).
- The `deploy-<sha12>` tag from issue #626 (see D-2).

## Decisions

### D-1: live since is the symlink's own mtime

`install_release.sh` flips `current` atomically, so the link's lstat mtime is
the flip time. No new state file, no ledger read.

### D-2: no tag lookup

The `deploy-<sha12>` tag from issue #626 is derivable from the release name
(the release directory is the commit name), so `status` does no network or git
lookup and the line stays offline and read-only.

### D-3: missing link is not an error

On a dev machine the line reads `release: unknown` with no live-since value;
exit codes and the verdict logic are untouched.

## Requirements

### R-1: text header shows the release and the flip time in Madrid time

### R-2: `--json` carries `release` and `live_since` (ISO-8601 UTC, null when unknown)

### R-3: no link: `release: unknown`, no live-since, same exit code

## Design

`status.main` reads the link once and passes a `ReleaseInfo` to
`render_status` and `report_to_dict`; both omit the release when it is `None`,
so other callers are unchanged.

## Test plan

`tests/unit/entrypoints/test_status.py`: text and JSON with and without a
release; the live-since value set through `os.utime(..., follow_symlinks=False)`
(skipped where symlinks are unavailable). `test_version.py`: `read_release` for
a missing and a dangling link. The plain snapshot gains the `release: unknown`
line.

## Risks

Shared files `status.py` and `status_view.py` are touched by other PRs; the
diff is small. A filesystem that does not keep symlink mtimes would show a
wrong time.

## Dependencies on other changes

- #539 (`hermes version`, the release reader), #626 (deploy tag, see D-2).
