- **Process hardening wave 6** (`scripts/dev/launch-agent.sh`,
  `.pre-commit-config.yaml`, `AGENTS.md`,
  `docs/decisions/2026-08-23-fetch-before-launch-tooling.md`,
  `docs/decisions/README.md`): turns the AGENTS.md rule 4 norm ("Fetch
  origin/main BEFORE spawning a worktree-isolated sub-agent") into an
  ENFORCED tooling gate. Introduced by GH #146 after Wave 4 (PR #133)
  established the norm and Wave 5 (PR #140) hardened process cadence
  without closing this window. New `launch-agent.sh` fetches
  `origin/main`, exits 1 with an exact fast-forward command when local
  `main` is behind, and prints the current `origin/main` SHA on
  success so the orchestrator can pin sub-agents against a KNOWN base
  in the launch prompt. `--quiet` outputs SHA-only for scripting.
  Hermetic self-test (`HERMES_SELF_TEST=1`) covers 7 fixtures via a
  `GIT_CMD` shell-stub simulating fresh / stale / ahead / offline /
  norefs / unknown-flag paths — no network, no ref manipulation.
  Pre-commit hook `launch-agent-self-test` mirrors the existing
  `create-pr-self-test` pattern: any change to `launch-agent.sh`
  re-runs its self-test on commit. AGENTS.md prose stays UNCHANGED —
  the norm still explains WHY; the new tool is the CHECK that
  ENFORCES it. Decision journal entry documents the rejection of
  Option B (post-checkout git hook — pre-commit does not manage that
  hook type, fires only per checkout not per launch) and Option C
  (extending `pr-preflight.sh` — dilutes its pre-push contract and
  hurts discoverability). Placeholder-named fragment per
  `changelog.d/README.md` — aggregator ignores non-numeric prefixes.
