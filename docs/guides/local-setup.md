# Local Setup

**Type**: Guide (living document — PR any change)
**Owners**: Ricardo Afonso (product owner, sole reviewer)
**Applies to**: everyone (and every AI agent) cloning `github.com/ricardoafo/hermes`
for local dev work.
**Description**: Clone → install → run tests → open your first PR. One-time setup steps you do once per clone, then never again.

This guide is opinionated on purpose. Hermes handles real capital and the
per-clone setup enforces discipline that CI depends on. Skip a step, hit a
recurring trap. The traps documented here have all bitten us at least once
in the past; each step below is a scar.

## Prerequisites

| Tool                | Version              | Install                                                            |
| ------------------- | -------------------- | ------------------------------------------------------------------ |
| Python              | 3.14 (via `uv`)      | `uv python install 3.14`                                           |
| `uv`                | 0.12.5 pinned        | <https://github.com/astral-sh/uv> — standalone installer preferred |
| `gh` CLI            | 2.x                  | <https://cli.github.com>                                           |
| `pre-commit`        | 3.8+ (via `uv sync`) | Auto-installed by `uv sync --frozen` (dev dep)                     |
| `markdownlint-cli2` | 0.23.2 (via npm)     | `npm install -g markdownlint-cli2@0.23.2` (for local pre-commit)   |
| Docker Desktop      | latest               | Only if you plan to run the self-hosted CI runner locally          |

You do NOT need a system-wide Python — `uv` provisions Python 3.14 into
`.venv/`. Do not `apt install python3.14`. Do not `pyenv` it. `uv` owns
Python for this repo.

## First clone

```bash
git clone https://github.com/ricardoafo/hermes.git
cd hermes

# Bootstrap the venv + install every dev dep + generate uv.lock-consistent env
uv sync --frozen
```

`--frozen` refuses to update the lock. If it errors with "no lockfile
found", you're on the wrong branch. `uv.lock` is committed on `main` per
ADR-010.

## Git config (one-time, per clone)

Two settings you MUST apply on every fresh clone. Both prevent recurring
traps that have cost dev-time in the past.

```bash
# 1. Auto-set upstream on first push of a new branch.
# Without this, `git push` on a new branch fails with an obscure
# "set-upstream" hint, and if you don't notice the hint, subsequent
# `git checkout main + git branch -D` deletes the local branch WITH
# UNPUSHED COMMITS. Reflog can recover, but only if you notice.
git config --global push.autoSetupRemote true

# 2. Bot identity — this repo requires commits from `rafo-claude-bot`
# (see AGENTS.md rule 13). If your global config sets a different
# user.name, the local override wins. Set once per clone.
git config --local user.name "rafo-claude-bot"
git config --local user.email "279133050+rafo-claude-bot@users.noreply.github.com"
```

Verify:

```bash
git config --local --get user.name       # rafo-claude-bot
git config --global --get push.autoSetupRemote  # true
```

## Pre-commit hooks (one-time, per clone)

Two hook stages need installing separately. Skipping either means CI
catches issues your local should have.

```bash
# 1. Commit-stage hooks (ruff / mypy / markdownlint / conventional-commits / etc.)
pre-commit install

# 2. Pre-push hook — runs the full CI-mirror suite before every push
#    (mirrors what `scripts/dev/pr-preflight.sh` does).
pre-commit install --hook-type pre-push
```

Verify:

```bash
ls -la .git/hooks/pre-commit .git/hooks/pre-push
# both should exist and be executable
```

**Do not skip the pre-push hook**. The whole process-hardening story
depends on it. If you push without it, you'll ship broken code to CI and
waste round-trips.

## First test run

```bash
# Full quality gate — mirrors what CI runs
bash scripts/dev/pr-preflight.sh
```

Expected output ends with:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ ALL 9 CHECKS PASS — safe to push.
```

If any check fails on a fresh clone with no local changes, something is
wrong with your environment. Report as a bug — this should be reliably
green on `main`.

## First PR flow

Per `docs/guides/dev-workflow.md` step 6 and the **project-tracker
hygiene** discipline in [AGENTS.md § Behavioral defaults](../../AGENTS.md#on-project-tracker-hygiene)
(file issue first, close on merge via `Closes #N`, comment on deferrals,
close superseded, audit each session):

```bash
# Every change is issue-first
gh issue create --repo ricardoafo/hermes --title "..." --label "type:..." --body "..."

# Branch
git checkout -b <prefix>/<kebab-case-name>

# Work + commit — pre-commit hooks run automatically
git add ...
git commit -m "<type>(<scope>): <lowercase subject>"

# Push — pre-push hook runs pr-preflight (full CI mirror)
git push

# Delete local branch immediately (per dev-workflow.md § Golden rules)
git checkout main
git branch -D <branch>

# Open PR — for SDD-tracked changes, use the render-body flow
gh pr create --head <branch> --base main --title "..." --body "..."
```

For SDD-tracked changes (per ADR-009 §6):

```bash
# Renders PR body from openspec/changes/<name>/change.md + issue.md
bash scripts/dev/pr-preflight.sh --render-body <change-name>
# Then use --body-file with the rendered temp file
gh pr create --head <branch> --base main --title "..." --body-file <tmp>
```

## Self-hosted CI runner (optional, only if you're the maintainer)

If you also operate the self-hosted GH Actions runner for this repo:

```bash
cd scripts/dev/gh-runner
cp .env.example .env
# edit .env — paste your PAT into HERMES_RUNNER_REGISTRATION_PAT
./up.sh   # spins 8 replicas by default
docker compose ps  # verify all 8 Up
```

Full runner docs in `scripts/dev/gh-runner/README.md`.

## Traps this guide protects against

Each of these bit the project at least once. The setup steps above make
them impossible for future clones.

- **`push -u` trap**: git 2.x requires `--set-upstream` on first push of a
  new branch. Without `push.autoSetupRemote = true`, push fails silently.
  If you then delete the local branch, you lose the commits. Reflog
  recovery works but isn't automatic. → Fixed by `git config --global
  push.autoSetupRemote true`.
- **Wrong git author on commits**: `require_last_push_approval` on the
  branch protection ruleset blocks merges if any commit isn't authored by
  `rafo-claude-bot`. The bot's global config might not be set. → Fixed by
  `git config --local user.name/user.email` per clone.
- **Push without pre-push hook**: bypasses the CI mirror. Ships broken
  code to CI, wastes round-trips. → Fixed by `pre-commit install
  --hook-type pre-push`.
- **PR subject starts uppercase**: `PR title` CI check enforces lowercase
  after the type prefix. `feat(fifo): Add lot log` fails; `feat(fifo):
  add lot log` passes. → Fixed by discipline; the pre-commit
  `conventional-commit` hook catches it at commit time.
- **Missing mandatory PR body sections**: `PR body` CI check requires
  Summary / Related / Testing / Risk sections. → Fixed by using
  `scripts/dev/pr-preflight.sh --body <file>` before `gh pr create`, or
  `--render-body <change-name>` for SDD changes.
