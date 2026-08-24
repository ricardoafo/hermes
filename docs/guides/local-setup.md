# Local Setup

**Type**: Guide (living document — PR any change)
**Owners**: Code Owner (product owner, sole reviewer)
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

# 2. Bot identity — this repo requires commits from the `ai-bot`
# (see AGENTS.md rule 13). The literal `user.name` / `user.email`
# values below are the underlying, operator-owned GitHub account for
# the bot — the `ai-bot` alias used in prose maps to this identity.
# If your global config sets a different user.name, the local override
# wins. Set once per clone.
git config --local user.name "rafo-claude-bot"
git config --local user.email "279133050+rafo-claude-bot@users.noreply.github.com"
```

Verify (the local `user.name` should print the operator-owned bot
account name — the project-facing alias is `ai-bot`):

```bash
git config --local --get user.name       # operator-owned bot account
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

## Configuring secrets

Hermes reads credentials (bot tokens, exchange API keys, webhook URLs)
from environment variables — never from committed YAML. For local dev,
those env vars live in a `.env.local` file at the repo root; the loader
picks them up automatically at process start.

The three-tier credential story: dotenv for local dev, GitHub Actions
secrets for CI, systemd `EnvironmentFile` on the prod VPS. See
[`docs/decisions/2026-08-23-credential-storage-layered-by-env.md`](../decisions/2026-08-23-credential-storage-layered-by-env.md)
for the full rationale.

Setup (once per clone):

```bash
# Copy the committed template — it lists every env var hermes uses.
cp .env.local.example .env.local

# Fill in the values you have. Skip integrations you have not set up
# yet — the adapter for a missing integration simply refuses to
# construct, everything else keeps working.
$EDITOR .env.local
```

`.env.local` is in `.gitignore` — do not remove that entry, do not
force-add the file. If you accidentally commit real secrets, rotate
them immediately (gitleaks scans full history at CI time).

Verify the loader can read the file:

```bash
uv run python -c "from hermes.infrastructure.config import load_config; \
                  print(load_config().env)"
# Expected: `dev` (unless you set HERMES_ENV to something else)
```

Precedence: a real shell env var wins over `.env.local`. That means
`HERMES_ENV=sandbox uv run ...` overrides whatever the file says — CI
and prod are never silently shadowed by a stray developer dotenv.

## First health check

After you populate `.env.local` with real credentials for at least one
integration, verify the wire path end-to-end BEFORE running the full
test suite. Each `hermes health <integration>` call hits a READ-ONLY
endpoint — it **never** sends a message to your channel, safe to loop
in a watchdog script.

### Telegram

```bash
uv run hermes health telegram
```

Expected output on OK:

```text
# hermes health telegram
  integration  telegram
  status       OK
  latency_ms   142.3
  detail       bot=@your_bot_username
  last_check   2026-08-23T14:22:03.412+00:00
```

Exit codes:

- `0` — OK. The token is valid, the API is reachable, `getMe` returned
  your bot's username.
- `1` — FAIL. Adapter reached the API but the API responded with an
  error (401 bad token, 429 rate-limited, etc.). The `detail` column
  tells you which.
- `3` — Not configured. `HERMES_TELEGRAM__BOT_TOKEN` (or `CHAT_ID`) is
  missing. Populate `.env.local` (see the `.env.local.example` header
  around line 27).

The probe calls the read-only `getMe` endpoint — it **never** sends a
message to your channel. Safe to run in any tier, safe to loop in a
watchdog script.

### Slack

```bash
uv run hermes health slack
```

Expected output on OK:

```text
# hermes health slack
  integration  slack
  status       OK
  latency_ms   87.4
  detail       workspace=your-workspace bot=@your_bot
  last_check   2026-08-23T14:22:03.412+00:00
```

Exit codes:

- `0` — OK. The bot token is valid, the API is reachable, `auth.test`
  returned your workspace + bot user.
- `1` — FAIL. Adapter reached the API but the API responded with an
  error (401 invalid token, 429 rate-limited, or the ubiquitous
  `{"ok": false, "error": "..."}` HTTP-200 body). The `detail` column
  tells you which — Slack's error code (e.g. `invalid_auth`,
  `missing_scope`) lands there verbatim.
