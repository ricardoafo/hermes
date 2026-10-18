# Change: 373-daily-alive-line

**SDD change**: `373-daily-alive-line`
**Date**: 2026-10-03
**Status**: Shipped
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/373-daily-alive-line/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Once a day at 07:00 Madrid, hermes posts one line to Slack saying it is alive:
bars processed in 24 h per interval, signals, the next 4h bar, journal error
and warning counts (naming the top events when non-zero) and the day's
restarts. A late or down verdict replaces "alive". The line comes from
`hermes status --summary`, built on the #370 status layer; a bash script and
a systemd timer gather the journal and restart count and post it. No P&L.
Depends on #370 (stacked on its branch).

## Scope

### In

- `hermes status --summary [--journal FILE|-] [--restarts N]`.
- `StatusReadPort.count_bars_since` and `StatusReport.bars_24h` (also in
  `--json`).
- `deploy/hermes-daily-line.sh`, `hermes-daily.service`, `hermes-daily.timer`.
- Bootstrap enables the timer when the env file has a Slack token; the CD
  bundle ships the script.

### Out

- The full supervisor desk (#365); Telegram (ADR-008 §4 keeps routine INFO off
  Telegram).

## Decisions

### D-1: summary in Python, gathering in bash

Wording, counts and verdict handling are pure functions over the status report
and journal lines, so they live in `hermes.entrypoints.daily_line`
with unit tests. The script only gathers what needs the host: the journal
(the service has `SupplementaryGroups=systemd-journal`), NRestarts, Slack.

### D-2: restarts as a daily difference

systemd's `NRestarts` is cumulative since the unit was loaded; the script keeps
yesterday's value in `StateDirectory=hermes-daily` and reports the increase. A
counter that went down (reboot, reload) counts from zero.

### D-3: the line goes out whatever the verdict

`hermes status` exits 1 when late or down; that day's line matters most, so
the script sends whatever the command printed.

### D-4: channel

`HERMES_DAILY_LINE__CHANNEL`, falling back to `HERMES_SLACK__DEFAULT_CHANNEL`.

## Requirements

### R-1: line content and order per the summary

### R-2: top events named for non-zero counts

### R-3: late or down replaces "alive"

### R-4: no P&L

### R-5: 07:00 Europe/Madrid, oneshot, journal access, state kept

### R-6: Slack failures (missing token, `ok:false`) fail the run

## Design

`status.py --summary` builds the report as usual, counts journal JSON lines by
level with `count_journal`, and prints `daily_line(report, counts, restarts)`.
The script pipes `journalctl --since=-24h -o cat` into it and posts the result
with the same stdin-config curl pattern as the other alert scripts.

## Test plan

- `tests/unit/entrypoints/test_daily_line.py`: exact line on a quiet day,
  top events and plurals, late and down heads, journal parsing, the CLI with
  stdin and `--restarts`, no P&L.
- `tests/unit/deploy/test_daily_line_script.py`: post content and channel,
  the `hermes` argv and stdin, restart difference and reset, late line still
  sent, token out of argv, missing token, `ok:false`.
- `tests/unit/deploy/test_systemd_units.py`: timer and service settings;
  `tests/unit/deploy/test_vps_bootstrap_alerts.py`: timer enabled only with a
  Slack token.

## Risks

- `OnCalendar` with a time zone needs systemd 235 or later (Ubuntu 22.04+ is
  fine).
- The journal window is "last 24 h" at send time, not a calendar day.

## Dependencies on other changes

- #370 (`hermes status`) — this branch is stacked on it.
