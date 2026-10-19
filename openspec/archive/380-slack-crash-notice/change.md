# Change: 380-slack-crash-notice

**SDD change**: `380-slack-crash-notice`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/380-slack-crash-notice/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

`hermes.service` gets an `ExecStopPost=` that posts one Slack line whenever
the service stops without a clean result: a crash, a signal or a timeout. That
includes the crashes `Restart=on-failure` recovers from in 30 s, which never
reach `OnFailure=` and so were silent outside Sentry. The script is bash and
curl, root-owned in `/opt/hermes/bin` like the #364 failure alert, and always
exits 0. Main risk: it runs as root (the `+` prefix) to read the journal.

## Scope

### In

- `deploy/hermes-notify-crash.sh` and the `ExecStopPost=` line.
- Install to `/opt/hermes/bin` from `vps_bootstrap.sh` and, after a passing
  health gate, from `install_release.sh`; the CD bundle and the bootstrap
  workflow ship it.
- README alerting table.

### Out

- Telegram: "down" stays the `OnFailure=` alert's job (ADR-008 §4 keeps
  same-day notices on Slack).
- The daily alive line (#373).

## Decisions

### D-1: `ExecStopPost=` on hermes.service

systemd runs it after every stop and passes `$SERVICE_RESULT`, `$EXIT_CODE`
and `$EXIT_STATUS`. `success` means a clean stop (a deploy's `systemctl
restart`, SIGTERM handled by serve), so it sends nothing; any other result
sends one line. No extra unit or timer is needed.

### D-2: `-+` prefix

`+` runs the script as root outside the sandbox so it can read the journal for
the last `serve.run.failed` line; giving the engine itself
`SupplementaryGroups=systemd-journal` would let it read every service's logs.
`-` and the script's unconditional exit 0 keep a failed notice from changing
the stop result or the restart.

### D-3: outside the release

Same as #364: a broken release is a likely cause of the crash, so the script
lives in `/opt/hermes/bin`, refreshed only after a release passes its gate.

### D-4: Slack's `ok:false`

Slack answers HTTP 200 with `"ok":false` on API errors (bad channel, revoked
token), so the script checks the body and logs the refusal to the journal.

## Requirements

### R-1: clean stops are silent

`SERVICE_RESULT=success` sends nothing.

### R-2: one line per unclean stop

Unit, host, result, exit code or signal, and the restart hint; the last
`serve.run.failed` line when the journal has one.

### R-3: secrets stay out

Secret-looking env values are masked in the journal line; the bot token goes
to curl through a config on stdin, never argv.

### R-4: never interferes

Exit 0 on every path, including missing credentials and Slack errors.

### R-5: installed outside the release

Bootstrap and good deploys put the script in `/opt/hermes/bin`.

## Design

`hermes-notify-crash.sh` reuses the failure alert's `curl_quote` and `redact`
helpers, builds the line, posts `chat.postMessage` with a Bearer header from
a stdin config and checks `"ok":true`.

## Test plan

- `tests/unit/deploy/test_alert_scripts.py`: R-1 to R-4 against a loopback
  fake Slack (clean stop, exit code, signal, journal line redacted, token not
  in argv, `ok:false`, missing credentials).
- `tests/unit/deploy/test_systemd_units.py`: the `ExecStopPost=` line points
  at `/opt/hermes/bin` with `-+`.
- `tests/integration/deploy/test_release_scripts.py`: a good deploy installs
  it in `bin/` (R-5); `tests/unit/deploy/test_vps_bootstrap_alerts.py`: the
  workflow ships it.

## Risks

- Root execution of a short script; it is root-owned, needs only bash and
  curl, and reads only the journal and its env.
- A crash loop sends up to five lines before `OnFailure=` takes over, which
  is the intended signal.

## Dependencies on other changes

- #364 (failure alert outside the release) set the pattern and the install path.
