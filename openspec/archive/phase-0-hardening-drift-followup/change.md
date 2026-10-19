# Change: phase-0-hardening-drift-followup

**SDD change**: `phase-0-hardening-drift-followup`
**Date**: 2026-08-22
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/phase-0-hardening-drift-followup/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Two enhancements to `scripts/dev/render-pr-body.sh` closing SDD-workflow
drift discovered during recent PRs. Multi-close support parses all `closes:`
lines from `issue.md`, deduplicates, and renders `Closes #A #B #C` in the
output body — prior behaviour read only the single `issue:` field, which left
underlying feature issues open (discovered when PR #92 failed to auto-close
issue #18). Remote-branch fallback adds an optional `--branch <ref>` arg so the
script can read `change.md`/`issue.md` via `git show origin/<ref>:<path>`
when the local copy is absent, preserving the Code Owner's
delete-local-after-push discipline without requiring a workflow change.

## Scope

### In

- `scripts/dev/render-pr-body.sh`: add `read_change_file()` helper,
  extend `render_body()` with `closes-list` param, add `--branch` arg
  parsing, replace file-read block, extend self-test with fixtures 5 + 6.
- `scripts/dev/pr-preflight.sh`: auto-derive branch via
  `git rev-parse --abbrev-ref HEAD` and pass `--branch` through to
  render-pr-body.sh when on a feature branch.
- `openspec/changes/TEMPLATE/issue.md`: document `closes:` field with
  commented example lines showing the multi-value pattern.

### Out

- Backfilling `closes:` into existing `issue.md` files — new parser is
  additive; backward compat confirmed by self-test fixture 4 (unchanged).
- Integration self-test of the remote-fetch path (requires live git state;
  covered by fixture 6 stub + manual verify).
- PR A docs changes (separate PR #114: naming scrub + sub-agent delegation
  guidance in AGENTS.md).

## Decisions

- **D-3 (#97)**: Multi-close schema is multi-LINE (`closes: N` repeating)
  rather than comma-list. Mirrors existing single-value pattern of `issue:`
  and `url:` fields; same `awk '{print $2}'` parser; diff-friendly.
- **D-4 (#93)**: `--branch` is opt-in. Local file wins when present; remote
  is fallback only. `git fetch origin <ref>` runs before `git show` to
  survive slow-network races. On failure, diagnostic message names both
  local and remote paths tried.
- **D-6**: Self-test fixture 6 uses a stub (no real git I/O) to keep the
  self-test hermetic and fast. Integration test is manual.

## Test plan

- `HERMES_SELF_TEST=1 bash scripts/dev/render-pr-body.sh` → exit 0, 6 fixtures pass.
- `bash scripts/dev/render-pr-body.sh phase-1-code-conventions-and-value-objects -o /tmp/body.md` → `Closes #106` (single-issue backward-compat).
- `bash scripts/dev/pr-preflight.sh` → all 12+ checks green.
- `pre-commit run --all-files` → clean.

## Risks

- **Low blast radius**: script-only, additive changes. `--branch` is opt-in;
  `closes:` parsing is backward-compat (missing = old behaviour).
- **Rollback**: single revert restores prior behaviour. No schema breakage;
  existing `issue.md` files unaffected.
- **Regression coverage**: extended self-test (fixtures 1–6). Fixture 6 uses
  a stub for `read_change_file` — no real git I/O in tests.

## Dependencies

- Companion: PR #114 (Change 3 PR A — docs naming scrub + sub-agent guidance).
- Follows: PR #112 (Change 1), PR #113 (Change 2).

## Follow-ups (out of scope)

- Future SDD PRs that ship as multi-issue bundles can dogfood by adding
  `closes:` lines to their `issue.md`.
- Consider strengthening the `radon` exit-code semantic (noted in
  pr-preflight.sh comment) in a future hardening pass.
