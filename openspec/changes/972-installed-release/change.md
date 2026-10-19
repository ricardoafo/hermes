# Change: 972-installed-release

**SDD change**: `972-installed-release`
**Date**: 2026-10-18
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/972-installed-release/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

Part 2 of issue #972: "A test drives an installed release through a
signal, a Telegram outage and each read-only bot command, asserting the
recorded messages." `hermes serve` and `hermes bot` run as their own
processes against the fake market (#971) and the recording notifier
fakes (#972 part 1), configured only by env variables. The fakes serve
scripted 2024 bars, and an installed release reads wall time. So the
release gets a test clock, `HERMES_TEST_CLOCK_URL`. The Code Owner chose
this option on 18 Oct. Only dev and qa may set it, and only on loopback.
The main risk is a test clock reaching staging or prod. The config
loader refuses it there by name, and it is unset in every shipped env.

## Scope

### In

- `RemoteClock` (`adapters/clock/remote.py`): `now()` reads `GET <url>`
  → `{"now": <aware ISO-8601>}`. `sleep(s)` polls until the clock has
  moved `s` on. An unreachable clock or an answer with no aware instant
  raises `RemoteClockError`, never falling back to wall time.
- `env_tier_problems`: `HERMES_TEST_CLOCK_URL` is refused outside dev and
  qa, and off loopback, by name.
- `hermes serve`: on a test clock, the composition's clock is the test
  clock, and the live klines feed sleeps on it.
  `BinanceLiveKlinesAdapter.create` takes `sleep`.
- `hermes bot`: on a test clock, the loop, the status report and the
  console read model read it.
- `validate_http_config` accepts `http://` on loopback, as the config
  models already do since part 2 of #970. Without this, the Frankfurter
  adapter refused the fake market's base at start.
- Two bot defects the new test found:
  - `/pos` and `/money` read `HERMES_DB_PATH` or `./hermes.sqlite3`
    instead of the bot's own `--db`. They now read `--db`.
  - The tax item read "tax tax estimate: not available yet". It now
    reads "tax estimate: not available yet".
- `tests/integration/lower_env/test_installed_release.py`.

### Out

- Running the same test against the built wheel in a QA job: issue #973.
- Slack, healthchecks and Sentry through the installed release: the
  issue names Telegram only.

## Decisions

### D-1: A test clock over HTTP, qa and dev only (Code Owner, 18 Oct)

- **Decision**: option A on the decision card. The alternatives were
  re-stamping the bars onto today's dates (a signal waits real hours)
  and libfaketime (a machine change on the runners).
- **Rationale**: it keeps scenarios fast, and the tier check keeps the
  setting out of staging and prod.

### D-2: The test steps the clock only when serve has finished each close

- **Decision**: the clock stands still. The test moves it to the next
  4h close plus the settle delay, waits until serve has logged every
  bar due then, and waits until serve makes no market request for
  0.3 s. Only then does it move the clock again.
- **Alternatives**: tick the clock at a multiple of wall time. A probe
  at 3600× showed false 16 s skew alarms (a few ms of wall time between
  two reads is many scripted seconds). Coarse jumps landed between
  serve's two clock reads and faked 30-minute skews and missed entries.
- **Rationale**: a standing clock reads the same on both sides, so skew
  is 0, and no close is ever late. The test passed under full CPU load
  on all 4 cores.

### D-3: Name the variable `HERMES_TEST_CLOCK_URL`, outside the section namespace

- **Decision**: no `__`, so the loader never treats it as a YAML-section
  override, and the name says it is a test seam.

## Requirements

### R-1: the test clock is refused outside dev and qa, and off loopback

- **A-1.1**: `uv run pytest tests/unit/infrastructure/config/test_endpoint_tiers.py -k "test_clock"` exits 0.

### R-2: RemoteClock reads, sleeps and fails as specified

- **A-2.1**: `uv run pytest tests/unit/adapters/clock/test_remote.py` exits 0.

### R-3: an installed release passes the signal, outage and command scenarios

- **A-3.1**: `uv run pytest tests/integration/lower_env/test_installed_release.py` exits 0.

### R-4: the bot reads its own ledger and names tax once

- **A-4.1**: `uv run pytest tests/unit/application/bot/test_replies.py -k tax_item` exits 0.

## Design

See Scope. Wiring:

- `serve._wire_on_ledger` calls `remote_clock_from_env(env_snapshot)`
  when no clock was injected, and `_live_feed` passes the test clock's
  `sleep` to the feed.
- `bot.main` builds one `clock` callable and passes it to `BotLoop`,
  `build_sources` and `resolve_read_model(clock=...)`.

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` → all checks green.

Test tiers touched: unit, integration

## Risks

- **A test clock in a real env file**: refused at load in sandbox,
  staging and prod, naming the variable and never its value.
- **A slow runner**: each step waits on serve's own log, not on a fixed
  delay. Per-step timeouts are 30 s, and the module has a 240 s cap.

## Dependencies on other changes

- #971 part 1 (PR #1169) and #972 part 1 (PR #1155), both merged.
- #970 part 2 (PR #1212, merged): loopback endpoints in qa.
