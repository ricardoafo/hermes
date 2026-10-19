# Change: 1187-telegram-http

**SDD change**: `1187-telegram-http`
**Date**: 2026-10-19
**Status**: Applied (part 1 of issue #1187: the Telegram and notifier half)
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/1187-telegram-http/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The urgent-alert notifier and `hermes bot` each carried a Telegram Bot API
client, and they drifted: only the notifier waited out an HTTP 429. One
transport, `hermes.adapters._shared.telegram_http.TelegramHttp`, now builds
the method URL, posts, waits out a 429 under the notifier's rules and checks
the reply; each adapter maps its error to its own public type at its edge,
so no caller or `except` changes. The bot keeps its proactive throttle on
top and gains the 429 retry. The Slack and Telegram notifiers share the one
existing correlation-tail helper, and three stray errors move under
`InfraError`. The main risk is a changed error message; the notifier's and
the bot's existing message tests pass unchanged.

## Scope

### In

- `src/hermes/adapters/_shared/telegram_http.py`: `TelegramHttp`,
  `TelegramHttpError`, `TelegramHttpFailure`, `retry_after`.
- `TelegramAdapter.notify` and `get_me`, and `TelegramBotClient.get_updates`
  and `send_message`, call through it.
- `correlation_tail` re-exported from `hermes.application.messages`; the two
  private copies in the notifier adapters are gone.
- `TelegramBotError(InfraError, RuntimeError)`,
  `JournalUnavailableError(InfraError, RuntimeError)`,
  `ScenarioError(InfraError, ValueError)`.

### Out

- The CLI boot helpers half of issue #1187 (shared CLI start-up, one exit
  code per failure): a later change under the same issue.

## Decisions

### D-1: One transport, one error, mapped at each adapter's edge

- **Decision**: the transport raises one `TelegramHttpError` that carries the
  method, the failed layer, the status, a token-free excerpt and Telegram's
  `description`; the notifier and the bot turn it into their own error with
  their own wording.
- **Alternatives**: one public error for both adapters (breaks every
  `except TelegramNotifierError` / `except TelegramBotError`); an injected
  error factory as in `adapters/_shared/wire.py` (the two adapters word the
  same failure differently, so they need the structured fields anyway).
- **Rationale**: callers and their tests stay unchanged (ADR-003 §04,
  code-conventions §2: adapters raise their own `InfraError`).
- **Cost of reversal**: LOW, two private `_call` methods.

### D-2: The URL root is per adapter

- **Decision**: `TelegramHttp.api_base` is the bot's configured root; the
  notifier passes an empty root so its client's `base_url` picks the host.
- **Alternatives**: always build an absolute URL from `TelegramConfig.api_base`
  (a test or lab client pointed at a fake by `base_url` would then reach the
  real Bot API).
- **Rationale**: no change to where either adapter sends.
- **Cost of reversal**: LOW.

### D-3: The health probe does not wait

- **Decision**: `get_me` calls with `retry_429=False`.
- **Alternatives**: retry every call (a health check could stall 30 s).
- **Rationale**: `hermes health telegram` answers at once, as before.
- **Cost of reversal**: LOW.

### D-4: Re-parent with multiple inheritance

- **Decision**: the three errors keep their old base as a second base.
- **Alternatives**: frozen dataclasses like `TelegramNotifierError`
  (`UnknownScenarioError` has its own `__init__` and attributes, and the
  scenario parsers' `except ValueError` re-wrap relies on the base).
- **Rationale**: every existing raise and `except` keeps working.
- **Cost of reversal**: LOW.

## Requirements

### R-1: One transport

- **A-1.1**: `grep -c "def _retry_after\|_MAX_SEND_SEC" src/hermes/adapters/notifier/telegram_adapter.py` prints `0`.
- **A-1.2**: `grep -l "TelegramHttp(" src/hermes/adapters/notifier/telegram_adapter.py src/hermes/adapters/telegram_bot/client.py` lists both files.

### R-2: Unchanged adapter contracts

- **A-2.1**: `uv run pytest -q tests/unit/adapters/notifier tests/unit/adapters/telegram_bot` exits 0 with no change to `test_telegram_adapter.py`.

### R-3: The bot waits out a 429

- **A-3.1**: `uv run pytest -q tests/unit/adapters/telegram_bot/test_client.py -k 429` exits 0.

### R-4: One correlation-tail helper

- **A-4.1**: `grep -rn "def _correlation_tail" src` prints nothing.

### R-5: The three errors are `InfraError`s

- **A-5.1**: `uv run pytest -q tests/unit/adapters -k "infra_error"` exits 0.

## Design

```text
src/hermes/adapters/_shared/telegram_http.py   new: TelegramHttp.call(method, *, http, json, params, data, read_timeout, retry_429) -> dict
src/hermes/adapters/notifier/telegram_adapter.py   _post/get_me -> _call -> TelegramHttp, maps to TelegramNotifierError
src/hermes/adapters/telegram_bot/client.py         _call -> TelegramHttp, maps to TelegramBotError; throttle unchanged
src/hermes/adapters/notifier/slack_adapter.py      uses hermes.application.messages.correlation_tail
src/hermes/application/messages/__init__.py        re-exports correlation_tail
```

## Test plan

1. Every acceptance criterion above passes.
2. `bash scripts/dev/pr-preflight.sh` passes.
3. All CI jobs green.

Test tiers touched: unit, property

Tests came first for the transport (`tests/unit/adapters/_shared/test_telegram_http.py`
ran red on the missing module); the bot's 429 tests and the three
re-parenting tests were written after their code. `test_serve_push_budget.py` now reads
the whole-call cap from the transport, where it moved.

## Risks

- **A retried long poll is cut short**: after a 429 on `getUpdates`, the retry
  reads for at most the time left in the 30 s budget, less than Telegram's
  30 s hold, so it can time out. The bot loop already catches
  `TelegramBotError` and polls again.
- **A bot reply with a 2xx other than 200 and `ok` true** is now accepted;
  before, any status but 200 failed. Telegram answers 200 on success.

## Dependencies on other changes

- None.
