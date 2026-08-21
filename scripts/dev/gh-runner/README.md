# GitHub Actions self-hosted runner (Docker, on-demand)

On-demand Docker runner for hermes CI. Ricardo brings it up before working
with Claude, brings it down when done. Avoids consuming GitHub-hosted
minutes on a private repo. Generic naming (`gh-runner`) so the same
container image / compose pattern can serve any repo — one image, many
containers, each targeting a different repo via env vars.

## Prerequisites

- Docker Desktop (Windows / macOS) or Docker Engine (Linux) running
- One of:
  - `gh` CLI authenticated as a user with Administration: Read+write on
    the repo (**recommended** — `up.sh` fetches a fresh registration
    token per compose-up; nothing stored on disk)
  - A fine-grained GitHub PAT with Administration: Read+write, exported
    as `RUNNER_REGISTRATION_PAT` in your shell (for 24/7 loops)

## First-time setup

Set the repo variable that flips workflows onto this runner:

```bash
gh variable set RUNNER_LABEL --repo ricardoafo/hermes \
    --body 'self-hosted,linux,x64,hermes'
```

When you want to go back to GitHub-hosted `ubuntu-latest` (minutes reset,
plan upgrade, etc.):

```bash
gh variable delete RUNNER_LABEL --repo ricardoafo/hermes
```

That's it. No `.env` file needed for the default on-demand flow.

## Daily operation

```bash
cd scripts/dev/gh-runner

./up.sh              # one-shot: fetch token, run ONE job, exit
./up.sh --loop       # polling loop: fresh token per iteration, Ctrl-C to stop

./down.sh            # stop cleanly
./down.sh --prune    # also wipe cache volumes (cold-start next up)
```

First `./up.sh` builds the image (~3-5 min). Subsequent builds cached.
Rebuilds happen automatically when the `Dockerfile` is newer than the
built image.

## What runs where

- **`runs-on: ${{ vars.RUNNER_LABEL && fromJSON(...) || 'ubuntu-latest' }}`** —
  every job in `.github/workflows/ci.yml` and `merge-queue.yml` uses this
  pattern. `RUNNER_LABEL` set → this runner; unset → `ubuntu-latest`.
- **Image (`gh-runner:latest`) is pre-baked for speed**. Contents:
  - Ubuntu 24.04 base
  - Python 3.14 (via `uv python install`)
  - uv 0.12.5 (pinned)
  - Node 24 (nodesource) + markdownlint-cli2 0.23.2 (pinned, `npm -g`)
  - gitleaks 8.21.2 (pinned binary — avoids Docker pull on every scan)
  - git, curl, jq, Docker CLI (socket-mounted for container-based actions)
- **Warm cache volumes persist across `--force-recreate`**:
  - `uv-cache`, `npm-cache`, `pip-cache` — dependency downloads
  - `pytest-cache`, `mypy-cache`, `ruff-cache` — Phase 0+ incremental
    caches (huge speed win on re-runs)
  - `hypothesis-db` — shrinking database (also correctness — reused
    shrinks avoid re-discovering minimal failing examples)
  - `actions-cache` — downloaded actions (checkout, setup-uv, etc.)
- **Container name (`gh-runner-hermes`)** — project-identifiable if you
  run multiple runners for different repos on the same host.
- **Rebuild triggers**: bump ARG values in `Dockerfile` → `./up.sh`
  detects and rebuilds automatically.

## Auth flows

### Default: `gh api` fetches per-session token (recommended)

`up.sh` calls `gh api POST /repos/{owner}/{repo}/actions/runners/registration-token`
right before compose-up, exports `RUNNER_TOKEN` in shell, and hands it to
compose. Token is 1-hour TTL, never touches disk. In `--loop` mode,
each iteration fetches a fresh token.

Prerequisite: `gh auth status` shows an account with Administration:
Read+write on the target repo.

### Alternative: long-lived PAT (for 24/7 loops)

If you want to run 24/7 without re-fetching every hour:

```bash
export RUNNER_REGISTRATION_PAT=github_pat_your_token_here
./up.sh --loop
```

The container's entrypoint uses the PAT to fetch fresh tokens for
each registration + deregistration. The PAT stays in your shell env,
not on disk. If you must persist it, put it in an `.env` file (it
IS gitignored by the repo root `.gitignore` — verify before you
write anything).

## Security notes

- **Docker socket mounted from host** — the runner effectively has
  root-equivalent on your machine via `docker`. Acceptable for
  solo-dev + private repo + trusted PRs only. Do NOT open the repo
  to public contribution while using this runner.
- **Tokens never persist on disk in the default flow** — `gh` CLI
  handles auth; `up.sh` mints per-session tokens.
- **`--ephemeral` runner** — each container processes exactly one
  job then exits. No state accumulation beyond the cache volumes.
- **`--replace` on registration** — duplicate names self-clean if
  a previous run didn't deregister (crash, force-kill).
- **Manual cleanup** if a runner appears stuck offline in GH:

    ```bash
    gh api /repos/ricardoafo/hermes/actions/runners --jq '.runners[]'
    gh api -X DELETE /repos/ricardoafo/hermes/actions/runners/<id>
    ```

## Troubleshooting

- **`gh api` fetch fails** → check `gh auth status`, then verify the
  account has Administration: Read+write on the repo (owner accounts
  have it implicitly).
- **Registration token expired mid-loop** → tokens are 1h; `--loop`
  mode fetches fresh per iteration. If a job takes >1h and the runner
  restarts mid-flight, next iteration fetches a fresh token.
- **Image build fails on `apt-get`** → transient network; re-run
  `docker compose build`.
- **`Cannot connect to Docker daemon`** → Docker Desktop not running.
- **Job hangs on socket operations** → `/var/run/docker.sock` group
  permissions differ from image assumption (GID 999). Adjust
  `groupadd -g` in Dockerfile to match host `stat -c '%g' /var/run/docker.sock`.

## Related

- `.github/workflows/ci.yml` — the workflows this runner serves
- `.github/workflows/merge-queue.yml` — same, for the merge queue
- [`docs/guides/ai-development-lifecycle.md`](../../../docs/guides/ai-development-lifecycle.md)
  — where this runner fits in the end-to-end flow
