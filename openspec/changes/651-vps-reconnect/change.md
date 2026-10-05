# Change: 651-vps-reconnect

**SDD change**: `651-vps-reconnect`
**Date**: 2026-10-05
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/651-vps-reconnect/change`.

## Summary

`hermes --vps` kept one ssh session with no keepalive and no retry, so a silent
network drop froze the console and could leave the terminal in the alternate
screen. The ssh argv now carries a keepalive, a dropped connection is retried
with capped backoff until Ctrl+C, and the terminal is reset after every exit.
Main risk: retrying something that should stop, so the retry decision is limited
to exit 255 with a post-connect drop message.

## Scope

### In

- `ServerAliveInterval=15` and `ServerAliveCountMax=2` in `build_ssh_argv`.
- Reconnect loop in `run_vps` with injectable `sleep` and `runner`.
- Terminal reset written to stdout after each ssh exit, only on a TTY.

### Out

- Resuming the console's own state after reconnect (the console reloads from the ledger).

## Decisions

### D-1: Retry only on post-connect drop markers

- **Decision**: The first attempt retries only when stderr has a drop marker
  ("closed by remote host", "Broken pipe", "Timeout, server not responding",
  "Connection to X closed", "client_loop: send disconnect", "Connection reset"
  except the pre-auth "Connection reset by <ip> port"). After a drop, unreachable
  errors also retry, so a sleeping laptop keeps waiting. Auth, 127 and refusals never retry.
- **Alternatives**: retry every 255 (would loop on a bad key or a mistyped host).
- **Rationale**: those messages exist only once ssh had connected.
- **Cost of reversal**: LOW.

## Requirements

### R-1: Keepalive

- **A-1.1**: `uv run pytest tests/unit/entrypoints/test_vps.py -k keepalive`

### R-2: Reconnect and cleanup

- **A-2.1**: `uv run pytest tests/unit/entrypoints/test_vps.py -k "retried or backoff or never_retry or ctrl_c or terminal_reset"`

## Design

Backoff 2, 4, 8, 16, 30 s (capped), reset by a new drop. Ctrl+C in the wait or in
ssh returns 130. Reset sequence: `\x1b[?1049l\x1b[?25h`.

## Test plan

1. The acceptance commands above pass.
2. `bash scripts/dev/pr-preflight.sh` green.

Test tiers touched: unit

## Risks

- **Reconnect loop on a half-dead link**: the wait is capped at 30 s and Ctrl+C always stops it.
