# Change: console-vps

**SDD change**: `console-vps`
**Date**: 2026-10-04
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/console-vps/change`.

## Summary

`hermes --vps <console command>` runs a read-only console command on the VPS
over `ssh -t` and shows it in the local terminal, so the operator needs no ssh
incantation. The flag is a leading global option like `--demo`, accepted only
for console commands. The main risk is mapping ssh failures to useful
one-line errors without capturing the TTY.

## Scope

### In

- `src/hermes/entrypoints/vps.py`: settings, ssh argv builder, failure mapping.
- `cli.py`: leading `--vps` dispatch and help text.
- `deploy/hermes-console.sh` wrapper, its install in `install_release.sh` and the CD staging line.
- `--demo` after the command; `HERMES_DB_PATH` / `HERMES_CONFIG_DIR` defaults.
- Docs section in `docs/guides/vps-operations.md`.

### Out

- Interactive host-key acceptance, jump hosts, a local config file (env only).
- Any non-console command on the VPS.

## Decisions

### D-1: Console commands only, taken from the registry

- **Decision**: `--vps` accepts exactly `console_commands()` (`wants_demo`); anything else exits 2 before ssh starts.
- **Alternatives**: an allow-list in the new module (rejected: the #525 conflict hotspot).
- **Rationale**: read-only by construction; `serve` and `migrate` can never run.
- **Cost of reversal**: LOW.

### D-2: Remote wrapper is the documented `hermes_run`

- **Decision**: remote runs `sudo bash -c 'set -a; . /etc/hermes/env.production; set +a; cd /var/lib/hermes && exec runuser -u hermes -- "$@"' _ <bin> <cmd> <args>`, built with `shlex.join`.
- **Alternatives**: bare `sudo -u hermes <bin>` (rejected: cwd is the login dir, so the default `./hermes.sqlite3` and the service env are missing).
- **Rationale**: same user, env and cwd as the service, per vps-operations.md.
- **Cost of reversal**: LOW.

### D-2b: Service paths come from the environment

- **Decision**: console commands default `--db` / `--config-dir` from `HERMES_DB_PATH` / `HERMES_CONFIG_DIR` (new; no existing variable named these paths). The `--vps` remote script and the VPS wrapper export them, so no flags are forwarded.
- **Alternatives**: forwarding `--db` / `--config-dir` from `--vps` (rejected: bare `hermes status` on the VPS would still need them).
- **Cost of reversal**: LOW.

### D-2c: VPS wrapper

- **Decision**: `deploy/hermes-console.sh` is installed as `/usr/local/bin/hermes` by `install_release.sh` (prod only, optional if the stage lacks it). It allows the console commands (listed in the script; a unit test pins the list to the registry) and `--help`, refusing the rest with exit 2.
- **Cost of reversal**: LOW.

### D-2d: A VPS build without the command exits 127

- **Decision**: the remote script first checks that the release's `--help` lists the command and otherwise exits 127, which the local side maps to the "needs a deploy" message. The remote's own text travels on the TTY, which stays attached so the passphrase prompt and the TUI work.
- **Cost of reversal**: LOW.

### D-3: `--vps` with `--demo` is refused

- **Decision**: exit 2 in either order. `--demo` itself is accepted before or after a console command (once).
- **Alternatives**: forward `--demo` to the VPS (rejected: fixture data needs no VPS).
- **Cost of reversal**: LOW.

### D-4: Settings from env only

- **Decision**: `HERMES_VPS_HOST` / `HERMES_VPS_USER` / `HERMES_VPS_KEY`, defaults `hermes-01`, `root`, `~/.ssh/hermes_vps_ed25519`; host and user must be plain names, the key must exist.
- **Rationale**: no secret in the repo; a missing key file would otherwise fall into an ssh password prompt.
- **Cost of reversal**: LOW.

### D-5: Failures are mapped from the exit code and ssh's own stderr

- **Decision**: only ssh's stderr is captured (stdin/stdout stay on the TTY). 255 plus an auth marker, 255 plus a connection marker, other 255 (last stderr line), 127 (deploy needed), `FileNotFoundError` (no ssh).
- **Cost of reversal**: LOW.

## Requirements

### R-1: read-only

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_vps.py -k refused` passes.

### R-2: exact argv and failure mapping

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_vps.py` passes.

## Design

`build_ssh_argv(settings, command, args)` returns `["ssh","-t","-i",key,"-o","ConnectTimeout=10","user@host", "sudo <shlex.join(...)>"]`. `run_vps` takes an injectable `runner` seam (`subprocess.run` by default). No local shell is involved, so it works from PowerShell with the built-in OpenSSH.

## Test plan

1. `uv run pytest tests/unit/entrypoints/test_vps.py`; no test opens a connection.
2. `bash scripts/dev/pr-preflight.sh` green.
3. Manual: `hermes --vps status` from the operator's PC.

## Risks

- Remote output encoding on Windows terminals is the terminal's, not ours.
- Host-key changes surface as the generic 255 message with ssh's last line.
