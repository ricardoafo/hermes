# Change: 177-runner-health

**SDD change**: `177-runner-health`
**Date**: 2026-10-04
**Status**: Proposed
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/177-runner-health/change`.

## Summary

`hermes runners` reads the self-hosted runner list from the GitHub Actions
API and prints one row per runner label (online/total, busy, status) plus an
overall status. The runners are Docker containers on the operator's Workbench
machine, not on the VPS, and they are ephemeral: each re-registers after
every job, so one runner is briefly offline between jobs and that is normal.
The classifier therefore judges a label group by how many of its runners are
online, never by a single offline runner. Main risk: the runners API needs a
token the bot does not have, so the command is operator-run.

## Scope

### In

- `hermes runners [--json]`, implemented in `src/hermes/entrypoints/runners.py`
  with a thin `commands/runners.py`.
- A pure classifier over the runner list, grouped by label (`hermes`,
  `hermes-ops`, `dependabot`).
- Operator docs in `scripts/dev/gh-runner/README.md`.

### Out

- Per-runner Healthchecks heartbeat: needs a change to the runner image on
  the operator's machine. Follow-up.
- systemd recovery: the runners are Docker with `restart: unless-stopped`.

## Decisions

### D-1: Standalone command, not a `hermes health` registry entry

- **Decision**: `hermes runners` is its own command; `_health.py` is untouched.
- **Alternatives**: `hermes health runners` (the issue's wording): rejected.
- **Rationale**: `hermes health` (all) runs on the VPS health timer, where no
  GitHub token exists. A new registry row would report NOT_CONFIGURED there
  and change its exit code.
- **Cost of reversal**: LOW.

### D-2: Judge by online share per label, not by offline duration

- **Decision**: a group is FAIL with zero online, DEGRADED with fewer than half
  online, else OK. Busy counts as online. `hermes` is required; `hermes-ops`
  and `dependabot` are judged only when present (they are planned).
- **Alternatives**: the issue's "offline for more than 5 minutes": rejected,
  the API reports no offline duration.
- **Rationale**: ephemeral runners are offline between jobs by design.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Classification

- **A-1.1**: every runner online or busy in a group: OK.
- **A-1.2**: a group with fewer than half its runners online: DEGRADED (exit 0).
- **A-1.3**: a group with runners and none online, or no `hermes` runner at
  all: FAIL (exit 1).
- **A-1.4**: adding an online runner to a group never worsens its status.

### R-2: Access errors

- **A-2.1**: no `HERMES_GITHUB_TOKEN`: exit 3, message names the variable.
- **A-2.2**: HTTP 401/403: exit 1, message names the Administration read scope.

### R-3: Output

- **A-3.1**: a table with online/total, busy and status per label, and an
  overall line.
- **A-3.2**: `--json` prints the same data as one JSON object.

## Design

- `HERMES_GITHUB_REPO` defaults to `ricardoafo/hermes`.
- `GET /repos/{repo}/actions/runners?per_page=100`, 10 s timeout, bearer token.
- `classify(runners) -> Report` is pure; `fetch_runners` is the only I/O.
- Exit codes reuse `EXIT_OK`, `EXIT_FAIL`, `EXIT_NOT_CONFIGURED` from `_health.py`.

## Test plan

1. Unit tests for OK, DEGRADED, FAIL, zero runners, missing token, 403, `--json`,
   with `respx` for the HTTP calls.
2. Hypothesis property on the classifier.
3. `bash scripts/dev/pr-preflight.sh` passes.

Test tiers touched: unit, property

## Risks

- **Token scope**: the runners API needs a token with repository
  Administration read; the bot token gets 403. The message says so.
- **Pagination**: `per_page=100` covers the planned runner count (about 10).
