- **Process hardening wave 7** (`.pre-commit-config.yaml`,
  `scripts/dev/regenerate-guides-index-hook.sh`,
  `scripts/dev/no-pr-body-at-repo-root.sh`,
  `scripts/dev/sub-agent-prompt-checklist.md`, `AGENTS.md`): closes
  three drifts observed during the 2026-08-23 → 2026-08-24 autonomous
  work session (audited in GH #181, deferring items 5-10). (1) The
  `regenerate-guides-index` pre-commit hook now delegates to
  `regenerate-guides-index-hook.sh`, which re-runs the regen script,
  `git add`s the refreshed `docs/guides/README.md`, and fails LOUDLY
  with a `git commit --amend --no-edit` hint when the file actually
  changed. Wave 5 sub-agent and the 2026-08-24 Sentry salvage each
  burned one recovery cycle on the previous silent-drift pattern
  (commit, push, CI fails on "guides index stale"). Self-test covers
  3 fixtures including idempotence. (2) New
  `no-pr-body-at-repo-root` pre-commit hook rejects `pr-body-*.md`,
  `.pr-body-*.md`, and `*.pr-body.md` files staged at the repo root
  BEFORE they hit markdownlint MD041 (first line must be H1 — PR
  bodies open with `## Summary`, so they always fail). Wave 3 patched
  `create-pr.sh` to copy repo-relative bodies to `/tmp/`; Wave 7
  slams the door on the file ever reaching the index. Hermetic
  self-test (`HERMES_SELF_TEST=1`) covers 14 fixtures via a pure
  matcher — no filesystem writes, no git-index mutation. Pre-commit
  hook `no-pr-body-at-repo-root-self-test` mirrors the existing
  `create-pr-self-test` pattern: any change to the script re-runs
  its self-test on commit. (3) `AGENTS.md § On sub-agent delegation`
  gained a "Sub-agent rate-limit resilience" subsection with three
  rules: WIP commits every ~30 tool uses (or per Part), push after
  every WIP commit for GitHub salvage, `mem_save` engram checkpoint
  at each milestone. New
  `scripts/dev/sub-agent-prompt-checklist.md` holds the copy-paste
  block orchestrators paste into non-trivial sub-agent launch
  prompts. Part 4 of the audit (merge-dependency label + GH Action
  gate) DEFERRED per the "> 15 min → separate issue" rule in the
  spec — filed follow-up for a dedicated PR. Placeholder-named
  fragment per `changelog.d/README.md` — aggregator ignores
  non-numeric prefixes.
