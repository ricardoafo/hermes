# Change: 812-release-cut

**SDD change**: `812-release-cut`
**Date**: 2026-10-06
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/812-release-cut/change`.

## Summary

`scripts/dev/cut-release.py <version>` cuts a release as ADR-022 §2 describes. It sets the version in
`pyproject.toml`, `src/hermes/__init__.py` and `uv.lock`, compiles the changelog, deletes the compiled fragments and
writes the release PR body. `pr-body-lint.sh` enforces ADR-022 §7's `Engine change: yes/no` line on release PRs. The
main risk is losing changelog text while compiling, so the cut keeps every hand-written `[Unreleased]` entry and a
test checks that against the real `CHANGELOG.md`.

## Scope

### In

- `scripts/dev/cut-release.py` and `tests/unit/infrastructure/test_cut_release.py`.
- `scripts/dev/pr-body-lint.sh`: a body with a `## Release` heading needs `Engine change: yes` or `no`.
- `changelog.d/README.md`: the cut replaces the manual aggregate-then-delete steps.

### Out

- The first cut, `1.0.0-shadow.1`: its own release PR, made with this script once it merges.
- `v` tags on deploy (issue #813), version-only deploys (issue #814), the version on screen (issue #815).

## Decisions

### D-1: PEP 440 alpha stands in for `-shadow.N` in package metadata

- **Decision**: the package declares `X.Y.ZaN` for release `X.Y.Z-shadow.N`; `X.Y.Z` is the same in both.
- **Alternatives**: `X.Y.Z-shadow.N` in `pyproject.toml` (rejected: not valid PEP 440, so `uv` and the wheel build
  refuse it); a local version `X.Y.Z+shadow.N` (rejected: local versions do not order, so `shadow.2` would not sort
  after `shadow.1`).
- **Rationale**: ADR-006 and ADR-022 name releases in SemVer; PEP 440 alpha orders the same way (`a1 < a2 < final`),
  and the mapping is one-to-one, so tags and screens can always show the SemVer name (`to_semver`).
- **Cost of reversal**: LOW. One mapping function, before any tag exists.

### D-2: The cut keeps hand-written `[Unreleased]` entries

- **Decision**: the new section holds the legacy entries first, then fragments, per Keep-a-Changelog section, with any
  other legacy heading (`Notes`) after them.
- **Alternatives**: `aggregate-changelog.py --apply` as-is (rejected: it replaces `[Unreleased]` with fragments only,
  dropping about 36 hand-written entries and the link references).
- **Rationale**: the changelog is the release record (ADR-006); nothing may be lost.
- **Cost of reversal**: LOW.

### D-3: The PR body is written outside the checkout

- **Decision**: default `<tmp>/hermes-release-<version>.md`, or `--body-out`.
- **Alternatives**: a file in the repo root (rejected: not ignored by git, so it could be committed).
- **Cost of reversal**: LOW.

## Requirements

### R-1: Versions

- **A-1.1**: `to_package_version("1.0.0-shadow.1") == "1.0.0a1"` and `to_semver` inverts it for every release version.
- **A-1.2**: malformed, equal or older versions, and mismatched version files, exit 2 and change no file.

### R-2: Changelog

- **A-2.1**: a cut leaves an empty `[Unreleased]` above `## [<version>] - <date>` and adds a `[<version>]:` link.
- **A-2.2**: a dry cut of the real `CHANGELOG.md` keeps every `[Unreleased]` bullet plus one per fragment bullet.
- **A-2.3**: numbered and orphan fragments are deleted; `README.md` and `wave-*.md` stay.

### R-3: Release PR body

- **A-3.1**: the body has `## Release` and an `Engine change:` line; without `--engine-change` it is a placeholder.
- **A-3.2**: `HERMES_SELF_TEST=1 bash scripts/dev/pr-body-lint.sh` passes, including a blank and an answered release
  body.

## Design

```text
scripts/dev/cut-release.py <version> [--date D] [--engine-change yes|no] [--dry-run] [--body-out P]
  to_package_version(version)              # refuse non X.Y.Z[-shadow.N]
  read_current_version(pyproject, init)    # refuse mismatch
  check_newer(current, new)                # refuse equal or older
  collect_fragments(changelog.d)           # from aggregate-changelog.py
  cut_changelog(changelog, version, date, fragments)
  -> write version files, CHANGELOG.md; delete compiled fragments; uv lock; write PR body
```

## Test plan

1. `uv run pytest tests/unit/infrastructure/test_cut_release.py` (unit and property).
2. `HERMES_SELF_TEST=1 bash scripts/dev/pr-body-lint.sh`.
3. A cut of `1.0.0-shadow.1` in a throwaway worktree: `uv lock` moves `hermes` to `1.0.0a1`; markdownlint is clean on
   the new `CHANGELOG.md`.

## Risks

- A future fragment format the aggregator skips would not be compiled or deleted. Mitigation: the cut uses the
  aggregator's own regexes for both.
