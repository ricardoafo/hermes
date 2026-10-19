# Shipped change specs move to `openspec/archive/`

**Date**: 2026-10-18
**Author**: AI Bot on behalf of the Code Owner
**Related**: ADR-009 §6 (Lifecycle, the Pending archival question), #1196

## Decision

A change spec stays in `openspec/changes/<name>/` while its tracking issue
is open. When the issue closes, the folder moves to
`openspec/archive/<name>/` with `**Status**: Shipped` (closed as completed)
or `Withdrawn` (closed as not planned). Archived specs are kept, not deleted.

## Context

By 18 Oct, 155 of 158 change folders described shipped work, and only 3
were live. Agents searching the repo for a term got a tenth of their hits
from old specs that describe the code as it was planned, not as it is.
ADR-009 left the choice between leaving specs in place and archiving them
Pending. Deleting them would lose the reasoning behind shipped code.

The archive is flat (`openspec/archive/<name>/`, not dated subfolders), so a
spec's relative links still resolve after the move and a reference only
changes `changes` to `archive`. A root `.ignore` keeps the archive out of
repo-wide `rg` searches. Naming the folder still searches it:
`rg <pattern> openspec/archive`.

## Consequences

- `scripts/dev/openspec-archive.py --sweep` archives every change whose
  issue has closed and repoints references, and `--closed` lists them
  without moving anything. Both ask GitHub, so they run at the release cut,
  not in CI; wiring `--sweep` into `cut-release.py` follows in #1196 once
  the release PRs that edit that script land.
- `openspec-archive.py --check` runs in pre-commit and in CI's `guards`
  check. It fails when a live change has no parseable `issue.md`, when an
  archived change is not Shipped or Withdrawn, or when a tracked file names
  a spec folder that does not exist. Release notes keep their original
  paths and are not checked.
- New changes still start in `openspec/changes/` from `TEMPLATE/`; the
  rule-15 gate is unchanged.
