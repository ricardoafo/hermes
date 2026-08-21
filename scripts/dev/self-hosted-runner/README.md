# Self-hosted GitHub Actions runner

On-demand Docker runner for hermes CI. Ricardo brings it up before working
with Claude, brings it down when done. Avoids consuming GitHub-hosted
minutes on a private repo.

## Prerequisites

- Docker Desktop (Windows / macOS) or Docker Engine (Linux) running
- Fine-grained GitHub PAT with Administration = Read and write on
  `ricardoafo/hermes`

## First-time setup

1. Copy `.env.example` to `.env` and paste your PAT:

    ```bash
    cp .env.example .env
    # edit .env, replace github_pat_xxx_replace_me with a real PAT
    ```

2. In GitHub, set the repo variable that tells workflows to target the
   self-hosted runner:

    ```bash
    gh variable set RUNNER_LABEL --repo ricardoafo/hermes --body 'self-hosted,linux,x64,hermes'
    ```

    Or via UI: Settings → Secrets and variables → Actions → Variables →
    New repository variable → `RUNNER_LABEL` = `self-hosted,linux,x64,hermes`.

    When GH-hosted minutes reset (or you upgrade the plan), delete the
    variable — workflows fall back to `ubuntu-latest` automatically.

## Daily operation

Before working with Claude on hermes:

```bash
./up.sh              # processes ONE job then exits
./up.sh --loop       # keeps polling; Ctrl-C to stop
```

When done:

```bash
./down.sh            # clean shutdown + deregistration
./down.sh --prune    # also clear cached volumes (fresh next up)
```

## What runs where

- **`runs-on: ${{ vars.RUNNER_LABEL || 'ubuntu-latest' }}`** — every job in
  `.github/workflows/ci.yml` and `merge-queue.yml` uses this pattern.
  When `RUNNER_LABEL` is set, jobs go to this runner. When unset, they
  target GitHub-hosted `ubuntu-latest`.
- The runner image includes: Ubuntu 24.04, git, curl, jq, Docker CLI
  (socket-mounted for gitleaks + similar actions).
- Python, uv, Node get installed per-job via `astral-sh/setup-uv` /
  `actions/setup-node` — keeps the image lean and catches version drift.

## Security notes

- **Docker socket is mounted from host** — the runner effectively has
  root-equivalent on your machine via `docker`. Acceptable for solo-dev +
  private repo + trusted PRs only. Do NOT open the repo to public
  contribution while using this runner.
- **PAT lives in .env only** — `.env` is gitignored by the repo root
  `.gitignore` (`.env` + `.env.local` patterns). Never commit.
- **Ephemeral runner** — each container processes exactly one job then
  exits. No state accumulation, no persistent workspace beyond the
  volumes mounted for cache.
- **Runner deregisters on SIGTERM / EXIT** — clean shutdown removes the
  runner from the repo's runners list. If a runner disappears without
  deregistering (crash, force-kill), remove it manually via
  `gh api -X DELETE /repos/ricardoafo/hermes/actions/runners/<id>`.

## Troubleshooting

- **Registration token fails** → check PAT has `Administration: Read and
  write` at repo level (fine-grained), not just `Actions: Read`.
- **Runner shows offline in GH** → it may have deregistered on last
  shutdown. `up.sh` re-registers on start. If duplicate names accumulate,
  they self-clean via `--replace` flag on next registration.
- **Job hangs** → runner might be waiting on docker.sock permissions.
  Verify Docker Desktop is running before `./up.sh`.
- **"Cannot connect to Docker daemon"** → Docker Desktop is not running,
  or on Linux the docker group membership needs a re-login.
