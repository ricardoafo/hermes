# Contract-test tier — separate from unit + integration

**Date**: 2026-08-23
**Status**: Accepted
**Owners**: Code Owner (product owner, sole reviewer)

## Context

Until now the hermes test suite had two tiers:

- **Unit** (`tests/unit/`): mocked wires (respx / hand-rolled fakes),
  fast, hermetic, runs on every PR.
- **Integration** (`tests/integration/`, marker `integration`): higher-fidelity
  paths — SQLite `:memory:`, Binance testnet, full flows through the domain.

Both tiers assert OUR code's behaviour. Neither answers a different, equally
important question: **is the third party still responding the way our mock
assumes it does?**

When PR #145 shipped the Telegram templates + preview foundation, every
Telegram-adapter test was mocked. If Telegram's `getMe` response shape drifted
(say they renamed `is_bot` → `bot`), our tests would stay green while the
production wire path silently broke.

## Decision

Introduce a third tier — **contract** — under `tests/contract/`, with a
dedicated `contract` pytest marker.

Contract tests:

- Make **real HTTP calls** against the third-party sandbox / read-only
  endpoint (Telegram `getMe`, Slack `auth.test`, Frankfurter `/latest`,
  Healthchecks `/ping/.../log`, Sentry `client_report`).
- **Skip when credentials are absent** (`pytest.skip` — safe in CI without
  secrets, safe on a fresh clone).
- Assert the **response shape our unit mocks assume** — not full business
  behaviour. E.g. for Telegram: `is_bot: True` and `username: str` are
  present. Nothing more.
- Never mutate remote state. `getMe`, `auth.test`, `/latest` — all
  read-only. Never `sendMessage`, never post an alert, never write a ledger.

## The three tiers

| Tier        | Directory            | Marker        | Runs when                                | Runs where                | Real wires?                              |
| ----------- | -------------------- | ------------- | ---------------------------------------- | ------------------------- | ---------------------------------------- |
| Unit        | `tests/unit/`        | (none)        | Every PR                                 | Every PR + local pre-push | No (respx, in-memory)                    |
| Contract    | `tests/contract/`    | `contract`    | Nightly + on-demand                      | Scheduled CI job          | Yes (read-only)                          |
| Integration | `tests/integration/` | `integration` | Every PR (fast subset) + nightly (full)  | CI + local                | Partial (SQLite in-mem, Binance testnet) |

## Default filter

`pyproject.toml` `[tool.pytest.ini_options]` sets `addopts = "... -m 'not
contract'"` so `pytest` in the default invocation SKIPS contract tests. This
keeps:

- `bash scripts/dev/pr-preflight.sh` fast.
- The per-PR CI unit job free of external network flakes.
- Local `pytest` runs green on a fresh clone with no `.env.local`.

Explicit run: `uv run pytest -m contract`. Nightly CI job (PR E scope) will
invoke this on a scheduled cron with the sandbox credentials mounted from
GH Actions secrets.

## Why not "just add a bigger integration tier"?

Integration tests run per-PR and assert business behaviour. Adding real HTTP
to that tier would:

- Make every PR depend on Telegram/Slack API availability. One 5xx from a
  vendor and the PR merge queue stalls.
- Force every developer to have every third-party credential to run tests
  locally — hostile to first-clone experience.
- Blur the "did our code break?" vs "did the third party change?" signal.

Separating the tiers keeps each one green for its own reason.

## Consequences

- **Positive**: upstream drift caught nightly, before it silently ships to
  prod. Unit-mock assumptions get a periodic sanity check.
- **Positive**: pattern documented once, followed by every adapter PR (D
  Slack, G Healthchecks, future exchange contract checks).
- **Positive**: fresh clones + PR runs unaffected by third-party outages.
- **Negative**: two more places to look when tracing a broken wire path.
  Mitigated by the tier table above and the `contract` marker being
  discoverable via `pytest --markers`.
- **Negative**: contract tests can fail because of upstream outage rather
  than code drift. Mitigated by (a) skip-on-missing-creds so the failure
  only happens in the scheduled CI job with credentials, and (b) grouping
  every contract failure into the nightly incident channel — not a per-PR
  block.

## First test (this PR)

`tests/contract/test_telegram_getme.py` — the pattern.

- Reads `HERMES_TELEGRAM__BOT_TOKEN` from env; `pytest.skip` if absent.
- Calls `TelegramAdapter.get_me()` (which hits `https://api.telegram.org/bot<TOKEN>/getMe`).
- Asserts `result["is_bot"] is True` and `isinstance(result["username"], str)`.
- Never sends a message.

PRs D/E/G will follow the same shape — one `test_<vendor>_<endpoint>.py`
file per contract, one `@pytest.mark.contract` test per file, skip on
missing creds.

## References

- `pyproject.toml` — `[tool.pytest.ini_options]` marker + default filter.
- `tests/contract/test_telegram_getme.py` — reference implementation.
- ADR-007 — testing strategy (this tier extends the three-tier baseline).
- `docs/decisions/2026-08-23-slack-alongside-telegram.md` — the Telegram
  adapter this contract covers.
