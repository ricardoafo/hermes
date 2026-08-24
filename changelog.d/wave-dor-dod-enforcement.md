### Added

- **DoR/DoD openspec-first enforcement** (#194 → openspec change
  `dor-dod-enforcement`): two-layer gate that refuses to open a PR (or
  push a branch) whose latest commit is `feat|refactor|perf` and whose
  diff vs `origin/main` includes no `openspec/changes/*/change.md`
  addition or modification. Enforcement lives at both
  `scripts/dev/create-pr.sh` (exit 3, new distinct code) and
  `scripts/dev/pr-preflight.sh` (check #13). AGENTS.md rule 15 tells
  the sub-agent orchestrator to verify the spec BEFORE delegating any
  Write/Edit tool. Bypass with `--skip-openspec-check "reason"` (create-pr)
  or `SKIP_OPENSPEC_CHECK=1` (pr-preflight) — reason is audit-logged
  to stderr. Rationale: between 2026-08-21 and 2026-08-24 the team
  shipped ~180 PRs and only 4 openspec change specs landed. Follow-up
  PR adds the CI job + pre-commit hook layers described in #194.
