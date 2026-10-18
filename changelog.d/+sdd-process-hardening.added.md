- **SDD process hardening — codify PR/actor/changelog gates locally**
  (`scripts/dev/pr-actor-check.sh` NEW, `scripts/dev/create-pr.sh`,
  `scripts/dev/changelog-check.sh`,
  `scripts/dev/sub-agent-prompt-checklist.md`,
  `docs/guides/automation-trust-chain.md` NEW, `AGENTS.md`): permafixes
  process rakes surfaced during 2026-08-25 autonomous SDD delegation.
  (1) New `pr-actor-check.sh` refuses PR-open when the gh-authenticated
  user matches the intended reviewer, wired into `create-pr.sh` with
  exit code 4 (silent deadlock previously only discovered at review
  time when the operator tried to self-approve). (2)
  `changelog-check.sh` now requires a `changelog.d/*.md` fragment for
  `feat|fix|refactor|perf` — CHANGELOG.md-only edit fails because it
  merge-conflicts across parallel PRs at scale. Bypass via
  `ALLOW_CHANGELOG_MD_EDIT=1` preserves old behavior for forks. (3)
  Sub-agent checklist extended with items 5-9: `isolation: worktree`
  REQUIRED for parallel spawns, PR body via `render-pr-body.sh` only,
  changelog fragment as acceptance criterion (not follow-up),
  `no-vague-test-assertions.py` as pre-PR checkpoint, upfront-fix-up
  rule (`ruff format` + `markdownlint-cli2 --fix` before first WIP push
  to preempt hook-loop tool-use inflation). (4) New guide
  `docs/guides/automation-trust-chain.md` documents the PR #209 (queue
  BEHIND reconciliation) + #210 (auto-update on push:main) + #213
  (GitHub App auth) hands-off architecture as a cross-repo reproduction
  reference. (5) `AGENTS.md` rule 13 gains a paragraph pointing at
  `pr-actor-check.sh` and the new guide. Every new gate ships with a
  `SKIP_*` env-var escape hatch (pattern established by
  `openspec/changes/dor-dod-enforcement/`).