- `3` — Not configured. `HERMES_SLACK__BOT_TOKEN` or
  `HERMES_SLACK__DEFAULT_CHANNEL` is missing. Populate `.env.local`
  (see the `.env.local.example` header for the Slack section).

The probe calls the read-only `auth.test` endpoint (per
<https://api.slack.com/methods/auth.test>) — no `chat.postMessage`
call from the health path, ever.

### Healthchecks

```bash
uv run hermes health healthchecks
```

Expected output on OK:

```text
# hermes health healthchecks
  integration  healthchecks
  status       OK
  latency_ms   87.4
  detail       ping accepted
  last_check   2026-08-23T14:22:03.412+00:00
```

Exit codes:

- `0` — OK. The ping URL is valid, Healthchecks.io accepted the `/start`
  ping, no dead-man's-switch alert was fired (the `/start` endpoint is
  informational — it does NOT trigger alerts on its own).
- `1` — FAIL. The adapter reached Healthchecks but got an HTTP error
  (400 on a malformed URL, 500 on an outage). The `detail` column
  carries the status code and body excerpt.
- `3` — Not configured. `HERMES_HEALTHCHECKS__PING_URL` is missing.
  Populate `.env.local` (see the `.env.local.example` header for the
  Healthchecks section — the URL is opaque, one UUID per environment,
  from your Healthchecks.io project dashboard).

The probe calls the `/start` endpoint (per
<https://healthchecks.io/docs/http_api/>) — an idempotent + informational
ping that Healthchecks uses to compute job duration. It NEVER calls
`ping_fail`, which would spam the operator's alert channels every time
they run `hermes health`.

### Sentry

```bash
uv run hermes health sentry
```

Expected output (populated `HERMES_SENTRY__DSN`):

```text
integration:  sentry
status:       OK
latency_ms:   0.0
detail:       DSN parsed host=sentry.example.com project=1 environment=dev
last_check:   2026-08-23T15:42:19+00:00
```

Exit codes:

- `0` — OK. The DSN is a well-formed Sentry URL. **No wire call is made** —
  the probe only parses the URL via `sentry_sdk.utils.Dsn`, the same parse
  the SDK itself runs at `init` time. This avoids burning the operator's
  free-tier quota (5,000 events/month) on every health check.
- `1` — FAIL. The DSN string does not parse. Copy the DSN again from
  Sentry (Settings → Client Keys).
- `3` — NOT_CONFIGURED. `HERMES_SENTRY__DSN` is empty. Populate it in
  `.env.local` (see `.env.local.example`).

Sentry initialization happens at CLI startup (before any subcommand
dispatches), so unhandled exceptions in the subcommand are shipped to
Sentry via the SDK's global excepthook. Init failures never crash the
CLI — a Sentry outage MUST NOT take down hermes.

### Full status

```bash
uv run hermes health
```

Runs every registered integration in one pass and prints ONE
consolidated table:

```text
# hermes health (all integrations)
integration   status  latency  detail                                       last_check
healthchecks  OK      87ms     ping accepted                                2026-08-23T15:42:18+00:00
sentry        OK      0.0ms    DSN parsed host=... project=1 environment=dev 2026-08-23T15:42:18+00:00
slack         OK      201ms    workspace=hermes-ws bot=@hermes_bot          2026-08-23T15:42:18+00:00
telegram      OK      142ms    bot=@hermes_alerts_bot                       2026-08-23T15:42:19+00:00
```

Aggregated exit code:

- `0` — every integration reports OK.
- `1` — any integration reports FAIL (an actual adapter outage — a
  fresh clone missing credentials will NOT produce this).
- `3` — any integration reports NOT_CONFIGURED and none report FAIL
  (partial config is a fresh-clone / one-integration-at-a-time state,
  not a system-down state — wrapper scripts should treat it distinctly
  from OK but should not page an operator).

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
  branch protection ruleset blocks merges if any commit isn't authored
  by the `ai-bot` (the operator-owned bot account). The bot's global
  config might not be set. → Fixed by `git config --local
  user.name/user.email` per clone.
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
