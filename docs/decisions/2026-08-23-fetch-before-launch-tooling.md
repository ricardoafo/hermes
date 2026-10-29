# Fetch-before-launch as a wrapper script, not a git hook

**Date**: 2026-08-23
**Author**: Maintainer
**Related**: AGENTS.md §"On sub-agent delegation" rule 4, Wave 4 (PR #133 —
introduced the AGENTS.md norm), Wave 5 (PR #140 — process hardening
cadence), GH #146 (this change), engram `preferences/post-pr-discipline`

## Decision

Enforce AGENTS.md rule 4 ("Fetch origin/main BEFORE spawning a
worktree-isolated sub-agent") with a NEW wrapper script
`scripts/dev/launch-agent.sh` that the orchestrator MUST call before each
sub-agent launch. Rejects launches when local `main` is behind
`origin/main`, prints the exact fast-forward command, and emits the
current `origin/main` SHA the sub-agent can pin its base against.

Rejected: post-checkout git hook (Option B). Rejected: extending
`pr-preflight.sh` (Option C). Kept the prose in AGENTS.md unchanged — the
norm still explains WHY; the new tool is the CHECK that ENFORCES it.

## Context

Wave 4 (PR #133) added the AGENTS.md rule after two rebase cascades in
wave-3 traced back to sub-agents inheriting stale local `main` via
`git worktree add`. The rule was documentation only. GH #146 tracks the
occasional cost (~5% token overhead per mid-flight rebase, worse when a
sub-agent's PR races another merge and hits silent conflicts).

`pr-preflight.sh` already checks "Base up-to-date with origin/main"
(check #1), so the freshness gate exists — but at PUSH time, once the
sub-agent has already done ALL the work on a stale base. Wave 6 closes
the earlier window: BEFORE the sub-agent starts.

## Options considered

**Option A — wrapper script** `scripts/dev/launch-agent.sh` called by
the orchestrator before spawning each sub-agent.

- Pros: mirrors the existing `create-pr.sh` pattern (self-testable,
  `HERMES_SELF_TEST=1` idiom, pre-commit-wired). Zero-friction — one
  bash call in the launch flow. Prints the `origin/main` SHA so the
  sub-agent can pin its base explicitly. Blocks EARLY so nothing wasted.
- Cons: still norm-driven at the invocation point. If the orchestrator
  forgets to call it, the gate does not fire.

**Option B — post-checkout git hook** that warns when a worktree is
created onto a stale base.

- Pros: fires automatically on `git worktree add` (checkout event).
- Cons: pre-commit's default install does NOT manage the `post-checkout`
  hook type — needs a separate `pre-commit install --hook-type
  post-checkout` step OR docs telling every operator to symlink the
  script manually. Poor discoverability. Also: WARN-only in the issue's
  proposal; making it block would risk breaking `git worktree add` in
  legitimate offline cases. And it fires ONCE per checkout, not per
  sub-agent launch — the same worktree might host multiple launches
  and only the first would see the gate.

**Option C — extend pr-preflight.sh** with `--check-base` subcommand
called manually by the orchestrator before sub-agent launch.

- Pros: reuses existing infrastructure, no new script.
- Cons: pre-flight is a PRE-PUSH gate. Bolting a pre-launch subcommand
  onto it dilutes the tool's contract — operators are used to running
  `pr-preflight.sh` in ONE mode (pre-push). Also: same
  norm-driven-invocation concern as A, but with worse discoverability
  (buried behind a flag on a script named for the OTHER lifecycle
  moment).

## Rationale for Option A

Three deciding factors:

1. **Contract clarity**: `launch-agent.sh` is named for its purpose. An
   operator (or a future sub-agent orchestrator) reading `AGENTS.md`
   sees the name and knows what it does. `pr-preflight.sh --check-base`
   requires reading the pre-flight source to know it does dual-duty.
2. **Testability parity**: A separate script gets a separate self-test
   fixture that documents pass/fail semantics for THIS gate specifically
   — see `HERMES_SELF_TEST=1 bash scripts/dev/launch-agent.sh`. Bolting
   onto pre-flight would either share pre-flight's coarse-grained
   self-test (bad — it only checks tool references, not the freshness
   logic) or need a new self-test path inside pre-flight (worst of both
   worlds).
3. **Extensibility**: Future pre-launch checks (e.g. "worktree is on a
   branch, not detached HEAD", "no untracked files in the worktree that
   would leak into the sub-agent's context") land naturally as new
   checks inside `launch-agent.sh`. Bolting them onto pre-flight would
   sprawl.

The norm-driven-invocation cost (orchestrator must remember to call it)
is real but bounded: the orchestrator is a single decision point and
already calls `create-pr.sh` religiously. Adding one more call before
each sub-agent launch is a discipline delta of `+1 line per launch`,
paid by a HUMAN or an ORCHESTRATOR agent — not by every sub-agent
scaffolded onto a stale base and then rebased mid-flight.

## Implementation notes

- `check_base_fresh` uses `git merge-base --is-ancestor origin/main HEAD`
  after a fetch — same primitive `pr-preflight.sh` uses for check #1. If
  origin/main is an ancestor of local main, local is at or ahead of
  remote (safe). Otherwise: BLOCKED with an exact fix command.
- `--quiet` prints ONLY the `origin/main` SHA on stdout for scripting
  (`base_sha=$(bash scripts/dev/launch-agent.sh --quiet) || exit 1`).
- Self-test uses a `GIT_CMD` shell-stub that simulates fresh/stale/
  ahead/offline/norefs states without touching a real remote — no
  network calls, no ref manipulation, hermetic.
- Pre-commit hook wired identically to `create-pr-self-test`: fires on
  any change to `scripts/dev/launch-agent.sh`.

## Consequences

**What this locks in**:

- Orchestrator MUST call `bash scripts/dev/launch-agent.sh` before each
  worktree-isolated sub-agent launch.
- The `origin/main` SHA printed on success is the base to pin the
  sub-agent against in the launch prompt.
- Any change to `launch-agent.sh` re-runs its self-test on commit.

**What this forecloses**:

- We are NOT adding a post-checkout git hook. If future data shows the
  orchestrator forgets the wrapper call regularly, revisit — but the
  first response would be to add a REMINDER (e.g. a bashrc alias or a
  Claude Code hook via `~/.claude/settings.json`), not switch to git
  hooks.
- We are NOT extending `pr-preflight.sh` with pre-launch duties. That
  script remains scoped to pre-push.

**What we'll revisit**:

- After Wave 7 or 8, count how often the orchestrator SKIPS the launch
  wrapper call. If drift returns, the fix is a Claude Code
  `PreToolUse` hook (settings.json) that runs the wrapper before any
  Agent tool invocation. That would enforce discipline at the harness
  level, closing even the norm-driven-invocation gap.
- If sub-agent launch prompts start pinning the wrong base (e.g. copy-
  pasting an old SHA), add a follow-up check that reads the sub-agent
  prompt for `base-sha:` and cross-verifies against the current
  `origin/main`.
